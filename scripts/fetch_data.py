"""拉取 B 题数据到 data/。

默认从 GitHub Release 下载 `B题-全部数据.zip`（约 1.8GB），用 GBK 名解码后落盘到 data/B题-全部数据/。
若已存在则跳过；可用 --force 重抓。
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import requests
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TARGET_DIR = DATA_DIR / "B题-全部数据"

DEFAULT_URL = "https://github.com/hsh819/rstats/releases/download/v1.0-data/B.-.zip"


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] download {url} -> {dest}")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        with open(dest, "wb") as f, tqdm(total=total, unit="B", unit_scale=True) as bar:
            for chunk in r.iter_content(1024 * 64):
                if chunk:
                    f.write(chunk)
                    bar.update(len(chunk))


def extract_gbk(zip_path: Path, out_root: Path) -> Path:
    """解压含 GBK 文件名的 zip。返回顶层目录名。"""
    out_root.mkdir(parents=True, exist_ok=True)
    top: set[str] = set()
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        for info in tqdm(infos, desc="extract"):
            try:
                name = info.filename.encode("cp437").decode("gbk")
            except Exception:
                name = info.filename
            top.add(name.split("/", 1)[0])
            target = out_root / name
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src_f, open(target, "wb") as dst_f:
                dst_f.write(src_f.read())
    if not top:
        raise RuntimeError("zip 内容为空")
    return out_root / next(iter(top))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL, help="zip 下载 URL")
    ap.add_argument("--force", action="store_true", help="即使已存在也重抓")
    args = ap.parse_args()

    if TARGET_DIR.exists() and not args.force:
        files = list(TARGET_DIR.rglob("*.pdf"))
        if files:
            print(f"[fetch] {TARGET_DIR} 已存在 ({len(files)} pdf)。--force 重抓")
            return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DATA_DIR / "B题-全部数据.zip"
    if not zip_path.exists() or args.force:
        download(args.url, zip_path)

    extracted_top = extract_gbk(zip_path, DATA_DIR)
    # 规范化为 B题-全部数据
    if extracted_top.name != "B题-全部数据":
        if TARGET_DIR.exists():
            print(f"[fetch] 目标 {TARGET_DIR} 已存在；将合并 {extracted_top}")
            for p in extracted_top.iterdir():
                p.rename(TARGET_DIR / p.name)
            extracted_top.rmdir()
        else:
            extracted_top.rename(TARGET_DIR)

    pdf_count = len(list(TARGET_DIR.rglob("*.pdf")))
    print(f"[fetch] 完成: {TARGET_DIR}（{pdf_count} pdf）")
    # 删除 zip 节省磁盘
    try:
        zip_path.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
