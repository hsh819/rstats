"""任务一主入口：遍历所有财报 PDF → 抽取 → 合并同一报告期下多份 PDF
→ 校验 → 写入 SQLite，同时导出 validation_report.csv。

运行：
    python -m src.task1.build_db

可选：传 --only 600080 只处理特定股票。
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path
from typing import Optional

from .. import config
from ..utils.period import ReportKey, period_sort_key
from . import pdf_parser, report_classifier, validator


# ============ DB 字段集合（保证 INSERT 时字段齐全） ============
CORE_COLUMNS = [
    "stock_code", "stock_abbr", "report_year", "report_period",
    "eps", "total_operating_revenue", "operating_revenue_yoy_growth", "operating_revenue_qoq_growth",
    "net_profit_10k_yuan", "net_profit_yoy_growth", "net_profit_qoq_growth",
    "net_asset_per_share", "roe", "operating_cf_per_share",
    "net_profit_excl_non_recurring", "net_profit_excl_non_recurring_yoy",
    "gross_profit_margin", "net_profit_margin", "roe_weighted_excl_non_recurring",
]
BALANCE_COLUMNS = [
    "stock_code", "stock_abbr", "report_year", "report_period",
    "asset_cash_and_cash_equivalents", "asset_accounts_receivable",
    "asset_inventory", "asset_trading_financial_assets", "asset_construction_in_progress",
    "asset_total_assets", "asset_total_assets_yoy_growth",
    "liability_accounts_payable", "liability_advance_from_customers",
    "liability_total_liabilities", "liability_total_liabilities_yoy_growth",
    "liability_contract_liabilities", "liability_short_term_loans",
    "asset_liability_ratio", "equity_unappropriated_profit", "equity_total_equity",
]
CASHFLOW_COLUMNS = [
    "stock_code", "stock_abbr", "report_year", "report_period",
    "net_cash_flow", "net_cash_flow_yoy_growth",
    "operating_cf_net_amount", "operating_cf_ratio_of_net_cf", "operating_cf_cash_from_sales",
    "investing_cf_net_amount", "investing_cf_ratio_of_net_cf",
    "investing_cf_cash_for_investments", "investing_cf_cash_from_investment_recovery",
    "financing_cf_cash_from_borrowing", "financing_cf_cash_for_debt_repayment",
    "financing_cf_net_amount", "financing_cf_ratio_of_net_cf",
]
INCOME_COLUMNS = [
    "stock_code", "stock_abbr", "report_year", "report_period",
    "net_profit", "net_profit_yoy_growth", "other_income",
    "total_operating_revenue", "operating_revenue_yoy_growth",
    "operating_expense_cost_of_sales", "operating_expense_selling_expenses",
    "operating_expense_administrative_expenses", "operating_expense_financial_expenses",
    "operating_expense_rnd_expenses", "operating_expense_taxes_and_surcharges",
    "total_operating_expenses", "operating_profit", "total_profit",
    "asset_impairment_loss", "credit_impairment_loss",
]


# ============ DB 初始化 ============
def init_db(db_path: Path, schema_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 清理旧库，重新建
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def insert_companies(conn: sqlite3.Connection, companies: list[dict]) -> None:
    cols = [
        "serial_number", "stock_code", "stock_abbr", "company_name", "english_name",
        "csrc_industry", "exchange", "security_type", "registered_area",
        "registered_capital", "employee_count", "management_count",
    ]
    placeholders = ",".join(["?"] * len(cols))
    conn.executemany(
        f"INSERT OR REPLACE INTO companies ({','.join(cols)}) VALUES ({placeholders})",
        [tuple(c.get(k) for k in cols) for c in companies],
    )
    conn.commit()


def upsert_row(conn: sqlite3.Connection, table: str, columns: list[str], row: dict) -> None:
    values = [row.get(c) for c in columns]
    placeholders = ",".join(["?"] * len(columns))
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        values,
    )


# ============ 合并策略 ============
def merge_extracted(
    items: list[tuple[ReportKey, Path, pdf_parser.ExtractedRecord]],
) -> pdf_parser.ExtractedRecord:
    """同一 (stock_code, year, period) 可能有多份 PDF（全文 + 摘要），
    规则：优先使用 **非摘要** 的覆盖度更高的字段；然后用摘要补齐仍缺的字段。
    """
    # 按覆盖度降序（非摘要优先）
    def rank(it):
        key, path, rec = it
        summary_penalty = 1 if key.is_summary else 0
        cov = sum(rec.coverage.values())
        return (summary_penalty, -cov)

    items_sorted = sorted(items, key=rank)
    merged = pdf_parser.ExtractedRecord()
    for _, _, rec in items_sorted:
        for attr in ("core", "balance", "income", "cash_flow"):
            target: dict = getattr(merged, attr)
            source: dict = getattr(rec, attr)
            for k, v in source.items():
                if v is None:
                    continue
                if k not in target or target[k] is None:
                    target[k] = v
        if not merged.stock_code and rec.stock_code:
            merged.stock_code = rec.stock_code
        if not merged.stock_abbr and rec.stock_abbr:
            merged.stock_abbr = rec.stock_abbr
    return merged


# ============ 主流程 ============
def run(only_stock: Optional[str] = None, verbose: bool = False) -> None:
    conn = init_db(config.DB_PATH, config.SCHEMA_PATH)

    # 1) 公司基本信息
    print("[1/4] 加载附件1：公司基本信息")
    companies = report_classifier.load_companies(config.FILE_COMPANIES)
    insert_companies(conn, companies)
    print(f"  companies: {len(companies)} 行")

    abbr_to_code = {c["stock_abbr"]: c["stock_code"] for c in companies if c.get("stock_abbr") and c.get("stock_code")}
    code_to_abbr = {c["stock_code"]: c["stock_abbr"] for c in companies if c.get("stock_code")}

    # 2) 分拣 PDF
    print("[2/4] 分拣 PDF")
    buckets = report_classifier.collect_reports(
        [config.DIR_REPORTS_SH, config.DIR_REPORTS_SZ],
        known_abbr_to_code=abbr_to_code,
    )
    if only_stock:
        buckets = {k: v for k, v in buckets.items() if k[0] == only_stock}
    print(f"  报告期分组: {len(buckets)}")

    # 3) 抽取 + 合并 + 校验 + 写库
    print("[3/4] 解析 PDF 并入库")
    all_validations: list[validator.ValidationEntry] = []

    # 先解析所有 PDF，得到内容识别的真实 (year, period)；再按内容 regroup
    parsed: list[tuple[str, int, str, ReportKey, Path, pdf_parser.ExtractedRecord]] = []
    for bucket_key in buckets:
        stock_code, _, _ = bucket_key
        for key, path in buckets[bucket_key]:
            print(f"  -> {path.name}")
            rec = pdf_parser.parse_pdf(path)
            rec.stock_code = stock_code
            if not rec.stock_abbr:
                rec.stock_abbr = key.stock_abbr or code_to_abbr.get(stock_code, "")
            # 内容识别优先；识别不出回落到文件名
            y = rec.detected_year if rec.detected_year else key.report_year
            p = rec.detected_period if rec.detected_period else key.report_period
            if rec.is_summary:
                key.is_summary = True
            parsed.append((stock_code, y, p, key, path, rec))

    regrouped: dict[tuple[str, int, str], list[tuple[ReportKey, Path, pdf_parser.ExtractedRecord]]] = {}
    for stock_code, y, p, key, path, rec in parsed:
        regrouped.setdefault((stock_code, y, p), []).append((key, path, rec))

    for bucket_key in sorted(regrouped.keys(), key=lambda x: (x[0], period_sort_key(x[1], x[2]))):
        stock_code, report_year, report_period = bucket_key
        extracted_list = regrouped[bucket_key]
        items = [(k, p) for k, p, _ in extracted_list]

        merged = merge_extracted(extracted_list)
        stock_abbr = merged.stock_abbr or code_to_abbr.get(stock_code, "")

        meta = {
            "stock_code": stock_code,
            "stock_abbr": stock_abbr,
            "report_year": report_year,
            "report_period": report_period,
        }

        # 写入 4 张表
        upsert_row(conn, "core_performance_indicators_sheet", CORE_COLUMNS, {**meta, **merged.core})
        upsert_row(conn, "balance_sheet", BALANCE_COLUMNS, {**meta, **merged.balance})
        upsert_row(conn, "cash_flow_sheet", CASHFLOW_COLUMNS, {**meta, **merged.cash_flow})
        upsert_row(conn, "income_sheet", INCOME_COLUMNS, {**meta, **merged.income})
        conn.commit()

        # 校验
        rec_source = ";".join(p.name for _, p in items)
        entries = validator.validate(
            rec_source=rec_source,
            stock_code=stock_code,
            report_year=report_year,
            report_period=report_period,
            core=merged.core,
            balance=merged.balance,
            income=merged.income,
            cash_flow=merged.cash_flow,
        )
        all_validations.extend(entries)
        if verbose:
            cov = {
                "core": len(merged.core), "balance": len(merged.balance),
                "income": len(merged.income), "cash_flow": len(merged.cash_flow),
            }
            print(f"     [{stock_code} {report_year}{report_period}] coverage={cov}")

    # 4) 写 validation_report
    print("[4/4] 写入校验报告")
    for e in all_validations:
        conn.execute(
            "INSERT INTO validation_report (pdf_path, stock_code, report_year, report_period, "
            "table_name, field_name, rule, status, diff, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (e.pdf_path, e.stock_code, e.report_year, e.report_period,
             e.table_name, e.field_name, e.rule, e.status, e.diff, e.note),
        )
    conn.commit()

    # 导出 CSV 便于人工审阅
    csv_path = config.DB_DIR / "validation_report.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "pdf_path", "stock_code", "report_year", "report_period",
            "table_name", "field_name", "rule", "status", "diff", "note",
        ])
        for e in all_validations:
            w.writerow([e.pdf_path, e.stock_code, e.report_year, e.report_period,
                        e.table_name, e.field_name, e.rule, e.status, e.diff, e.note])
    print(f"  validation_report.csv: {len(all_validations)} 行 -> {csv_path}")

    # 汇总
    for tbl in ("core_performance_indicators_sheet", "balance_sheet", "income_sheet", "cash_flow_sheet"):
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: {n} 行")
    conn.close()
    print(f"[DONE] DB 写入 {config.DB_PATH}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只处理指定股票代码", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    run(only_stock=args.only, verbose=args.verbose)


if __name__ == "__main__":
    main()
