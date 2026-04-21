"""中文财务数字解析：处理"1,234.56"、"-（12.34）"、"16.64亿"、"1234万元"等格式。
统一输出到 **万元** 单位（decimal 小数）。"""
from __future__ import annotations

import re
from typing import Optional

_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def parse_number(raw: object) -> Optional[float]:
    """解析单元格原值为浮点数；失败返回 None。不做单位换算。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s or s in {"-", "—", "–", "N/A", "NA", "不适用", "/"}:
        return None
    # 财务负数: (1,234) 或 （1,234）
    neg = False
    if (s.startswith("(") and s.endswith(")")) or (s.startswith("（") and s.endswith("）")):
        neg = True
        s = s[1:-1]
    s = s.replace(",", "").replace(" ", "").replace(" ", "")
    # 百分号
    if s.endswith("%"):
        try:
            v = float(s[:-1])
            return -v if neg else v
        except ValueError:
            return None
    m = _NUM_RE.fullmatch(s)
    if not m:
        # 亿 / 万 后缀
        mul = 1.0
        if s.endswith("亿"):
            mul = 1e8
            s = s[:-1]
        elif s.endswith("万"):
            mul = 1e4
            s = s[:-1]
        elif s.endswith("万元"):
            mul = 1e4
            s = s[:-2]
        elif s.endswith("亿元"):
            mul = 1e8
            s = s[:-2]
        elif s.endswith("元"):
            s = s[:-1]
        try:
            v = float(s) * mul
            return -v if neg else v
        except ValueError:
            return None
    try:
        v = float(m.group())
        return -v if neg else v
    except ValueError:
        return None


def to_wan_yuan(raw: object, src_unit: str = "元") -> Optional[float]:
    """把原值换算到万元。src_unit ∈ {'元','万元','亿元','百万元'}。"""
    v = parse_number(raw)
    if v is None:
        return None
    u = src_unit.strip()
    if u == "元":
        return round(v / 1e4, 2)
    if u == "万元":
        return round(v, 2)
    if u == "百万元":
        return round(v * 1e2, 2)
    if u == "亿元":
        return round(v * 1e4, 2)
    return round(v / 1e4, 2)


def detect_unit(header_text: str) -> str:
    """根据报表表头的"单位：元/万元/亿元"判断源单位。"""
    if not header_text:
        return "元"
    t = str(header_text)
    if "亿元" in t:
        return "亿元"
    if "百万元" in t:
        return "百万元"
    if "万元" in t:
        return "万元"
    if "元" in t:
        return "元"
    return "元"
