"""从 GitHub 拉取 B 题示例数据到 data/ 目录。使用 raw URL 直接下载，避免 API 限流。"""
import sys
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "B题-示例数据"
REF = "b728af3a083579c33dd59ac56ba8d7f5f02afdb0"
BASE = f"https://raw.githubusercontent.com/hsh819/rstats/{REF}/B%E9%A2%98-%E7%A4%BA%E4%BE%8B%E6%95%B0%E6%8D%AE"

TOP_FILES = [
    "附件1：中药上市公司基本信息（截至到2025年12月22日）.xlsx",
    "附件3：数据库-表名及字段说明.xlsx",
    "附件4：问题汇总.xlsx",
    "附件6：问题汇总.xlsx",
    "附件5：研报数据/个股_研报信息.xlsx",
    "附件5：研报数据/行业_研报信息.xlsx",
    "附件5：研报数据/字段说明.xlsx",
]

SHANGHAI_PDFS = [
    "600080_20230428_FQ2V.pdf",
    "600080_20230428_MMWM.pdf",
    "600080_20230428_PCK7.pdf",
    "600080_20230819_DNUE.pdf",
    "600080_20230819_U8CH.pdf",
    "600080_20231028_F42E.pdf",
    "600080_20240427_0WKP.pdf",
    "600080_20240427_IBMB.pdf",
    "600080_20240427_W39O.pdf",
    "600080_20240817_5X5X.pdf",
    "600080_20240817_6AW9.pdf",
    "600080_20241030_XN72.pdf",
    "600080_20250425_6GSD.pdf",
    "600080_20250425_LXGH.pdf",
    "600080_20250425_P7ID.pdf",
    "600080_20250822_ABVM.pdf",
    "600080_20250822_ODWZ.pdf",
    "600080_20251030_IVCB.pdf",
]

SHENZHEN_PDFS = [
    "华润三九：2022年年度报告.pdf",
    "华润三九：2022年年度报告摘要.pdf",
    "华润三九：2023年一季度报告.pdf",
    "华润三九：2023年三季度报告.pdf",
    "华润三九：2023年半年度报告.pdf",
    "华润三九：2023年半年度报告摘要.pdf",
    "华润三九：2023年年度报告.pdf",
    "华润三九：2023年年度报告摘要.pdf",
    "华润三九：2024年一季度报告.pdf",
    "华润三九：2024年三季度报告.pdf",
    "华润三九：2024年半年度报告.pdf",
    "华润三九：2024年半年度报告摘要.pdf",
    "华润三九：2024年年度报告.pdf",
    "华润三九：2024年年度报告摘要.pdf",
    "华润三九：2025年一季度报告.pdf",
    "华润三九：2025年三季度报告.pdf",
    "华润三九：2025年半年度报告.pdf",
    "华润三九：2025年半年度报告摘要.pdf",
]

INDIVIDUAL_RESEARCH_PDFS = [
    "2025年三季报点评：内涵+外延双轮驱动，经营拐点已现.pdf",
    "业绩表现稳健，彰显强经营韧性与品牌力.pdf",
]

INDUSTRY_RESEARCH_PDFS = [
    "医药健康行业研究：从2025医保谈判看行业风向：成功率提升，创新导向持续强化.pdf",
]


def download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return
    print(f"  GET {dest.relative_to(ROOT)}", flush=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024 * 64):
                f.write(chunk)


def url_for(rel: str) -> str:
    return f"{BASE}/" + "/".join(quote(p) for p in rel.split("/"))


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    print("Fetching top-level xlsx/metadata files…")
    for rel in TOP_FILES:
        download(url_for(rel), DATA / rel)

    print("Fetching 附件2 - 上交所 PDFs…")
    for name in SHANGHAI_PDFS:
        download(url_for(f"附件2：财务报告/reports-上交所/{name}"), DATA / "附件2：财务报告/reports-上交所" / name)

    print("Fetching 附件2 - 深交所 PDFs…")
    for name in SHENZHEN_PDFS:
        download(url_for(f"附件2：财务报告/reports-深交所/{name}"), DATA / "附件2：财务报告/reports-深交所" / name)

    print("Fetching 附件5 - 个股研报…")
    for name in INDIVIDUAL_RESEARCH_PDFS:
        download(url_for(f"附件5：研报数据/个股研报/{name}"), DATA / "附件5：研报数据/个股研报" / name)

    print("Fetching 附件5 - 行业研报…")
    for name in INDUSTRY_RESEARCH_PDFS:
        download(url_for(f"附件5：研报数据/行业研报/{name}"), DATA / "附件5：研报数据/行业研报" / name)

    print(f"Done. Files under: {DATA}")


if __name__ == "__main__":
    sys.exit(main() or 0)
