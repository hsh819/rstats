"""报告期识别：从文件名 / 披露日期推断 (report_year, report_period)。

report_period 枚举：
    Q1   一季度
    HY   半年度
    Q3   三季度
    FY   年度
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class ReportKey:
    stock_code: str
    stock_abbr: Optional[str]
    report_year: int
    report_period: str  # FY | Q1 | HY | Q3
    is_summary: bool = False  # 深交所"报告摘要"标记
    raw_filename: str = ""


# 深交所：`公司简称：YYYY年<周期>报告[摘要].pdf`
_SZ_RE = re.compile(
    r"^(?P<abbr>[^：]+)[:：](?P<year>\d{4})年(?P<period>一季度|半年度|三季度|年度)报告(?P<summary>摘要)?"
)
# 上交所：`<股票代码>_<YYYYMMDD>_<hash>.pdf`
_SH_RE = re.compile(r"^(?P<code>\d{6})_(?P<date>\d{8})_[A-Z0-9]+")


_SZ_PERIOD_MAP = {"一季度": "Q1", "半年度": "HY", "三季度": "Q3", "年度": "FY"}


def classify_filename(filename: str) -> Optional[ReportKey]:
    """按上交所 / 深交所命名规则解析文件名。无法识别返回 None。"""
    base = filename
    if base.endswith(".pdf") or base.endswith(".PDF"):
        base = base[:-4]

    m = _SZ_RE.match(base)
    if m:
        return ReportKey(
            stock_code="",
            stock_abbr=m.group("abbr").strip(),
            report_year=int(m.group("year")),
            report_period=_SZ_PERIOD_MAP[m.group("period")],
            is_summary=bool(m.group("summary")),
            raw_filename=filename,
        )

    m = _SH_RE.match(base)
    if m:
        code = m.group("code")
        d = date(int(m.group("date")[:4]), int(m.group("date")[4:6]), int(m.group("date")[6:]))
        year, period = _sh_date_to_period(d)
        return ReportKey(
            stock_code=code,
            stock_abbr=None,
            report_year=year,
            report_period=period,
            is_summary=False,
            raw_filename=filename,
        )
    return None


def _sh_date_to_period(d: date) -> tuple[int, str]:
    """上交所披露日启发式 → (report_year, period)。

    年报：次年 3-4 月披露       → (year-1, FY)
    一季报：当年 4-5 月          → (year, Q1)
    半年报：当年 7-9 月          → (year, HY)
    三季报：当年 10-11 月        → (year, Q3)
    """
    y, m = d.year, d.month
    if m in (1, 2, 3, 4):
        # 4 月披露同时覆盖"上年报 + 当年一季报"。用日期细分：月初更偏年报，月末偏一季报。
        if m <= 3 or (m == 4 and d.day < 20):
            return y - 1, "FY"
        return y, "Q1"
    if m in (5, 6):
        return y, "Q1"
    if m in (7, 8, 9):
        return y, "HY"
    if m in (10, 11):
        return y, "Q3"
    if m == 12:
        return y, "Q3"
    return y, "FY"


def period_sort_key(report_year: int, report_period: str) -> int:
    """同一公司跨期排序用：Q1<HY<Q3<FY。"""
    order = {"Q1": 1, "HY": 2, "Q3": 3, "FY": 4}
    return report_year * 10 + order.get(report_period, 0)


def period_label(report_year: int, report_period: str) -> str:
    """用于图表标签与文字输出。"""
    mapping = {"Q1": "一季报", "HY": "半年报", "Q3": "三季报", "FY": "年报"}
    return f"{report_year}{mapping.get(report_period, report_period)}"
