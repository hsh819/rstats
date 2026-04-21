"""归因：从 RAG 检索召回片段 + 组装附件7 表5 references。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .rag_index import Chunk, Retriever


def build_references(hits: list[tuple[Chunk, float]], max_n: int = 3) -> list[dict]:
    """附件7 表5 references schema：
       { "paper_path", "text", "paper_image" }
    paper_image 暂时留空（可用 pdfplumber page.to_image 后续补）。
    """
    refs: list[dict] = []
    for c, score in hits[:max_n]:
        refs.append({
            "paper_path": str(c.paper_path),
            "paper_title": c.paper_title,
            "page": c.page,
            "score": round(score, 4),
            "text": c.text,
            "paper_image": "",
        })
    return refs


def attribute(retriever: Retriever, query: str, filter_stock: Optional[str] = None) -> tuple[str, list[dict]]:
    hits = retriever.search(query, top_k=5, filter_stock=filter_stock)
    refs = build_references(hits, max_n=3)
    if not refs:
        return "未在研报中检索到相关内容", []
    summary = "\n".join(f"- {r['paper_title']}（p{r['page']}）: {r['text'][:120]}" for r in refs)
    return summary, refs
