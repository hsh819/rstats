"""研报 RAG 索引：PDF → chunk → TF-IDF 向量 → 倒排。

设计：
- 按页切块，跳过首页（封面）；
- 剔除含"免责声明 / 分析师声明 / 评级说明 / 特此声明"的 chunk；
- chunk 长度 300 字、overlap 60；
- 检索返回时加 `min_score` 阈值和 `stock_name / paper_title` 前置命中加权。
"""
from __future__ import annotations

import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pdfplumber

from .. import config


@dataclass
class Chunk:
    paper_path: str
    paper_title: str
    stock_name: str
    page: int
    text: str
    chunk_id: str = ""


_DISCLAIMER_RE = re.compile(
    r"免责声明|分析师声明|特此声明|评级说明|风险提示|联系电话|联系人|地址[：:]|开户机构|销售商品发行|投资评级|投资机构|本报告仅供",
)
_JUNK_RE = re.compile(r"^[-·•\s\d]+$")


def _split(text: str, size: int = 300, overlap: int = 60) -> list[str]:
    text = text.strip()
    out = []
    i = 0
    while i < len(text):
        out.append(text[i : i + size])
        i += size - overlap
    return out


def extract_pdf(path: Path) -> list[tuple[int, str]]:
    try:
        with pdfplumber.open(path) as pdf:
            return [(idx + 1, (pg.extract_text() or "")) for idx, pg in enumerate(pdf.pages)]
    except Exception as e:
        print(f"[rag] open failed {path.name}: {e}")
        return []


def build_chunks(meta_rows: list[dict], pdf_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for m in meta_rows:
        title = str(m.get("title", "")).strip()
        stock_name = str(m.get("stockName", m.get("industryName", ""))).strip()
        pdf_path = None
        for p in pdf_dir.glob("*.pdf"):
            if title and (title in p.stem or p.stem in title):
                pdf_path = p
                break
        if pdf_path is None:
            candidates = list(pdf_dir.glob("*.pdf"))
            if len(candidates) == 1:
                pdf_path = candidates[0]
        if pdf_path is None:
            continue
        for page, txt in extract_pdf(pdf_path):
            if page == 1:
                # 跳过封面页
                continue
            for k, seg in enumerate(_split(txt)):
                s = seg.strip()
                if len(s) < 60:
                    continue
                if _JUNK_RE.match(s):
                    continue
                if _DISCLAIMER_RE.search(s):
                    continue
                chunks.append(Chunk(
                    paper_path=str(pdf_path),
                    paper_title=title or pdf_path.stem,
                    stock_name=stock_name,
                    page=page,
                    text=s,
                    chunk_id=f"{pdf_path.stem}#p{page}#c{k}",
                ))
    return chunks


class Retriever:
    def __init__(self, chunks: list[Chunk]):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.chunks = chunks
        self._vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 2), max_df=0.95)
        self._mat = self._vec.fit_transform([c.text for c in chunks]) if chunks else None

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_stock: Optional[str] = None,
        min_score: float = 0.02,
    ) -> list[tuple[Chunk, float]]:
        if not self.chunks or self._mat is None:
            return []
        from sklearn.metrics.pairwise import cosine_similarity

        qv = self._vec.transform([query])
        sims = cosine_similarity(qv, self._mat).ravel()

        # 前置命中加权：stock_name / paper_title 里包含 filter_stock 的 chunk 加分
        boosted: list[tuple[int, float]] = []
        for i, base in enumerate(sims):
            c = self.chunks[i]
            score = float(base)
            if filter_stock:
                if filter_stock in (c.stock_name or ""):
                    score += 0.12
                elif filter_stock in (c.paper_title or ""):
                    score += 0.06
            boosted.append((i, score))

        boosted.sort(key=lambda x: x[1], reverse=True)
        out: list[tuple[Chunk, float]] = []
        for i, score in boosted:
            if score < min_score:
                break
            c = self.chunks[i]
            if filter_stock and filter_stock not in (c.stock_name or "") and filter_stock not in (c.paper_title or ""):
                # 严格过滤：若提供了 filter_stock，跳过无关研报
                continue
            out.append((c, score))
            if len(out) >= top_k:
                break
        # 若严格过滤后无结果，降级为不过滤
        if not out and filter_stock:
            for i, score in boosted:
                if score < min_score:
                    break
                out.append((self.chunks[i], score))
                if len(out) >= top_k:
                    break
        return out


INDEX_CACHE = config.ROOT / "db" / "rag_chunks.pkl"


def build_index_from_disk() -> Retriever:
    import pandas as pd

    chunks: list[Chunk] = []
    for meta_file, pdf_dir in (
        (config.FILE_RESEARCH_INDIVIDUAL_META, config.DIR_RESEARCH_INDIVIDUAL),
        (config.FILE_RESEARCH_INDUSTRY_META, config.DIR_RESEARCH_INDUSTRY),
    ):
        if not meta_file.exists() or not pdf_dir.exists():
            continue
        df = pd.read_excel(meta_file)
        chunks.extend(build_chunks(df.to_dict("records"), pdf_dir))

    INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_CACHE.open("wb") as f:
        pickle.dump(chunks, f)
    print(f"[rag] chunks={len(chunks)} cached -> {INDEX_CACHE}")
    return Retriever(chunks)


def load_or_build(force: bool = False) -> Retriever:
    if not force and INDEX_CACHE.exists():
        try:
            with INDEX_CACHE.open("rb") as f:
                chunks = pickle.load(f)
            return Retriever(chunks)
        except Exception:
            pass
    return build_index_from_disk()


if __name__ == "__main__":
    r = build_index_from_disk()
    hits = r.search("主营业务收入上升原因", top_k=3)
    for c, s in hits:
        print(f"{s:.3f} | {c.paper_title} p{c.page} | {c.text[:80]}")
