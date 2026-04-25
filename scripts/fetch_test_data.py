"""拉取 B 题测试数据到 data/B题-测试数据/。

从 GitHub Release v2.0-data 下载 `B-testdata.zip`，用 GBK 名解码后落盘。
若目标目录已含 PDF 则跳过；--force 强制重抓。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 复用 fetch_data 的工具函数
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_data import download, extract_gbk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TARGET_DIR = DATA_DIR / "B题-测试数据"

DEFAULT_URL = "https://github.com/hsh819/rstats/releases/download/v2.0-data/B-testdata.zip"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL, help="zip 下载 URL")
    ap.add_argument("--force", action="store_true", help="即使已存在也重抓")
    args = ap.parse_args()

    if TARGET_DIR.exists() and not args.force:
        files = list(TARGET_DIR.rglob("*.pdf"))
        if files:
            print(f"[fetch_test] {TARGET_DIR} 已存在 ({len(files)} pdf)。--force 重抓")
            return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DATA_DIR / "B-testdata.zip"
    if not zip_path.exists() or args.force:
        download(args.url, zip_path)

    extracted_top = extract_gbk(zip_path, DATA_DIR)
    # 规范化为 B题-测试数据
    if extracted_top.name != "B题-测试数据":
        if TARGET_DIR.exists():
            print(f"[fetch_test] 目标 {TARGET_DIR} 已存在；将合并 {extracted_top}")
            for p in extracted_top.iterdir():
                p.rename(TARGET_DIR / p.name)
            extracted_top.rmdir()
        else:
            extracted_top.rename(TARGET_DIR)

    pdf_count = len(list(TARGET_DIR.rglob("*.pdf")))
    print(f"[fetch_test] 完成: {TARGET_DIR}（{pdf_count} pdf）")
    try:
        zip_path.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
