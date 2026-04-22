"""研报 PDF 页 → JPG 截图，供附件 7 表 5 references.paper_image 字段使用。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import config

OUT_DIR = config.RESULT_DIR / "refs"


def render_pdf_page(paper_path: str | Path, page: int, out_path: Optional[Path] = None) -> str:
    """渲染指定 PDF 页到 JPG，返回相对 result/ 的文件名；失败返回空串。

    Cache：若目标文件已存在则直接返回。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("[paper_image] PyMuPDF (fitz) 未安装，paper_image 将留空")
        return ""

    src = Path(paper_path)
    if not src.exists():
        return ""

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = src.stem.replace(" ", "_").replace(":", "_").replace("：", "_")
    if out_path is None:
        out_path = OUT_DIR / f"{stem}_p{page}.jpg"

    if out_path.exists():
        return _rel_to_result(out_path)

    try:
        doc = fitz.open(src)
        if page < 1 or page > len(doc):
            doc.close()
            return ""
        pg = doc[page - 1]
        pix = pg.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        pix.save(str(out_path))
        doc.close()
        return _rel_to_result(out_path)
    except Exception as e:
        print(f"[paper_image] render fail {src.name} p{page}: {e}")
        return ""


def _rel_to_result(p: Path) -> str:
    try:
        return str(p.relative_to(config.RESULT_DIR))
    except ValueError:
        return str(p)
