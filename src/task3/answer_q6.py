"""任务三入口：附件6 多意图问题 → 规划 → 结构化查询 + RAG 归因 → result_3.xlsx。

回答 JSON 遵循附件7 表5：
{
  "问题编号": ..., "问题类型": ...,
  "结构化结果": [...],
  "references": [{"paper_path","paper_title","page","text","paper_image"}]
}
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import pandas as pd

from .. import config
from ..utils.excel_io import write_task_results
from ..utils.period import period_label
from ..task2 import answer_q4 as t2, intent_router, sql_runner
from . import attribution, planner, rag_index

RESULT_FILE = config.RESULT_DIR / "result_3.xlsx"


def _run_structured(question: str) -> tuple[str, str, list[dict]]:
    """对结构化子任务：复用任务二的意图 + NL→SQL 链路。返回 (sql, chart_fmt, records)。"""
    intent = intent_router.route(question, use_llm=False)
    sql, chart_fmt = t2.llm_nl2sql(question, intent)
    if not sql:
        sql, chart_fmt = t2.rule_nl2sql(intent)
    if not sql:
        return "", chart_fmt, []
    cols, rows = sql_runner.execute(config.DB_PATH, sql)
    records = sql_runner.rows_to_records(cols, rows)
    return sql, chart_fmt, records


def process_question(qid: str, qtype: str, turns: list[dict], retriever: rag_index.Retriever) -> dict:
    sub_answers: list[dict] = []
    all_refs: list[dict] = []
    last_sql = ""
    last_chart_fmt = "table"
    img_idx = 0
    company_hint = None  # 用于 RAG 过滤

    for turn in turns:
        q = str(turn.get("Q", "")).strip()
        if not q:
            continue
        subtasks = planner.plan(q)
        for st in subtasks:
            if st.intent in ("query", "trend", "rank", "compare"):
                sql, chart_fmt, records = _run_structured(st.query)
                last_sql = sql or last_sql
                last_chart_fmt = chart_fmt
                # 出图
                if records:
                    img_idx += 1
                    from ..task2.chart import auto_plot

                    img_path = config.RESULT_DIR / f"{qid}_{img_idx}.jpg"
                    try:
                        y_field = list(records[0].keys())[-1]  # 真数值列（添加 _period 前）
                        x_field = "report_year"
                        if "report_year" in records[0] and "report_period" in records[0]:
                            for r in records:
                                r["_period"] = period_label(r.get("report_year") or 0, str(r.get("report_period") or ""))
                            x_field = "_period"
                        series = "stock_abbr" if "stock_abbr" in records[0] else None
                        auto_plot(records, chart_fmt, f"{qid} - {st.query[:24]}", img_path, x_field, y_field, series)
                    except Exception as e:
                        print(f"[task3] chart fail: {e}")
                sub_answers.append({
                    "子任务": st.id,
                    "意图": st.intent,
                    "Q": st.query,
                    "SQL": sql,
                    "结构化结果": records[:10],
                })
                # 记录公司提示
                for r in records:
                    if "stock_abbr" in r:
                        company_hint = r["stock_abbr"]
                        break
            elif st.intent == "attribution":
                summary, refs = attribution.attribute(retriever, st.query, filter_stock=company_hint)
                sub_answers.append({
                    "子任务": st.id,
                    "意图": "attribution",
                    "Q": st.query,
                    "答案": summary,
                })
                all_refs.extend(refs)

    answer_json = {
        "问题编号": qid,
        "问题类型": qtype,
        "子任务结果": sub_answers,
        "references": all_refs,
    }
    return {
        "编号": qid,
        "问题": json.dumps([{"Q": t.get("Q")} for t in turns], ensure_ascii=False),
        "SQL 查询语句": last_sql,
        "回答": json.dumps(answer_json, ensure_ascii=False, indent=2),
    }


def main(limit: Optional[int] = None):
    retriever = rag_index.load_or_build()
    df = pd.read_excel(config.FILE_Q_TASK3)
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
        print(f"[task3] {qid} {qtype}: {len(turns)} turns")
        rows.append(process_question(qid, qtype, turns, retriever))

    write_task_results(
        RESULT_FILE,
        rows=rows,
        columns=["编号", "问题", "SQL 查询语句", "回答"],
        sheet_name="task3",
    )
    print(f"[task3] 写入 {RESULT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    main(args.limit)
