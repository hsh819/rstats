"""SQL 白名单执行器。只允许单条 SELECT，禁止 DDL/DML 与危险函数。"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

BANNED = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|create|pragma|vacuum|replace|truncate|exec)\b",
    re.IGNORECASE,
)
ALLOWED_TABLES = {
    "companies",
    "core_performance_indicators_sheet",
    "balance_sheet",
    "income_sheet",
    "cash_flow_sheet",
}


class UnsafeSQLError(ValueError):
    pass


def validate_sql(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if not s.lower().startswith("select") and not s.lower().startswith("with"):
        raise UnsafeSQLError("仅允许 SELECT 查询")
    if ";" in s:
        raise UnsafeSQLError("禁止多条 SQL")
    if BANNED.search(s):
        raise UnsafeSQLError("SQL 包含禁用关键字")
    return s


def execute(db_path: Path, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    s = validate_sql(sql)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(s)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    finally:
        conn.close()
    return cols, rows


def rows_to_records(cols: list[str], rows: list[tuple]) -> list[dict]:
    return [dict(zip(cols, r)) for r in rows]
