"""Excel I/O helpers — 保留多行 JSON 列，避免 pandas to_excel 默认行高压扁。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import openpyxl
from openpyxl.styles import Alignment


def write_task_results(
    path: Path,
    rows: Iterable[dict],
    columns: list[str],
    sheet_name: str = "Sheet1",
) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(columns)
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        cells = [row.get(c, "") for c in columns]
        ws.append([_serialize(v) for v in cells])
        for col_idx, v in enumerate(cells, start=1):
            ws.cell(row=ws.max_row, column=col_idx).alignment = wrap
    # 合理列宽
    widths = {"编号": 10, "问题类型": 14, "问题": 40, "SQL 查询语句": 60, "SQL查询语法": 60, "图形格式": 8, "回答": 80}
    for col_idx, col in enumerate(columns, start=1):
        letter = openpyxl.utils.get_column_letter(col_idx)
        ws.column_dimensions[letter].width = widths.get(col, 20)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _serialize(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, indent=2)
    return v if v is not None else ""
