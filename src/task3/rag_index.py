"""研报 RAG 索引：PDF → chunk → TF-IDF 向量 → 倒排。

为了轻量，先用 scikit-learn TfidfVectorizer + 余弦相似度（中文字粒度 n-gram）。
如有 sentence-transformers + FAISS，可在后续替换为向量检索。
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import pdfplumber

from .. import config


@dataclass
class Chunk:
    paper_path: str
    paper_title: str
    stock_name: str
    page: int
    text: str


def _split(text: str, size: int = 480, overlap: int = 60) -> list[str]:
    text = text.strip()
    out = []
    i = 0
    while i < len(text):
        out.append(text[i : i + size])
        i += size - overlap
    return out


def extract_pdf(path: Path) -> list[tuple[int, str]]:
    """返回 [(page, text), ...]"""
    try:
        with pdfplumber.open(path) as pdf:
            return [(idx + 1, (pg.extract_text() or "")) for idx, pg in enumerate(pdf.pages)]
    except Exception as e:
        print(f"[rag] open failed {path.name}: {e}")
        return []


def build_chunks(meta_rows: list[dict], pdf_dir: Path) -> list[Chunk]:
    """基于元数据表 + PDF 目录生成 chunks。"""
    chunks: list[Chunk] = []
    for m in meta_rows:
        title = str(m.get("title", "")).strip()
        stock_name = str(m.get("stockName", m.get("industryName", ""))).strip()
        # 用标题匹配 pdf 文件
        pdf_path = None
        for p in pdf_dir.glob("*.pdf"):
            if title in p.stem or p.stem in title:
                pdf_path = p
                break
        if pdf_path is None:
            # fallback: 若只有一个 pdf
            candidates = list(pdf_dir.glob("*.pdf"))
            if len(candidates) == 1:
                pdf_path = candidates[0]
        if pdf_path is None:
            continue
        for page, txt in extract_pdf(pdf_path):
            for seg in _split(txt):
                if len(seg.strip()) < 50:
                    continue
                chunks.append(Chunk(
                    paper_path=str(pdf_path),
                    paper_title=title,
                    stock_name=stock_name,
                    page=page,
                    text=seg,
                ))
    return chunks


class Retriever:
    def __init__(self, chunks: list[Chunk]):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.chunks = chunks
        self._vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 2), max_df=0.95)
        self._mat = self._vec.fit_transform([c.text for c in chunks]) if chunks else None

    def search(self, query: str, top_k: int = 5, filter_stock: Optional[str] = None) -> list[tuple[Chunk, float]]:
        if not self.chunks or self._mat is None:
            return []
        from sklearn.metrics.pairwise import cosine_similarity

        qv = self._vec.transform([query])
        sims = cosine_similarity(qv, self._mat).ravel()
        # 排序
        order = sims.argsort()[::-1]
        out: list[tuple[Chunk, float]] = []
        for i in order:
            c = self.chunks[i]
            if filter_stock and filter_stock and filter_stock not in c.stock_name and filter_stock not in c.paper_title:
                continue
            out.append((c, float(sims[i])))
            if len(out) >= top_k:
                break
        return out


# ========== 缓存 / 构建 ==========
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


def load_or_build() -> Retriever:
    if INDEX_CACHE.exists():
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
