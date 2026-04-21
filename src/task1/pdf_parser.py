"""从单个财报 PDF 抽取四张核心报表的字段值。

主策略：识别报表 section（"合并资产负债表" / "合并利润表" / "合并现金流量表" /
"主要会计数据"），在每个 section 的文本行中按行标签正则匹配字段，
取首个数值列（本报告期/本期末）作为字段值。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber

from . import field_mapper as fm
from ..utils.cn_number import parse_number, to_wan_yuan

NUMBER_TOKEN = re.compile(r"-?\d[\d,]*\.?\d*|-|—")
PCT_TOKEN = re.compile(r"-?\d[\d,]*\.?\d*%")
# 财务负数括号形式：(1,234.56) 或 （1,234.56）
PAREN_NEG_TOKEN = re.compile(r"^[\(（]-?\d[\d,]*\.?\d*[\)）]$")


def _is_number_like(tok: str) -> bool:
    return bool(
        NUMBER_TOKEN.fullmatch(tok)
        or PCT_TOKEN.fullmatch(tok)
        or PAREN_NEG_TOKEN.fullmatch(tok)
    )


@dataclass
class ExtractedRecord:
    stock_code: str = ""
    stock_abbr: str = ""
    core: dict = field(default_factory=dict)
    balance: dict = field(default_factory=dict)
    income: dict = field(default_factory=dict)
    cash_flow: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)  # 表名 -> 命中字段数
    detected_year: Optional[int] = None
    detected_period: Optional[str] = None  # FY | Q1 | HY | Q3
    is_summary: bool = False


# ========================= 内容识别：年度 / 周期 =========================
_PERIOD_PATTERNS = [
    (re.compile(r"(\d{4})\s*年\s*年度\s*报告(摘要)?"), "FY"),
    (re.compile(r"(\d{4})\s*年\s*年报(摘要)?"), "FY"),
    (re.compile(r"(\d{4})\s*年\s*第一季度\s*报告"), "Q1"),
    (re.compile(r"(\d{4})\s*年\s*一季度\s*报告(摘要)?"), "Q1"),
    (re.compile(r"(\d{4})\s*年\s*半年度\s*报告(摘要)?"), "HY"),
    (re.compile(r"(\d{4})\s*年\s*半年报(摘要)?"), "HY"),
    (re.compile(r"(\d{4})\s*年\s*中期\s*报告"), "HY"),
    (re.compile(r"(\d{4})\s*年\s*第三季度\s*报告"), "Q3"),
    (re.compile(r"(\d{4})\s*年\s*三季度\s*报告(摘要)?"), "Q3"),
]


def detect_period_from_text(head_text: str) -> tuple[Optional[int], Optional[str], bool]:
    """从首页文本推断 (report_year, report_period, is_summary)。"""
    for pat, period in _PERIOD_PATTERNS:
        m = pat.search(head_text)
        if m:
            year = int(m.group(1))
            is_sum = bool(m.lastindex and m.lastindex >= 2 and m.group(2))
            return year, period, is_sum
    return None, None, False


# ========================= 行解析 =========================
def split_label_and_numbers(line: str) -> tuple[str, list[str]]:
    """把一行 '行标签 num1 num2 …' 切成 (label, [num_tokens])。

    处理财务行中常见的负数括号、占位符 '-'。
    """
    tokens = line.strip().split()
    nums: list[str] = []
    # 从右往左吃真正的数字 token
    while tokens:
        tail = tokens[-1]
        if _is_number_like(tail) or tail in {"-", "—", "–"}:
            nums.append(tail)
            tokens.pop()
        else:
            break
    nums.reverse()
    label = " ".join(tokens).strip()
    return label, nums


def first_numeric(tokens: list[str]) -> Optional[str]:
    for tok in tokens:
        if NUMBER_TOKEN.fullmatch(tok) or PCT_TOKEN.fullmatch(tok):
            return tok
    return None


# ========================= Section 切片 =========================
SECTION_KEYS = [
    ("core", ["主要会计数据和财务指标", "主要会计数据", "主要财务数据和财务指标"]),
    ("balance", ["合并资产负债表"]),
    ("income", ["合并利润表"]),
    ("cash_flow", ["合并现金流量表"]),
]

# 下一个 section 常见的开始，用于截断
STOP_MARKERS = [
    "母公司资产负债表",
    "母公司利润表",
    "母公司现金流量表",
    "公司股东数量",
    "合并所有者权益变动表",
    "合并现金流量表补充资料",
    "第十一节",
    "第七节",
]


def pick_sections(pages_text: list[str]) -> dict[str, str]:
    """基于页文本拼接，定位每个 section 的文本块。
    找到起点后向后累计页文本直到遇到下一个 section 起点或停止符。
    """
    # 段起始页索引
    starts: list[tuple[int, str]] = []
    for idx, text in enumerate(pages_text):
        for key, markers in SECTION_KEYS:
            for m in markers:
                if m in text and (key, idx) not in [(k, i) for i, (k_, _m) in enumerate(starts) if k_ == key]:
                    starts.append((idx, key))
                    break
    # 去重保留每个 section 第一次出现
    seen: dict[str, int] = {}
    for idx, key in starts:
        seen.setdefault(key, idx)

    result: dict[str, str] = {}
    sorted_starts = sorted(seen.items(), key=lambda x: x[1])  # [(key, page_idx), ...]
    page_indices = [p for _, p in sorted_starts]

    for i, (key, page_idx) in enumerate(sorted_starts):
        end = page_indices[i + 1] if i + 1 < len(sorted_starts) else len(pages_text)
        buf = []
        for j in range(page_idx, min(end + 2, len(pages_text))):
            txt = pages_text[j]
            # 若 block 命中 stop marker，切到之前
            for sm in STOP_MARKERS:
                if sm in txt and sm not in (pages_text[page_idx]):
                    # 截到 stop marker 之前那一页
                    end = j
                    break
            if j >= end and j > page_idx:
                break
            buf.append(txt)
        result[key] = "\n".join(buf)
    return result


# ========================= 单元解析 =========================
def parse_section(
    section_text: str,
    rules: list[fm.FieldRule],
    unit_hint: str = "元",
) -> dict[str, Optional[float]]:
    """对 section 文本逐行匹配，产生 {field_en: value} 字典。
    对于 % 类字段保持百分比小数（如 56.48 表示 56.48%）。
    对于金额类字段统一换算到 万元。
    """
    out: dict[str, Optional[float]] = {}
    for line in section_text.splitlines():
        label, nums = split_label_and_numbers(line)
        if not label or not nums:
            continue
        rule = fm.match_field(label, rules)
        if rule is None:
            continue
        if rule.field in out:
            continue  # 保留首次匹配（通常合并报表在前）
        raw = first_numeric(nums)
        if raw is None:
            continue
        if rule.unit == "%":
            v = parse_number(raw)
            if v is not None:
                out[rule.field] = round(v, 4)
        else:
            out[rule.field] = to_wan_yuan(raw, src_unit=rule.unit)
    return out


# ========================= 对外入口 =========================
def parse_pdf(pdf_path: Path) -> ExtractedRecord:
    rec = ExtractedRecord()
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = [p.extract_text() or "" for p in pdf.pages]
    except Exception as e:
        print(f"[parser] FAILED to open {pdf_path.name}: {e}")
        return rec

    # 识别真实年度 / 报告期（首页标题最可靠）
    head = "\n".join(pages_text[:2])
    y, p, summary = detect_period_from_text(head)
    rec.detected_year = y
    rec.detected_period = p
    rec.is_summary = summary

    sections = pick_sections(pages_text)

    if "core" in sections:
        rec.core = parse_section(sections["core"], fm.CORE_RULES)
        _post_core(rec.core)
    if "balance" in sections:
        rec.balance = parse_section(sections["balance"], fm.BALANCE_RULES)
    if "income" in sections:
        rec.income = parse_section(sections["income"], fm.INCOME_RULES)
    if "cash_flow" in sections:
        rec.cash_flow = parse_section(sections["cash_flow"], fm.CASHFLOW_RULES)

    rec.coverage = {
        "core": len(rec.core),
        "balance": len(rec.balance),
        "income": len(rec.income),
        "cash_flow": len(rec.cash_flow),
    }
    return rec


def _post_core(core: dict) -> None:
    """清洗核心指标：部分字段来源于辅助 label（_src 后缀），需要迁移。"""
    if "equity_total_equity_src" in core:
        core.pop("equity_total_equity_src", None)
    if "operating_cf_per_share_src" in core:
        core.pop("operating_cf_per_share_src", None)
