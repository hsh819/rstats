"""归因：RAG 检索召回片段 + 组装附件 7 表 5 references。

流程：
1. `extract_keywords()` 从问题/意图里抽关键词（业务线、指标、期间、原因词）。
2. 有 LLM KEY 时用 LLM 改写检索 query（短词）；无 KEY 用规则拼接。
3. `Retriever.search` 召回 top-K。
4. 按 (paper_path, page) 去重，保留最高分。
5. 有 LLM 时再请一次 LLM 做 200 字中文归因总结；无 LLM 退化为规则串联。
6. `build_references` 输出字段严格对齐附件 7 表 5。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from ..llm_client import get_client
from . import paper_image
from .rag_index import Chunk, Retriever


_BIZ_WORDS = ("主营", "业务", "产品", "药品", "医保", "研发", "营销", "政策", "市场", "集采", "采购", "渠道")
_METRIC_HINT_WORDS = (
    "营收", "营业收入", "净利润", "毛利率", "净利率", "研发", "应收", "存货",
    "现金流", "负债", "ROE", "EPS", "利润总额",
)


def extract_keywords(question: str, intent_fields: Optional[list[str]] = None) -> list[str]:
    """从问题里提取短关键词（去重后不超过 6 个）。"""
    out: list[str] = []
    for w in _METRIC_HINT_WORDS:
        if w in question and w not in out:
            out.append(w)
    for w in _BIZ_WORDS:
        if w in question and w not in out:
            out.append(w)
    for y in re.findall(r"20\d{2}\s*年", question):
        if y not in out:
            out.append(y)
    if intent_fields:
        for f in intent_fields:
            if f and f not in out:
                out.append(f)
    if not out:
        parts = re.split(r"[，,。;；?？!！\s]+", question)
        out = [p for p in parts if 2 <= len(p) <= 12][:3]
    return out[:6]


def reformulate_query(question: str, keywords: list[str], use_llm: bool = True) -> str:
    """把长问题改写为 RAG 友好的短关键词串。"""
    if use_llm:
        client = get_client()
        if client.enabled:
            out = client.chat_json([
                {"role": "system", "content": "你是信息检索助手。把用户问题改写为 3-6 个中文核心关键词（空格分隔）。返回 JSON {\"query\": \"...\"}。"},
                {"role": "user", "content": question},
            ])
            q = (out or {}).get("query", "") if isinstance(out, dict) else ""
            if q:
                return q
    if keywords:
        return " ".join(keywords)
    return question[:60]


def build_references(hits: list[tuple[Chunk, float]], max_n: int = 3, render_image: bool = True) -> list[dict]:
    """按附件 7 表 5 references schema 组装：{paper_path,paper_title,page,text,paper_image}。

    - (paper_path, page) 去重，保留最高分
    - paper_image 用 PyMuPDF 渲染
    """
    seen: dict[tuple[str, int], tuple[Chunk, float]] = {}
    for c, score in hits:
        key = (c.paper_path, c.page)
        prev = seen.get(key)
        if prev is None or score > prev[1]:
            seen[key] = (c, score)
    ordered = sorted(seen.values(), key=lambda x: x[1], reverse=True)[:max_n]

    refs: list[dict] = []
    for c, score in ordered:
        img = paper_image.render_pdf_page(c.paper_path, c.page) if render_image else ""
        refs.append({
            "paper_path": c.paper_path,
            "paper_title": c.paper_title,
            "page": c.page,
            "score": round(score, 4),
            "text": c.text,
            "paper_image": img,
        })
    return refs


def llm_summarize(question: str, refs: list[dict]) -> str:
    client = get_client()
    if not client.enabled or not refs:
        return ""
    compact = [{"title": r["paper_title"], "page": r["page"], "text": r["text"]} for r in refs]
    out = client.chat_json([
        {"role": "system", "content": "你是财经分析助手。根据研报片段归因回答用户问题，200 字内，末尾括注引用的 (标题 p页) 。返回 JSON {\"answer\": \"...\"}。"},
        {"role": "user", "content": f"问题：{question}\n研报片段：{json.dumps(compact, ensure_ascii=False)}"},
    ])
    return (out or {}).get("answer", "") if isinstance(out, dict) else ""


def attribute(
    retriever: Retriever,
    query: str,
    filter_stock: Optional[str] = None,
    filter_stock_code: Optional[str] = None,
    intent_fields: Optional[list[str]] = None,
) -> tuple[str, list[dict]]:
    keywords = extract_keywords(query, intent_fields)
    # 短问题不调 LLM 改写
    use_llm_rewrite = len(query) > 30
    reformulated = reformulate_query(query, keywords, use_llm=use_llm_rewrite)
    hits = retriever.search(
        reformulated, top_k=8,
        filter_stock=filter_stock, filter_stock_code=filter_stock_code,
    )
    refs = build_references(hits, max_n=3)
    if not refs:
        return "未在研报中检索到相关内容", []
    summary = llm_summarize(query, refs)
    if not summary:
        summary = "；".join(f"- {r['paper_title']}（p{r['page']}）: {r['text'][:120]}" for r in refs)
    return summary, refs
