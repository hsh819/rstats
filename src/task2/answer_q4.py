"""任务二入口：附件4 问题 → 多轮对话 → SQL → 图表 → 写 result_2.xlsx。

输出列（附件7 表 2 格式）：
编号 | 问题类型 | 问题 | SQL 查询语句 | 图形格式 | 回答 | 图表

回答字段为 JSON 字符串：
  {"问题编号": ..., "问题类型": ..., "子问题": [{"Q","A","SQL","image"}]}
"""
from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path
from typing import Optional

import pandas as pd
from tqdm import tqdm

from .. import config
from ..llm_client import get_client
from ..utils.cn_number import format_amount_wan, format_auto, format_percent, parse_number
from ..utils.excel_io import write_task_results_with_images
from ..utils.period import period_label
from . import advanced_rules, chart, dialogue, intent_router, prompts, sql_runner
from .field_schema import FIELD_META, FieldMeta, canonical_field

RESULT_FILE = config.RESULT_DIR / "result_2.xlsx"


# ========== 规则侧 NL→SQL 回退（无 LLM 或 LLM 不返回时使用） ==========
def _resolve_field(intent: intent_router.Intent) -> tuple[Optional[str], Optional[FieldMeta]]:
    """返回 (canonical_name, FieldMeta) ；无命中返回 (None, None)。

    启发式：
    - filter 单位是 "%"/"个百分点" → 优先返回百分比字段
    - 其他情况下，按首个匹配返回，但把"净利润/营业收入"这类通用兜底字段放最后，
      让更具体字段（如 经营活动现金流）优先。
    """
    if not intent.fields:
        return None, None
    prefer_percent = any(f.unit_hint in ("%", "个百分点") for f in intent.filters)
    generic = {"净利润", "营业收入", "营业总收入"}

    candidates = []
    for f in intent.fields:
        canon = canonical_field(f)
        if canon and canon in FIELD_META:
            candidates.append((canon, FIELD_META[canon]))

    if not candidates:
        return None, None
    if prefer_percent:
        for c in candidates:
            if c[1].kind in ("percent", "ratio"):
                return c
    # 非通用字段优先
    for c in candidates:
        if c[0] not in generic:
            return c
    return candidates[0]


def _company_clause(companies: list[str]) -> str:
    if not companies:
        return ""
    parts = []
    for c in companies:
        s = str(c)
        if s.isdigit():
            # 全部数据 stock_code 都是 6 位零填充
            parts.append(f"stock_code='{s.zfill(6)}'")
        else:
            parts.append(f"stock_abbr='{s}'")
    return " OR ".join(parts)


def _period_order_sql() -> str:
    return "CASE report_period WHEN 'Q1' THEN 1 WHEN 'HY' THEN 2 WHEN 'Q3' THEN 3 WHEN 'FY' THEN 4 END"


# 单位 → 万元 换算倍数
_UNIT_TO_WAN = {
    "亿元": 10000, "亿": 10000,
    "千万元": 1000, "千万": 1000,
    "百万元": 100, "百万": 100,
    "万元": 1, "万": 1,
    "元": 0.0001,
}


def _filter_value_to_col_unit(f: "intent_router.Filter", fmeta: FieldMeta) -> float:
    """把 filter.value 从原始单位换算到字段列自身单位（金额字段→万元；百分比字段→百分比原值）。"""
    u = f.unit_hint
    if fmeta.kind == "percent":
        return f.value  # 值本身就是百分比
    if fmeta.kind == "amount":
        mul = _UNIT_TO_WAN.get(u, 1 if u in ("", "元") else 1)
        if u == "元":
            mul = 0.0001
        return f.value * mul
    return f.value


def _filter_to_where(f: "intent_router.Filter", col: str, fmeta: FieldMeta) -> str:
    if f.op == "<0":
        return f"{col} < 0"
    if f.op == ">0":
        return f"{col} > 0"
    v = _filter_value_to_col_unit(f, fmeta)
    return f"{col} {f.op} {v}"


def rule_nl2sql(intent: intent_router.Intent) -> tuple[str, str]:
    """根据意图生成 SQL 与图表类型。无命中返回空串。

    支持：
      - 基本查询 / 趋势 / 排名 / 对比
      - Intent.filters: `field op value` WHERE 子句
      - Intent.loss_flag: 自动 net_profit<0
      - Intent.aggregate ∈ {AVG, SUM, MEDIAN, COUNT}
    """
    canon_name, fmeta = _resolve_field(intent)
    has_aggregate = bool(intent.aggregate)

    # 聚合分支：SELECT AVG/SUM/COUNT
    if has_aggregate:
        if not fmeta and intent.aggregate != "COUNT":
            return "", "table"
        return _rule_nl2sql_aggregate(intent, canon_name, fmeta)

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
        where_clauses.append("report_period='FY'")

    # filters → WHERE（值比较），并保证被比较的字段有 NOT NULL
    seen_conds: set[str] = set()
    for f in intent.filters:
        cond = _filter_to_where(f, col, fmeta)
        if cond not in seen_conds:
            where_clauses.append(cond)
            seen_conds.add(cond)
    if intent.filters:
        where_clauses.append(f"{col} IS NOT NULL")
    # 亏损 / 为负：对所选字段加 <0（若还没被 filters 覆盖）
    if intent.loss_flag:
        cond = f"{col} < 0"
        if cond not in seen_conds:
            where_clauses.append(cond)
            seen_conds.add(cond)

    select_cols = "stock_abbr, stock_code, report_year, report_period, " + col
    sql = f"SELECT {select_cols} FROM {table}"
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)

    if is_rank:
        sql += f" ORDER BY {col} DESC LIMIT 10"
    elif intent.filters or intent.loss_flag:
        # 筛选查询：按值降序便于阅读
        sql += f" ORDER BY {col} DESC LIMIT 50"
    else:
        sql += f" ORDER BY stock_code, report_year, {_period_order_sql()}"

    chart_type = (
        "line" if intent.intent == "trend"
        else "bar" if intent.intent in ("rank", "compare") or intent.filters or intent.loss_flag
        else "table"
    )
    return sql, chart_type


