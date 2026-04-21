"""任务二入口：读附件4 问题 → 多轮对话 → SQL → 图表 → 写 result_2.xlsx。

输出列（附件7 表 2 格式）：
编号 | 问题类型 | 问题 | SQL 查询语句 | 图形格式 | 回答

回答字段为 JSON 字符串，包含问题、子任务列表（Q/A/SQL/图）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import pandas as pd

from .. import config
from ..llm_client import get_client
from ..utils.excel_io import write_task_results
from ..utils.period import period_label, period_sort_key
from . import chart, dialogue, intent_router, prompts, sql_runner

RESULT_FILE = config.RESULT_DIR / "result_2.xlsx"


# ========== 规则侧 NL→SQL 回退（无 LLM 或 LLM 不返回时使用） ==========
_FIELD_TABLE = {
    "利润总额": ("income_sheet", "total_profit"),
    "净利润": ("income_sheet", "net_profit"),
    "营业总收入": ("income_sheet", "total_operating_revenue"),
    "营业收入": ("income_sheet", "total_operating_revenue"),
    "主营业务收入": ("income_sheet", "total_operating_revenue"),
    "毛利率": ("core_performance_indicators_sheet", "gross_profit_margin"),
    "净利率": ("core_performance_indicators_sheet", "net_profit_margin"),
    "每股收益": ("core_performance_indicators_sheet", "eps"),
    "ROE": ("core_performance_indicators_sheet", "roe"),
    "净资产收益率": ("core_performance_indicators_sheet", "roe"),
    "总资产": ("balance_sheet", "asset_total_assets"),
    "总负债": ("balance_sheet", "liability_total_liabilities"),
    "经营活动现金流": ("cash_flow_sheet", "operating_cf_net_amount"),
    "研发费用": ("income_sheet", "operating_expense_rnd_expenses"),
    "资产负债率": ("balance_sheet", "asset_liability_ratio"),
    "所有者权益": ("balance_sheet", "equity_total_equity"),
}


def rule_nl2sql(intent: intent_router.Intent) -> tuple[str, str]:
    """根据意图生成 SQL 与图表类型（粗略规则）。无命中返回空串。"""
    if not intent.fields or not intent.companies:
        return "", "table"
    field_zh = intent.fields[0]
    table, col = _FIELD_TABLE.get(field_zh, ("core_performance_indicators_sheet", "eps"))

    wh = []
    # company
    comps = intent.companies
    where_comp = " OR ".join([f"stock_abbr='{c}'" if not c.isdigit() else f"stock_code='{c}'" for c in comps])
    wh.append(f"({where_comp})")
    # year
    if intent.years:
        ys = ",".join(str(y) for y in intent.years)
        wh.append(f"report_year IN ({ys})")
    # period
    if intent.periods:
        ps = ",".join(f"'{p}'" for p in intent.periods)
        wh.append(f"report_period IN ({ps})")

    where_clause = " AND ".join(wh)
    select_cols = "stock_abbr, report_year, report_period, " + col
    sql = (
        f"SELECT {select_cols} FROM {table} "
        f"WHERE {where_clause} "
        f"ORDER BY stock_code, report_year, "
        f"CASE report_period WHEN 'Q1' THEN 1 WHEN 'HY' THEN 2 WHEN 'Q3' THEN 3 WHEN 'FY' THEN 4 END"
    )

    chart_type = "line" if intent.intent == "trend" else ("bar" if intent.intent in ("rank", "compare") else "table")
    return sql, chart_type


# ========== LLM 侧 NL→SQL（若可用） ==========
def llm_nl2sql(question: str, intent: intent_router.Intent) -> tuple[str, str]:
    client = get_client()
    if not client.enabled:
        return "", "table"
    user = prompts.NL2SQL_PROMPT_TEMPLATE.format(
        schema=prompts.DB_SCHEMA_BRIEF,
        entities=intent.__dict__,
        question=question,
    )
    out = client.chat_json([
        {"role": "system", "content": "你是严谨的 SQL 生成助手。"},
        {"role": "user", "content": user},
    ])
    sql = (out or {}).get("sql", "") or ""
    ct = (out or {}).get("chart_type", "table") or "table"
    return sql, ct


# ========== 文字回答生成 ==========
def rule_answer(question: str, cols: list[str], rows: list[tuple], chart_type: str) -> str:
    if not rows:
        return "未查询到数据"
    if len(rows) == 1 and len(cols) == 1:
        return f"{cols[0]}: {rows[0][0]}"
    if chart_type == "line" or len(rows) > 1:
        # 取首列为 label、末列为值
        head = rows[0]
        tail = rows[-1]
        last_col = cols[-1]
        try:
            start_v = float(head[-1]) if head[-1] is not None else None
            end_v = float(tail[-1]) if tail[-1] is not None else None
            if start_v is not None and end_v is not None:
                delta = end_v - start_v
                pct = (delta / abs(start_v) * 100) if start_v else 0
                return f"{last_col} 从 {head[:-1]} 的 {start_v} 变化到 {tail[:-1]} 的 {end_v}（{pct:+.2f}%）"
        except (TypeError, ValueError):
            pass
    return f"共 {len(rows)} 行：" + "; ".join(str(r) for r in rows[:3])


def llm_answer(question: str, sql: str, cols: list[str], rows: list[tuple]) -> str:
    client = get_client()
    if not client.enabled:
        return ""
    records = [dict(zip(cols, r)) for r in rows[:30]]
    user = prompts.ANSWER_PROMPT_TEMPLATE.format(
        question=question, sql=sql, result=json.dumps(records, ensure_ascii=False, default=str),
    )
    out = client.chat_json([
        {"role": "system", "content": "你是财经助手。"},
        {"role": "user", "content": user},
    ])
    return (out or {}).get("answer", "") or ""


# ========== 单题处理 ==========
def process_question(qid: str, qtype: str, turns: list[dict], seq: int = 1) -> dict:
    """把 [{"Q":...}] 形式的多轮问题跑完，返回行记录 dict。"""
    state = dialogue.SessionState()
    sub_results = []
    last_sql = ""
    last_chart_fmt = "table"
    last_image: Optional[Path] = None

    for t_idx, turn in enumerate(turns):
        q = turn.get("Q") or ""
        if not q:
            continue
        intent = dialogue.step(state, q)

        if intent.need_clarify:
            sub_results.append({"Q": q, "A": f"澄清：{intent.clarify_question}", "SQL": "", "image": ""})
            continue

        sql, chart_type = llm_nl2sql(q, intent)
        if not sql:
            sql, chart_type = rule_nl2sql(intent)
        if not sql:
            sub_results.append({"Q": q, "A": "信息不足，无法生成 SQL", "SQL": "", "image": ""})
            continue

        try:
            cols, rows = sql_runner.execute(config.DB_PATH, sql)
        except Exception as e:
            sub_results.append({"Q": q, "A": f"SQL 执行失败：{e}", "SQL": sql, "image": ""})
            continue

        records = sql_runner.rows_to_records(cols, rows)

        # 出图
        img_path = config.RESULT_DIR / f"{qid}_{seq + t_idx}.jpg"
        try:
            # 启发式 x / y field：x=first text col 或 period_label，y=最后一个 SQL 列（数值）
            y_field = cols[-1] if cols else None
            x_field = cols[0] if cols else None
            if "report_year" in cols and "report_period" in cols:
                for r in records:
                    r["_period"] = period_label(r.get("report_year") or 0, str(r.get("report_period") or ""))
                x_field = "_period"
            series_field = "stock_abbr" if "stock_abbr" in cols and len({r.get("stock_abbr") for r in records}) > 1 else None
            chart.auto_plot(records, chart_type, f"{qid} - {q[:24]}", img_path,
                            x_field=x_field, y_field=y_field, series_field=series_field)
            last_image = img_path
        except Exception as e:
            print(f"[chart] fail: {e}")

        # 文字回答
        ans = llm_answer(q, sql, cols, rows) or rule_answer(q, cols, rows, chart_type)

        last_sql = sql
        last_chart_fmt = chart_type
        sub_results.append({"Q": q, "A": ans, "SQL": sql, "image": img_path.name})

    # 附件 7 表 2 JSON（回答字段）
    answer_json = {
        "问题编号": qid,
        "问题类型": qtype,
        "子问题": sub_results,
    }
    return {
        "编号": qid,
        "问题类型": qtype,
        "问题": json.dumps([{"Q": t.get("Q")} for t in turns], ensure_ascii=False),
        "SQL 查询语句": last_sql,
        "图形格式": last_chart_fmt,
        "回答": json.dumps(answer_json, ensure_ascii=False, indent=2),
    }


# ========== 主入口 ==========
def main(limit: Optional[int] = None):
    df = pd.read_excel(config.FILE_Q_TASK2)
    rows = []
    for i, row in df.iterrows():
        if limit is not None and i >= limit:
            break
        qid = str(row["编号"]).strip()
        qtype = str(row["问题类型"]).strip()
        raw_q = row["问题"]
        try:
            turns = json.loads(raw_q) if isinstance(raw_q, str) else raw_q
        except json.JSONDecodeError:
            turns = [{"Q": str(raw_q)}]
        if isinstance(turns, dict):
            turns = [turns]
        print(f"[task2] {qid} {qtype}: {len(turns)} turns")
        rows.append(process_question(qid, qtype, turns))

    write_task_results(
        RESULT_FILE,
        rows=rows,
        columns=["编号", "问题类型", "问题", "SQL 查询语句", "图形格式", "回答"],
        sheet_name="task2",
    )
    print(f"[task2] 写入 {RESULT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    main(args.limit)
