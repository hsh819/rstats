"""任务二入口：附件4 问题 → 多轮对话 → SQL → 图表 → 写 result_2.xlsx。

输出列（附件7 表 2 格式）：
编号 | 问题类型 | 问题 | SQL 查询语句 | 图形格式 | 回答 | 图表

回答字段为 JSON 字符串：
  {"问题编号": ..., "问题类型": ..., "子问题": [{"Q","A","SQL","image"}]}
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import pandas as pd

from .. import config
from ..llm_client import get_client
from ..utils.cn_number import format_amount_wan, format_auto, format_percent, parse_number
from ..utils.excel_io import write_task_results_with_images
from ..utils.period import period_label
from . import chart, dialogue, intent_router, prompts, sql_runner
from .field_schema import FIELD_META, FieldMeta, canonical_field

RESULT_FILE = config.RESULT_DIR / "result_2.xlsx"


# ========== 规则侧 NL→SQL 回退（无 LLM 或 LLM 不返回时使用） ==========
def _resolve_field(intent: intent_router.Intent) -> tuple[Optional[str], Optional[FieldMeta]]:
    """返回 (canonical_name, FieldMeta) ；无命中返回 (None, None)。"""
    for f in intent.fields:
        canon = canonical_field(f)
        if canon and canon in FIELD_META:
            return canon, FIELD_META[canon]
    return None, None


def _company_clause(companies: list[str]) -> str:
    if not companies:
        return ""
    parts = []
    for c in companies:
        if str(c).isdigit():
            parts.append(f"stock_code='{c}'")
        else:
            parts.append(f"stock_abbr='{c}'")
    return " OR ".join(parts)


def _period_order_sql() -> str:
    return "CASE report_period WHEN 'Q1' THEN 1 WHEN 'HY' THEN 2 WHEN 'Q3' THEN 3 WHEN 'FY' THEN 4 END"


def rule_nl2sql(intent: intent_router.Intent) -> tuple[str, str]:
    """根据意图生成 SQL 与图表类型。无命中返回空串。"""
    _, fmeta = _resolve_field(intent)
    if not fmeta:
        return "", "table"
    table, col = fmeta.table, fmeta.column

    where_clauses: list[str] = []

    comp_clause = _company_clause(intent.companies)
    is_rank = intent.intent == "rank"
    if comp_clause:
        where_clauses.append(f"({comp_clause})")
    if intent.years:
        ys = ",".join(str(y) for y in intent.years)
        where_clauses.append(f"report_year IN ({ys})")
    if intent.periods:
        ps = ",".join(f"'{p}'" for p in intent.periods)
        where_clauses.append(f"report_period IN ({ps})")
    elif is_rank or intent.intent == "compare":
        # 排名/对比若没指定期，默认看年报，避免多期混淆
        where_clauses.append("report_period='FY'")

    select_cols = "stock_abbr, report_year, report_period, " + col
    sql = f"SELECT {select_cols} FROM {table}"
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)

    if is_rank:
        sql += f" ORDER BY {col} DESC LIMIT 10"
    else:
        sql += f" ORDER BY stock_code, report_year, {_period_order_sql()}"

    chart_type = (
        "line" if intent.intent == "trend"
        else "bar" if intent.intent in ("rank", "compare")
        else "table"
    )
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
    if sql:
        try:
            sql = sql_runner.validate_sql(sql)
        except sql_runner.UnsafeSQLError:
            return "", ct
    return sql, ct


# ========== 文字回答生成 ==========
def _row_period_label(rec: dict) -> str:
    y = rec.get("report_year")
    p = rec.get("report_period")
    if y and p:
        try:
            return period_label(int(y), str(p))
        except Exception:
            return f"{y}{p}"
    return ""


def _value_label(v, kind: str) -> str:
    return format_auto(v, kind)


def rule_answer(question: str, cols: list[str], rows: list[tuple], chart_type: str,
                field_name: Optional[str], fmeta: Optional[FieldMeta], intent_type: str) -> str:
    if not rows:
        return "未查询到数据"
    records = [dict(zip(cols, r)) for r in rows]
    kind = fmeta.kind if fmeta else "amount"
    field_zh = field_name or "数值"
    value_col = fmeta.column if fmeta else cols[-1]

    # 单点查询
    if intent_type == "query" and len(records) == 1:
        r = records[0]
        comp = r.get("stock_abbr") or ""
        period = _row_period_label(r)
        v = _value_label(r.get(value_col), kind)
        prefix = f"{comp} {period}".strip()
        return (f"{prefix}的{field_zh}为 {v}" if prefix else f"{field_zh}为 {v}")

    # 排名
    if intent_type == "rank":
        head = records[: min(5, len(records))]
        items = [f"{i+1}) {r.get('stock_abbr','')} {_value_label(r.get(value_col), kind)}"
                 for i, r in enumerate(head)]
        return f"{field_zh} 排名 Top{len(head)}：" + "; ".join(items)

    # 对比
    if intent_type == "compare" and len(records) >= 2:
        items = [f"{r.get('stock_abbr','')} {_row_period_label(r)} {_value_label(r.get(value_col), kind)}"
                 for r in records]
        return f"{field_zh} 对比：" + "; ".join(items)

    # 趋势 / 多行：跳过值为 None 的行找到首末有效值
    non_null = [r for r in records if parse_number(r.get(value_col)) is not None]
    if len(non_null) >= 2:
        head, tail = non_null[0], non_null[-1]
        v0 = parse_number(head.get(value_col))
        v1 = parse_number(tail.get(value_col))
        l0, l1 = _row_period_label(head), _row_period_label(tail)
        comp = head.get("stock_abbr", "")
        if v0 is not None and v1 is not None:
            delta = v1 - v0
            if kind in ("percent", "ratio"):
                return (f"{comp}的{field_zh}从{l0}的 {format_percent(v0)} 变化到{l1}的 {format_percent(v1)}"
                        f"（{delta:+.2f} 个百分点）")
            pct = (delta / abs(v0) * 100) if v0 else 0.0
            return (f"{comp}的{field_zh}从{l0}的 {format_amount_wan(v0)} 变化到{l1}的 {format_amount_wan(v1)}"
                    f"（{pct:+.2f}%）")
        return f"{comp}的{field_zh}：" + "; ".join(
            f"{_row_period_label(r)} {_value_label(r.get(value_col), kind)}" for r in non_null[:6]
        )
    if non_null:
        r = non_null[-1]
        return (f"{r.get('stock_abbr','')} {_row_period_label(r)} 的{field_zh}为 "
                f"{_value_label(r.get(value_col), kind)}（仅有效值）")

    return "未查询到有效数据"


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
    last_image_name = ""

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
        img_name = ""
        if records:
            img_path = config.RESULT_DIR / f"{qid}_{seq + t_idx}.jpg"
            try:
                # 在添加 _period 前固化 y_field（最后一列即目标字段）
                y_field = cols[-1]
                x_field = cols[0]
                if "report_year" in cols and "report_period" in cols:
                    for r in records:
                        r["_period"] = period_label(int(r.get("report_year") or 0),
                                                    str(r.get("report_period") or ""))
                    x_field = "_period"
                elif "stock_abbr" in cols and intent.intent in ("rank", "compare"):
                    x_field = "stock_abbr"
                series_field = (
                    "stock_abbr"
                    if "stock_abbr" in cols and len({r.get("stock_abbr") for r in records}) > 1
                    and intent.intent == "trend" else None
                )
                chart.auto_plot(records, chart_type, f"{qid} - {q[:24]}", img_path,
                                x_field=x_field, y_field=y_field, series_field=series_field)
                img_name = img_path.name
                last_image_name = img_name
            except Exception as e:
                print(f"[chart] fail: {e}")

        field_name, fmeta = _resolve_field(intent)
        ans = llm_answer(q, sql, cols, rows) or rule_answer(q, cols, rows, chart_type, field_name, fmeta, intent.intent)

        last_sql = sql
        last_chart_fmt = chart_type
        sub_results.append({"Q": q, "A": ans, "SQL": sql, "image": img_name})

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
        "图表": last_image_name,
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

    write_task_results_with_images(
        RESULT_FILE,
        rows=rows,
        columns=["编号", "问题类型", "问题", "SQL 查询语句", "图形格式", "回答", "图表"],
        image_col="图表",
        image_dir=config.RESULT_DIR,
        sheet_name="task2",
    )
    print(f"[task2] 写入 {RESULT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    main(args.limit)
