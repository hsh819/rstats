"""财报 PDF 文件分拣：
- 按文件名识别 (股票代码, 股票简称, 报告期, 是否摘要)
- 按 (股票代码, 报告年, 报告期) 分组，以便合并同一报告期下的多份 PDF（全文 + 摘要等）
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable

from ..utils.period import ReportKey, classify_filename


def collect_reports(dirs: Iterable[Path], known_abbr_to_code: dict[str, str]) -> dict[tuple[str, int, str], list[tuple[ReportKey, Path]]]:
    """返回 {(stock_code, report_year, report_period): [(key, path), ...]}"""
    buckets: dict[tuple[str, int, str], list[tuple[ReportKey, Path]]] = defaultdict(list)
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() != ".pdf":
                continue
            key = classify_filename(p.name)
            if key is None:
                print(f"[classifier] skip unrecognized: {p.name}")
                continue
            if not key.stock_code and key.stock_abbr:
                key.stock_code = known_abbr_to_code.get(key.stock_abbr, "")
            bucket_key = (key.stock_code, key.report_year, key.report_period)
            buckets[bucket_key].append((key, p))
    return buckets


def load_abbr_code_map(xlsx_path: Path) -> dict[str, str]:
    import pandas as pd

    df = pd.read_excel(xlsx_path, sheet_name="基本信息表")
    m = {}
    for _, row in df.iterrows():
        abbr = str(row.get("A股简称", "")).strip()
        code = str(row.get("股票代码", "")).strip()
        if abbr and code:
            m[abbr] = code.zfill(6) if code.isdigit() else code
    return m


def load_companies(xlsx_path: Path) -> list[dict]:
    import pandas as pd

    df = pd.read_excel(xlsx_path, sheet_name="基本信息表")
    records = []
    col_map = {
        "序号": "serial_number",
        "股票代码": "stock_code",
        "A股简称": "stock_abbr",
        "公司名称": "company_name",
        "英文名称": "english_name",
        "所属证监会行业": "csrc_industry",
        "上市交易所": "exchange",
        "证券类别": "security_type",
        "注册区域": "registered_area",
        "注册资本": "registered_capital",
        "雇员人数": "employee_count",
        "管理人员人数": "management_count",
    }
    for _, row in df.iterrows():
        rec: dict = {}
        for cn, en in col_map.items():
            v = row.get(cn)
            if en == "stock_code":
                v = str(v).zfill(6) if str(v).isdigit() else str(v).strip()
            elif en in {"employee_count", "management_count", "serial_number"}:
                try:
                    v = int(v) if v is not None and str(v).strip() else None
                except (TypeError, ValueError):
                    v = None
            else:
                v = None if v is None or (isinstance(v, float) and str(v) == "nan") else str(v).strip()
            rec[en] = v
        records.append(rec)
    return records
