"""任务三入口：附件6 多意图问题 → 规划 → 结构化查询 + RAG 归因 → result_3.xlsx。

回答 JSON（附件7 表5）：
{
  "问题编号": ...,
  "问题类型": ...,
  "结构化结果": [ {"子任务","意图","Q","SQL","图形格式","结果数据","image"} ],
  "references": [ {"paper_path","paper_title","page","text","paper_image"} ]
}
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import pandas as pd

from .. import config
from ..utils.excel_io import write_task_results_with_images
from ..utils.period import period_label
from ..task2 import answer_q4 as t2, intent_router, sql_runner
from . import attribution, planner, rag_index

RESULT_FILE = config.RESULT_DIR / "result_3.xlsx"


def _run_structured(question: str) -> tuple[str, str, list[dict]]:
    """结构化子任务：复用任务二的意图 + NL→SQL 链路。返回 (sql, chart_fmt, records)。"""
    intent = intent_router.route(question, use_llm=False)
    sql, chart_fmt = t2.llm_nl2sql(question, intent)
    if not sql:
        sql, chart_fmt = t2.rule_nl2sql(intent)
    if not sql:
        return "", chart_fmt, []
    try:
        cols, rows = sql_runner.execute(config.DB_PATH, sql)
    except Exception as e:
        print(f"[task3] sql fail: {e}")
        return sql, chart_fmt, []
    records = sql_runner.rows_to_records(cols, rows)
    return sql, chart_fmt, records


def _plot_if_any(records: list[dict], chart_fmt: str, qid: str, img_idx: int, title_q: str) -> tuple[str, list[dict]]:
    """给结构化结果出图；返回 (image_name, records_with_period)。"""
    if not records:
        return "", records
    from ..task2.chart import auto_plot

    keys = list(records[0].keys())
    y_field = keys[-1]
    x_field = "report_year" if "report_year" in keys else keys[0]
    if "report_year" in keys and "report_period" in keys:
        for r in records:
            try:
                r["_period"] = period_label(int(r.get("report_year") or 0),
                                            str(r.get("report_period") or ""))
            except Exception:
                r["_period"] = f"{r.get('report_year')}{r.get('report_period')}"
        x_field = "_period"
    elif "stock_abbr" in keys:
        x_field = "stock_abbr"
    series = "stock_abbr" if "stock_abbr" in keys and chart_fmt == "line" else None

    img_path = config.RESULT_DIR / f"{qid}_{img_idx}.jpg"
    try:
        auto_plot(records, chart_fmt, f"{qid} - {title_q[:24]}", img_path, x_field, y_field, series)
        return img_path.name, records
    except Exception as e:
        print(f"[task3] chart fail: {e}")
        return "", records


def process_question(qid: str, qtype: str, turns: list[dict], retriever: rag_index.Retriever) -> dict:
    structured_results: list[dict] = []
    all_refs: list[dict] = []
    last_sql = ""
    last_chart_fmt = "table"
    img_idx = 0
    company_hint: Optional[str] = None
    last_image_name = ""
    last_paper_image = ""

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
                img_idx += 1
                img_name, records = _plot_if_any(records, chart_fmt, qid, img_idx, st.query)
                if img_name:
                    last_image_name = img_name
                structured_results.append({
                    "子任务": st.id,
                    "意图": st.intent,
                    "Q": st.query,
                    "SQL": sql,
                    "图形格式": chart_fmt,
                    "结果数据": records[:10],
                    "image": img_name,
                })
                # 若 DB 查询空，自动追加一个 attribution 子任务（RAG 补位）
                if not records:
                    subtasks.append(planner.SubTask(
                        id=f"{st.id}_rag", intent="attribution", query=st.query, depends_on=[st.id],
                    ))
                for r in records:
                    if "stock_abbr" in r and r.get("stock_abbr"):
                        company_hint = str(r["stock_abbr"])
                        break
            elif st.intent == "attribution":
                intent = intent_router.route(st.query, use_llm=False)
                summary, refs = attribution.attribute(
                    retriever, st.query,
                    filter_stock=company_hint,
                    intent_fields=intent.fields,
                )
                structured_results.append({
                    "子任务": st.id,
                    "意图": "attribution",
                    "Q": st.query,
                    "答案": summary,
                })
                for r in refs:
                    all_refs.append(r)
                    if not last_paper_image and r.get("paper_image"):
                        last_paper_image = r["paper_image"]

    answer_json = {
        "问题编号": qid,
        "问题类型": qtype,
        "结构化结果": structured_results,
        "references": all_refs,
    }
    return {
        "编号": qid,
        "问题类型": qtype,
        "问题": json.dumps([{"Q": t.get("Q")} for t in turns], ensure_ascii=False),
        "SQL 查询语句": last_sql,
        "图形格式": last_chart_fmt,
        "回答": json.dumps(answer_json, ensure_ascii=False, indent=2),
        "图表": last_image_name,
        "研报截图": last_paper_image,
    }


def main(limit: Optional[int] = None):
    retriever = rag_index.load_or_build(force=True)  # 本轮 chunk 结构变了，强制重建
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

    write_task_results_with_images(
        RESULT_FILE,
        rows=rows,
        columns=["编号", "问题类型", "问题", "SQL 查询语句", "图形格式", "回答", "图表", "研报截图"],
        image_col="图表",
        image_dir=config.RESULT_DIR,
        sheet_name="task3",
        extra_image_cols={"研报截图": "研报截图"},
    )
    print(f"[task3] 写入 {RESULT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    main(args.limit)
