"""任务一主入口：遍历所有财报 PDF → 抽取 → 合并同一报告期下多份 PDF
→ 校验 → 写入 SQLite，同时导出 validation_report.csv。

运行：
    python -m src.task1.build_db                # 默认并发，自动按 cpu 数
    python -m src.task1.build_db --jobs 4       # 指定 worker 数
    python -m src.task1.build_db --only 600080  # 仅处理特定股票

并发策略：用 ProcessPoolExecutor 并发解析 PDF（CPU bound：pdfplumber 解析），
主进程按 (stock_code, year, period) 串行合并并写入 SQLite。
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from .. import config
from ..utils.period import ReportKey, period_sort_key
from . import derived_fields, pdf_parser, report_classifier, validator


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
    """同一 (stock_code, year, period) 多份 PDF（全文 + 摘要等）时按覆盖度合并。"""
    def rank(it):
        key, _path, rec = it
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


# ============ Worker：解析单份 PDF（在子进程跑） ============
def _parse_one(stock_code: str, abbr_hint: str,
               key_year: int, key_period: str, key_is_summary: bool,
               raw_filename: str, path_str: str
               ) -> tuple[str, str, str, int, str, bool, str, "pdf_parser.ExtractedRecord"]:
    """子进程入口。返回 (stock_code, abbr_hint, raw_filename, year, period, is_summary, path_str, rec)。"""
    path = Path(path_str)
    rec = pdf_parser.parse_pdf(path)
    rec.stock_code = stock_code
    if not rec.stock_abbr:
        rec.stock_abbr = abbr_hint
    y = rec.detected_year if rec.detected_year else key_year
    p = rec.detected_period if rec.detected_period else key_period
    is_sum = key_is_summary or rec.is_summary
    return stock_code, abbr_hint, raw_filename, y, p, is_sum, path_str, rec


# ============ 主流程 ============
def run(only_stock: Optional[str] = None, jobs: Optional[int] = None, verbose: bool = False) -> None:
    conn = init_db(config.DB_PATH, config.SCHEMA_PATH)

    # 1) 公司基本信息
    print(f"[1/4] 加载附件1：公司基本信息（DATA_DIR={config.DATA_DIR}）")
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
    pdf_count = sum(len(v) for v in buckets.values())
    print(f"  报告期分组: {len(buckets)}; PDF: {pdf_count}")

    # 3) 并发解析
    print(f"[3/4] 并发解析 PDF (jobs={jobs or os.cpu_count()})")
    parse_args: list[tuple] = []
    for bucket_key, items in buckets.items():
        stock_code, _, _ = bucket_key
        for key, path in items:
            abbr_hint = key.stock_abbr or code_to_abbr.get(stock_code, "")
            parse_args.append((
                stock_code, abbr_hint,
                key.report_year, key.report_period, key.is_summary,
                key.raw_filename, str(path),
            ))

    parsed: list[tuple] = []
    failed: list[tuple[str, str]] = []
    n_workers = jobs or max(1, (os.cpu_count() or 4))
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        fut_map = {ex.submit(_parse_one, *args): args for args in parse_args}
        for fut in tqdm(as_completed(fut_map), total=len(fut_map), desc="parse"):
            args = fut_map[fut]
            try:
                parsed.append(fut.result())
            except Exception as e:  # noqa: BLE001
                failed.append((args[6], str(e)))
                if verbose:
                    print(f"[parse-fail] {args[6]}: {e}")

    if failed:
        print(f"  {len(failed)} 份 PDF 解析失败")

    # 按内容年份/周期重新分组
    regrouped: dict[tuple[str, int, str], list[tuple[ReportKey, Path, pdf_parser.ExtractedRecord]]] = {}
    for stock_code, abbr_hint, raw_fn, y, p, is_sum, path_str, rec in parsed:
        rk = ReportKey(
            stock_code=stock_code, stock_abbr=abbr_hint,
            report_year=y, report_period=p,
            is_summary=is_sum, raw_filename=raw_fn,
        )
        regrouped.setdefault((stock_code, y, p), []).append((rk, Path(path_str), rec))

    # 写库 + 校验（主进程串行）
    print(f"[4/4] 写库 + 校验 ({len(regrouped)} groups)")
    all_validations: list[validator.ValidationEntry] = []
    BATCH = 50
    pending = 0
    for bucket_key in tqdm(sorted(regrouped.keys(), key=lambda x: (x[0], period_sort_key(x[1], x[2]))),
                           desc="upsert"):
        stock_code, report_year, report_period = bucket_key
        extracted_list = regrouped[bucket_key]
        items = [(k, p) for k, p, _ in extracted_list]

        merged = merge_extracted(extracted_list)
        stock_abbr = merged.stock_abbr or code_to_abbr.get(stock_code, "")
        meta = {
            "stock_code": stock_code, "stock_abbr": stock_abbr,
            "report_year": report_year, "report_period": report_period,
        }
        upsert_row(conn, "core_performance_indicators_sheet", CORE_COLUMNS, {**meta, **merged.core})
        upsert_row(conn, "balance_sheet", BALANCE_COLUMNS, {**meta, **merged.balance})
        upsert_row(conn, "cash_flow_sheet", CASHFLOW_COLUMNS, {**meta, **merged.cash_flow})
        upsert_row(conn, "income_sheet", INCOME_COLUMNS, {**meta, **merged.income})
        pending += 1
        if pending >= BATCH:
            conn.commit()
            pending = 0

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
    conn.commit()

    # 派生字段回填（资产负债率/毛利率/净利率/同比/环比）
    print("[5/5] 派生字段回填")
    stats = derived_fields.derive_missing_fields(conn)
    for k, v in stats.items():
        print(f"  {k}: +{v} 行")

    # 写 validation_report
    for e in all_validations:
        conn.execute(
            "INSERT INTO validation_report (pdf_path, stock_code, report_year, report_period, "
            "table_name, field_name, rule, status, diff, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (e.pdf_path, e.stock_code, e.report_year, e.report_period,
             e.table_name, e.field_name, e.rule, e.status, e.diff, e.note),
        )
    conn.commit()

    csv_path = config.DB_DIR / "validation_report.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["pdf_path", "stock_code", "report_year", "report_period",
                    "table_name", "field_name", "rule", "status", "diff", "note"])
        for e in all_validations:
            w.writerow([e.pdf_path, e.stock_code, e.report_year, e.report_period,
                        e.table_name, e.field_name, e.rule, e.status, e.diff, e.note])

    # 失败清单单独写 fail_log
    if failed:
        log_path = config.DB_DIR / "parse_failures.txt"
        log_path.write_text("\n".join(f"{p}\t{e}" for p, e in failed), encoding="utf-8")
        print(f"  parse_failures.txt 写入 {len(failed)} 条")

    # 汇总
    status_counts: dict[str, int] = {}
    for e in all_validations:
        status_counts[e.status] = status_counts.get(e.status, 0) + 1
    print(f"  validation: {dict(sorted(status_counts.items()))}")
    for tbl in ("core_performance_indicators_sheet", "balance_sheet", "income_sheet", "cash_flow_sheet"):
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: {n} 行")
    conn.close()
    print(f"[DONE] DB 写入 {config.DB_PATH}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只处理指定股票代码", default=None)
    ap.add_argument("--jobs", type=int, default=None, help="并发 worker 数（默认 cpu_count）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    run(only_stock=args.only, jobs=args.jobs, verbose=args.verbose)


if __name__ == "__main__":
    main()
