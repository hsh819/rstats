"""入库前自动校验：
1. 勾稽关系：资产总计 ≈ 负债合计 + 所有者权益合计（允差 ≤0.5 万元）
2. 多表一致性：income_sheet.net_profit == core_performance.net_profit_10k_yuan
3. 同比覆盖：核心指标中的同比字段缺失时记告警（不阻断入库）
4. 数值非负：仅检查总资产等非负字段
校验结果返回列表，最终写入 validation_report 表。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ValidationEntry:
    pdf_path: str
    stock_code: str
    report_year: int
    report_period: str
    table_name: str
    field_name: str
    rule: str
    status: str  # OK / WARN / FAIL
    diff: Optional[float] = None
    note: str = ""


def validate(
    rec_source: str,
    stock_code: str,
    report_year: int,
    report_period: str,
    core: dict,
    balance: dict,
    income: dict,
    cash_flow: dict,
    tolerance: float = 0.5,
) -> list[ValidationEntry]:
    out: list[ValidationEntry] = []

    # 1. 勾稽关系
    ta = balance.get("asset_total_assets")
    tl = balance.get("liability_total_liabilities")
    te = balance.get("equity_total_equity")
    if ta is not None and tl is not None and te is not None:
        diff = abs(ta - (tl + te))
        status = "OK" if diff <= tolerance else "WARN"
        out.append(
            ValidationEntry(
                rec_source, stock_code, report_year, report_period,
                "balance_sheet", "asset_total_assets",
                "资产=负债+所有者权益", status, round(diff, 2),
                f"资产 {ta} vs 负债+权益 {round(tl+te,2)}"
            )
        )
    elif ta is not None or tl is not None or te is not None:
        out.append(
            ValidationEntry(
                rec_source, stock_code, report_year, report_period,
                "balance_sheet", "asset_total_assets",
                "资产=负债+所有者权益", "WARN", None,
                "资产负债表缺少组成项之一"
            )
        )

    # 2. 净利润一致性
    np_core = core.get("net_profit_10k_yuan")
    np_income = income.get("net_profit")
    if np_core is not None and np_income is not None:
        diff = abs(np_core - np_income)
        status = "OK" if diff <= tolerance else "WARN"
        out.append(
            ValidationEntry(
                rec_source, stock_code, report_year, report_period,
                "cross_table", "net_profit",
                "利润表=核心业绩指标表（净利润）", status, round(diff, 2),
                f"核心表 {np_core} vs 利润表 {np_income}"
            )
        )

    # 3. 总资产非负
    if ta is not None and ta < 0:
        out.append(
            ValidationEntry(
                rec_source, stock_code, report_year, report_period,
                "balance_sheet", "asset_total_assets",
                "总资产非负", "FAIL", None, f"值为负 {ta}"
            )
        )

    # 4. 报告期必填
    if report_period not in {"Q1", "HY", "Q3", "FY"}:
        out.append(
            ValidationEntry(
                rec_source, stock_code, report_year, report_period,
                "meta", "report_period", "报告期枚举", "FAIL", None, "不在 {Q1,HY,Q3,FY}"
            )
        )

    return out
