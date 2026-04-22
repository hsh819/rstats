"""意图识别：规则侧（主）+ LLM（回落）。

输出结构化意图字典，供 dialogue.py 决定是否需要澄清。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .. import config
from ..llm_client import get_client
from . import prompts
from .field_schema import FIELD_META, all_keywords, canonical_field


@dataclass
class Intent:
    intent: str = "query"  # query | trend | rank | compare | clarify
    companies: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)  # Q1 / HY / Q3 / FY
    fields: list[str] = field(default_factory=list)  # 中文主名（FIELD_META key）
    need_clarify: bool = False
    clarify_question: str = ""
    inherit_context: bool = False  # 本轮含代词（"该公司/本期"）时标记，由 dialogue 决定是否继承


# 报告期汉字映射
_PERIOD_ZH = {
    "一季报": "Q1", "一季度": "Q1", "第一季度": "Q1", "Q1": "Q1",
    "半年报": "HY", "中报": "HY", "半年度": "HY", "上半年": "HY", "HY": "HY",
    "三季报": "Q3", "三季度": "Q3", "第三季度": "Q3", "Q3": "Q3",
    "年报": "FY", "全年": "FY", "年度": "FY", "FY": "FY",
}

# 代词 / 回指
_PRONOUNS = ("该公司", "这家公司", "本公司", "同公司", "上一期", "上期", "本期", "同期", "该指标", "这个指标", "相同的")


# 兼容旧代码：导出 FIELD_SYNONYMS 简表（key → column）
FIELD_SYNONYMS: dict[str, str] = {k: v.column for k, v in FIELD_META.items()}


@lru_cache(maxsize=1)
def load_known_abbrs(db_path_str: str = "") -> tuple[str, ...]:
    """从 companies 表加载股票简称；失败时退化为内置样本表。"""
    db_path = Path(db_path_str) if db_path_str else config.DB_PATH
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT DISTINCT stock_abbr FROM companies WHERE stock_abbr IS NOT NULL").fetchall()
        conn.close()
        names = tuple(r[0] for r in rows if r and r[0])
        if names:
            return names
    except sqlite3.Error:
        pass
    return ("金花股份", "华润三九")


def rule_based(question: str) -> Intent:
    q = question
    intent = Intent()
    # 公司
    for a in load_known_abbrs():
        if a in q and a not in intent.companies:
            intent.companies.append(a)
    # 股票代码 3-6 位（华润三九=999，部分公司代码不足 6 位）
    for code in re.findall(r"(?<!\d)(\d{3,6})(?!\d)", q):
        # 排除年份、占比百分号
        if 1900 <= int(code) <= 2100 and len(code) == 4:
            continue
        if code not in intent.companies:
            intent.companies.append(code)
    # 年份
    for y in re.findall(r"(20\d{2})\s*年", q):
        y_i = int(y)
        if y_i not in intent.years:
            intent.years.append(y_i)
    # 报告期
    for k, v in _PERIOD_ZH.items():
        if k in q and v not in intent.periods:
            intent.periods.append(v)
    # 字段：按所有关键词匹配（包含同义词），再规范化到主名
    for kw in all_keywords():
        if kw in q:
            canon = canonical_field(kw)
            if canon and canon not in intent.fields:
                intent.fields.append(canon)

    # 代词：标记需要继承上下文
    if any(p in q for p in _PRONOUNS):
        intent.inherit_context = True

    # 意图类型
    if any(k in q for k in ["趋势", "变化", "走势", "近几年", "近三年", "近五年", "历年", "每年", "可视化", "折线", "曲线"]):
        intent.intent = "trend"
    elif any(k in q for k in ["top", "Top", "TOP", "前几", "前十", "前三", "最高", "最低", "排名", "排序"]):
        intent.intent = "rank"
    elif any(k in q for k in ["对比", "相比", "vs", "VS", "比较", "差异"]):
        intent.intent = "compare"
    else:
        intent.intent = "query"

    # 澄清逻辑（初轮需要 company + field；后轮由 dialogue 二次判断）
    if intent.intent == "query":
        if not intent.companies:
            intent.need_clarify = True
            intent.clarify_question = "请问要查询哪家公司？"
        elif not intent.fields:
            intent.need_clarify = True
            intent.clarify_question = "请问要查询哪个财务指标？"
    elif intent.intent == "trend" and not intent.companies:
        intent.need_clarify = True
        intent.clarify_question = "请问要分析哪家公司的趋势？"
    return intent


def llm_refine(question: str, base: Intent) -> Intent:
    """若规则识别信心不足或缺信息，交给 LLM 进一步判定。无 LLM 时直接返回 base。"""
    client = get_client()
    if not client.enabled:
        return base
    sys = prompts.INTENT_PROMPT
    usr = f"问题：{question}\n已有规则识别：{base.__dict__}"
    out = client.chat_json([
        {"role": "system", "content": sys},
        {"role": "user", "content": usr},
    ])
    if not out or "intent" not in out:
        return base
    ents = out.get("entities", {}) or {}
    # LLM 返回的 field 列表走 canonical_field 规范化
    fields = list(base.fields)
    for f in ents.get("field") or []:
        canon = canonical_field(str(f))
        if canon and canon not in fields:
            fields.append(canon)
    return Intent(
        intent=out.get("intent", base.intent),
        companies=list({*base.companies, *(ents.get("company") or [])}),
        years=list({*base.years, *[int(y) for y in (ents.get("year") or []) if str(y).isdigit()]}),
        periods=list({*base.periods, *(ents.get("period") or [])}),
        fields=fields,
        need_clarify=bool(out.get("need_clarify", base.need_clarify)),
        clarify_question=out.get("clarify_question", base.clarify_question) or "",
        inherit_context=base.inherit_context,
    )


def route(question: str, use_llm: bool = True) -> Intent:
    base = rule_based(question)
    if use_llm and base.need_clarify:
        return llm_refine(question, base)
    return base
