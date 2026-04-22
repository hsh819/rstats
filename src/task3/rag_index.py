"""研报 RAG 索引：PDF → chunk → TF-IDF 向量 → 倒排。

设计：
- 按页切块，跳过首页（封面）；
- 剔除含"免责声明 / 分析师声明 / 评级说明 / 特此声明"的 chunk；
- chunk 长度 300 字、overlap 60；
- 检索返回时加 `min_score` 阈值和 `stock_code/stock_name/paper_title` 前置命中加权；
- 在数据规模较大时（>10k chunks）自动降低 ngram 维度，控制内存。

Cache 策略：用一个稳定 hash（基于 chunk 总数 + 元数据 mtime）作为文件名后缀，
避免数据变化后误用旧 cache。
"""
from __future__ import annotations

import hashlib
import pickle
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber
from tqdm import tqdm

from .. import config


@dataclass
class Chunk:
    paper_path: str
    paper_title: str
    stock_name: str
    page: int
    text: str
    chunk_id: str = ""
    stock_code: str = ""        # 个股研报取自元数据；行业研报为空
    industry_name: str = ""     # 行业研报取自元数据；个股研报为空


_DISCLAIMER_RE = re.compile(
    r"免责声明|分析师声明|特此声明|评级说明|风险提示|联系电话|联系人|地址[：:]|"
    r"开户机构|销售商品发行|投资评级|投资机构|本报告仅供|分析师承诺|证券投资咨询业务|"
    r"研究部|目录"
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


def _index_pdfs(pdf_dir: Path) -> dict[str, Path]:
    """按 stem 建立 PDF 索引便于 O(1) 标题匹配。"""
    return {p.stem: p for p in pdf_dir.glob("*.pdf")}


def build_chunks(meta_rows: list[dict], pdf_dir: Path, kind: str = "individual") -> list[Chunk]:
    """kind ∈ {individual, industry}；individual 用 stockName/stockCode，industry 用 industryName。"""
    pdf_index = _index_pdfs(pdf_dir)
    chunks: list[Chunk] = []
    desc = "RAG-individual" if kind == "individual" else "RAG-industry"
    for m in tqdm(meta_rows, desc=desc):
        title = str(m.get("title", "")).strip()
        if not title:
            continue
        stock_name = str(m.get("stockName", "")).strip() if kind == "individual" else ""
        stock_code = str(m.get("stockCode", "")).strip() if kind == "individual" else ""
        industry_name = str(m.get("industryName", "")).strip() if kind == "industry" else ""

        # PDF 命名 = title + .pdf；先精确，再前缀
        pdf_path = pdf_index.get(title)
        if pdf_path is None:
            for stem, p in pdf_index.items():
                if title in stem or stem.startswith(title[:20]):
                    pdf_path = p
                    break
        if pdf_path is None:
            continue
        for page, txt in extract_pdf(pdf_path):
            if page == 1:
                continue
            for k, seg in enumerate(_split(txt)):
                s = seg.strip()
                if len(s) < 60 or _JUNK_RE.match(s) or _DISCLAIMER_RE.search(s):
                    continue
                chunks.append(Chunk(
                    paper_path=str(pdf_path),
                    paper_title=title,
                    stock_name=stock_name,
                    page=page,
                    text=s,
                    chunk_id=f"{pdf_path.stem}#p{page}#c{k}",
                    stock_code=str(stock_code).zfill(6) if stock_code.isdigit() else stock_code,
                    industry_name=industry_name,
                ))
    return chunks


class Retriever:
    def __init__(self, chunks: list[Chunk]):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.chunks = chunks
        # 按规模自适应：>10k chunks 降为 char ngram(2,2) + 限制词表
        if len(chunks) > 10000:
            self._vec = TfidfVectorizer(
                analyzer="char_wb", ngram_range=(2, 2),
                max_df=0.9, min_df=2, max_features=200000,
            )
        else:
            self._vec = TfidfVectorizer(
                analyzer="char_wb", ngram_range=(1, 2), max_df=0.95,
            )
        self._mat = self._vec.fit_transform([c.text for c in chunks]) if chunks else None

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_stock: Optional[str] = None,
        filter_stock_code: Optional[str] = None,
        min_score: float = 0.02,
    ) -> list[tuple[Chunk, float]]:
        if not self.chunks or self._mat is None:
            return []
        from sklearn.metrics.pairwise import cosine_similarity

        qv = self._vec.transform([query])
        sims = cosine_similarity(qv, self._mat).ravel()

        boosted: list[tuple[int, float]] = []
        for i, base in enumerate(sims):
            c = self.chunks[i]
            score = float(base)
            # 优先 code 精确加权
            if filter_stock_code and c.stock_code == filter_stock_code:
                score += 0.20
            elif filter_stock:
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
            if filter_stock_code:
                if c.stock_code == filter_stock_code:
                    out.append((c, score))
            elif filter_stock:
                if filter_stock in (c.stock_name or "") or filter_stock in (c.paper_title or ""):
                    out.append((c, score))
            else:
                out.append((c, score))
            if len(out) >= top_k:
                break
        # 严格过滤无果时降级
        if not out and (filter_stock_code or filter_stock):
            for i, score in boosted:
                if score < min_score:
                    break
                out.append((self.chunks[i], score))
                if len(out) >= top_k:
                    break
        return out


CACHE_DIR = config.ROOT / "db"


def _cache_key() -> str:
    """根据元数据文件 mtime + 路径生成 hash。"""
    h = hashlib.md5()
    for f in (config.FILE_RESEARCH_INDIVIDUAL_META, config.FILE_RESEARCH_INDUSTRY_META):
        if f.exists():
            h.update(str(f).encode())
            h.update(str(f.stat().st_mtime_ns).encode())
            h.update(str(f.stat().st_size).encode())
    return h.hexdigest()[:10]


def _cache_path() -> Path:
    return CACHE_DIR / f"rag_chunks_{_cache_key()}.pkl"


def build_index_from_disk() -> Retriever:
    import pandas as pd

    chunks: list[Chunk] = []
    for meta_file, pdf_dir, kind in (
        (config.FILE_RESEARCH_INDIVIDUAL_META, config.DIR_RESEARCH_INDIVIDUAL, "individual"),
        (config.FILE_RESEARCH_INDUSTRY_META, config.DIR_RESEARCH_INDUSTRY, "industry"),
    ):
        if not meta_file.exists() or not pdf_dir.exists():
            print(f"[rag] missing {meta_file} or {pdf_dir}, skip")
            continue
        df = pd.read_excel(meta_file)
        chunks.extend(build_chunks(df.to_dict("records"), pdf_dir, kind=kind))

    cache = _cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as f:
        pickle.dump(chunks, f)
    print(f"[rag] chunks={len(chunks)} cached -> {cache}")
    return Retriever(chunks)


def load_or_build(force: bool = False) -> Retriever:
    cache = _cache_path()
    if not force and cache.exists():
        try:
            with cache.open("rb") as f:
                chunks = pickle.load(f)
            print(f"[rag] loaded cache {cache.name} chunks={len(chunks)}")
            return Retriever(chunks)
        except Exception:
            pass
    return build_index_from_disk()


if __name__ == "__main__":
    r = load_or_build(force=False)
    hits = r.search("主营业务收入上升原因", top_k=3)
    for c, s in hits:
        print(f"{s:.3f} | {c.paper_title} p{c.page} | {c.text[:80]}")