def _rule_nl2sql_aggregate(intent: "intent_router.Intent",
                           canon_name: Optional[str], fmeta: Optional[FieldMeta]) -> tuple[str, str]:
    """AVG / SUM / COUNT / MEDIAN 走独立分支。"""
    agg = intent.aggregate

    # COUNT：统计满足条件的行数
    if agg == "COUNT":
        table = fmeta.table if fmeta else "core_performance_indicators_sheet"
        where: list[str] = []
        if intent.years:
            where.append(f"report_year IN ({','.join(str(y) for y in intent.years)})")
        if intent.periods:
            where.append(f"report_period IN ({','.join(chr(39)+p+chr(39) for p in intent.periods)})")
        seen: set[str] = set()
        if fmeta:
            col = fmeta.column
            for f in intent.filters:
                cond = _filter_to_where(f, col, fmeta)
                if cond not in seen:
                    where.append(cond); seen.add(cond)
            if intent.loss_flag:
                cond = f"{col} < 0"
                if cond not in seen:
                    where.append(cond); seen.add(cond)
            where.append(f"{col} IS NOT NULL")
        sql = f"SELECT COUNT(*) AS 数量 FROM {table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return sql, "table"

    # AVG / SUM / MEDIAN：SQLite 无 MEDIAN 内置，近似用 AVG（只是统计意义上的简化）
    assert fmeta is not None
    table, col = fmeta.table, fmeta.column
    sql_agg = {"AVG": f"AVG({col})", "SUM": f"SUM({col})", "MEDIAN": f"AVG({col})"}[agg]
    label = {"AVG": "均值", "SUM": "总和", "MEDIAN": "中位数(近似=AVG)"}[agg]

    where: list[str] = [f"{col} IS NOT NULL"]
    if intent.years:
        where.append(f"report_year IN ({','.join(str(y) for y in intent.years)})")
    if intent.periods:
        where.append(f"report_period IN ({','.join(chr(39)+p+chr(39) for p in intent.periods)})")
    for f in intent.filters:
        where.append(_filter_to_where(f, col, fmeta))
    sql = f"SELECT {sql_agg} AS {col}_{agg.lower()} FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql, "table"


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

    # 聚合单值结果：cols 只有一列 aggregate 结果
    if len(cols) == 1 and len(records) == 1:
        col_name = cols[0]
        v = records[0].get(col_name)
        if col_name == "数量":
            return f"共 {v} 家"
        agg_kind = kind
        label = _value_label(v, agg_kind)
        # 尝试从 col 推断聚合类型
        suffix = ""
        if col_name.endswith("_avg"):
            suffix = "均值"
        elif col_name.endswith("_sum"):
            suffix = "总和"
        elif col_name.endswith("_median"):
            suffix = "中位数"
        return f"{field_zh}{suffix}：{label}"

    # 单点查询
    if intent_type == "query" and len(records) == 1:
        r = records[0]
        comp = r.get("stock_abbr") or ""
        period = _row_period_label(r)
        v = _value_label(r.get(value_col), kind)
        prefix = f"{comp} {period}".strip()
        return (f"{prefix}的{field_zh}为 {v}" if prefix else f"{field_zh}为 {v}")

    # 筛选列表（带 filter 的查询）：列出前 5 家
    if intent_type == "query" and len(records) > 1:
        head = records[: min(5, len(records))]
        items = [f"{r.get('stock_abbr','')}({r.get('stock_code','')}) {_value_label(r.get(value_col), kind)}"
                 for r in head]
        return f"符合条件共 {len(records)} 家，前{len(head)}：" + "; ".join(items)

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
            # 高级规则：多表 JOIN、差值 Top-N、比值分布、散点、CAGR 等
            adv = advanced_rules.try_build(intent, q)
            if adv:
                sql, chart_type = adv
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

        # CAGR 需要 post-process（多行 → 每公司 CAGR）
        if chart_type == "hist_cagr":
            records = advanced_rules.cagr_post_process(records)
            chart_type = "hist"

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
    if limit is not None:
        df = df.head(limit)
    rows = []
    for _, row in tqdm(df.iterrows(), total=len(df), desc="task2"):
        qid = str(row["编号"]).strip()
        qtype = str(row["问题类型"]).strip()
        raw_q = row["问题"]
        try:
            turns = json.loads(raw_q) if isinstance(raw_q, str) else raw_q
        except json.JSONDecodeError:
            turns = [{"Q": str(raw_q)}]
        if isinstance(turns, dict):
            turns = [turns]
        try:
            rows.append(process_question(qid, qtype, turns))
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=2)
            print(f"[task2] {qid} FAILED: {e}\n{tb}")
            rows.append({
                "编号": qid, "问题类型": qtype,
                "问题": json.dumps(turns, ensure_ascii=False),
                "SQL 查询语句": "", "图形格式": "table",
                "回答": json.dumps({"问题编号": qid, "问题类型": qtype, "子问题": [], "error": str(e)},
                                   ensure_ascii=False),
                "图表": "",
            })

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
