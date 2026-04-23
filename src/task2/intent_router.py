"""意图识别：规则侧（主）+ LLM（回落）。

输出结构化意图字典，供 dialogue.py 决定是否需要澄清。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .. import config
from ..llm_client import get_client
from . import prompts
from .field_schema import FIELD_META, all_keywords, canonical_field


@dataclass
class Filter:
    """WHERE 子句元数据：field_zh op value（单位换算在 NL2SQL 阶段做）。"""
    field_zh: str           # 中文字段主名（FIELD_META key），或 None 表示沿用 intent.fields[0]
    op: str                 # ">", "<", ">=", "<=", "=", "<0", ">0", "BETWEEN"
    value: float            # 比较值；单位：字段自身（% 字段即百分比数值，amount 字段即该字段单位）
    unit_hint: str = ""     # "亿元" / "万元" / "%" 等原始单位，NL2SQL 做换算


@dataclass
class Intent:
    intent: str = "query"  # query | trend | rank | compare | clarify
    companies: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)  # Q1 / HY / Q3 / FY
    fields: list[str] = field(default_factory=list)  # 中文主名（FIELD_META key）
    need_clarify: bool = False
    clarify_question: str = ""
    inherit_context: bool = False  # 本轮含代词（"该公司/本期"）时标记
    filters: list[Filter] = field(default_factory=list)  # 数值比较 WHERE
    aggregate: str = ""    # ""（空）/ "AVG" / "COUNT" / "SUM" / "MEDIAN"
    loss_flag: bool = False  # 亏损/为负数 → net_profit < 0


# 报告期汉字映射
_PERIOD_ZH = {
    "一季报": "Q1", "一季度": "Q1", "第一季度": "Q1", "Q1": "Q1",
    "半年报": "HY", "中报": "HY", "半年度": "HY", "上半年": "HY", "HY": "HY",
    "三季报": "Q3", "三季度": "Q3", "第三季度": "Q3", "Q3": "Q3",
    "年报": "FY", "全年": "FY", "年度": "FY", "FY": "FY",
}

# 代词 / 回指
_PRONOUNS = ("该公司", "这家公司", "本公司", "同公司", "上一期", "上期", "本期", "同期", "该指标", "这个指标", "相同的")

# 比较操作关键字 → op
_GT_WORDS = ("超过", "大于", "高于", "多于", "超过了", "大过", ">")
_LT_WORDS = ("低于", "小于", "少于", "不足", "<")
_GTE_WORDS = ("不低于", "至少", "≥", ">=")
_LTE_WORDS = ("不超过", "不高于", "至多", "≤", "<=")

# 常见单位后缀到"万元"的换算倍数（乘以该倍数得到万元）
_UNIT_TO_WAN = {
    "亿元": 10000, "亿": 10000,
    "千万元": 1000, "千万": 1000,
    "百万元": 100, "百万": 100,
    "万元": 1, "万": 1,
    "元": 0.0001,
}

# 聚合关键字
_AGG_AVG = ("均值", "平均值", "平均", "算术平均", "mean")
_AGG_SUM = ("总和", "合计", "总计")
_AGG_COUNT = ("有多少", "多少家", "共有", "数量", "几家")
_AGG_MEDIAN = ("中位数",)

# 亏损语义
_LOSS_WORDS = ("亏钱", "亏损", "净利润为负", "利润为负", "净利润负", "为负的", "负利润")


# 兼容旧代码：导出 FIELD_SYNONYMS 简表（key → column）
FIELD_SYNONYMS: dict[str, str] = {k: v.column for k, v in FIELD_META.items()}


@lru_cache(maxsize=1)
def load_known_abbrs(db_path_str: str = "") -> tuple[str, ...]:
    """从 companies 表加载股票简称；失败时退化为内置样本表。"""
    db_path = Path(db_path_str) if db_path_str else config.DB_PATH
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT DISTINCT stock_abbr FROM companies WHERE stock_abbr IS NOT NULL").fetchall()
        conn.close()
        names = tuple(r[0] for r in rows if r and r[0])
        if names:
            return names
    except sqlite3.Error:
        pass
    return ("金花股份", "华润三九")


def _fuzzy_company_match(q: str, known_abbrs: tuple[str, ...]) -> list[str]:
    """当精确简称未命中时，尝试子串匹配：Q 中 2-4 字短语能唯一对应到某个简称。"""
    hits: list[str] = []
    blocklist = {
        "公司", "企业", "行业", "中药", "上市", "净利", "营收", "营业", "利润",
        "毛利", "现金", "资产", "负债", "收入", "费用", "股份", "制药", "药业",
        "集团", "医药", "药品",
    }
    for tok in re.findall(r"[一-龥]{2,4}", q):
        if tok in blocklist:
            continue
        matches = [a for a in known_abbrs if tok in a]
        if len(matches) == 1 and matches[0] not in hits:
            hits.append(matches[0])
    return hits


def _parse_filters(q: str) -> list[Filter]:
    """解析"超过 X / 低于 X / 大于 X%" 这类数值比较。返回 Filter 列表。

    NL2SQL 阶段会把 filters 套到 intent.fields[0] 或通过关键字定位更合适的字段。
    """
    filters: list[Filter] = []

    # 正则：捕获 <关键字> <数值><单位>（单位可选）
    # 数值支持：整数、小数、百分数
    num = r"(\d+(?:\.\d+)?)"
    unit = r"(亿元|亿|千万元|千万|百万元|百万|万元|万|元|%|个百分点)?"
    gt = "|".join(re.escape(w) for w in _GT_WORDS)
    lt = "|".join(re.escape(w) for w in _LT_WORDS)
    gte = "|".join(re.escape(w) for w in _GTE_WORDS)
    lte = "|".join(re.escape(w) for w in _LTE_WORDS)

    for op, keywords in [(">", gt), ("<", lt), (">=", gte), ("<=", lte)]:
        for m in re.finditer(rf"(?:{keywords})\s*{num}\s*{unit}", q):
            val = float(m.group(1))
            u = m.group(2) or ""
            filters.append(Filter(field_zh="", op=op, value=val, unit_hint=u))

    # "为负数/为负" → 专门的 filter
    if re.search(r"为负(?:数|的)?", q):
        filters.append(Filter(field_zh="", op="<0", value=0.0))
    # "为正" / "正数"
    if re.search(r"为正(?:数|的)?", q):
        filters.append(Filter(field_zh="", op=">0", value=0.0))

    return filters


def _detect_aggregate(q: str) -> str:
    for w in _AGG_AVG:
        if w in q:
            return "AVG"
    for w in _AGG_MEDIAN:
        if w in q:
            return "MEDIAN"
    for w in _AGG_SUM:
        if w in q:
            return "SUM"
    for w in _AGG_COUNT:
        if w in q:
            return "COUNT"
    return ""


def rule_based(question: str) -> Intent:
    q = question
    intent = Intent()
    known = load_known_abbrs()

    # 公司：精确简称
    for a in known:
        if a in q and a not in intent.companies:
            intent.companies.append(a)
    # 股票代码 3-6 位（避免把"200亿/30%/20万元/66家"里的数字当代码）
    for m in re.finditer(r"(?<!\d)(\d{3,6})(?!\d)", q):
        code = m.group(1)
        tail_idx = m.end()
        trailing = q[tail_idx: tail_idx + 2]
        # 紧跟单位/量词/百分号 → 是数值而不是代码
        if trailing and trailing[0] in ("亿", "万", "%", "元", "家", "个", "年", "月", "季", "位", "名", "倍"):
            continue
        if 1900 <= int(code) <= 2100 and len(code) == 4:
            continue
        if code not in intent.companies:
            intent.companies.append(code)
    # 公司：子串模糊匹配（只在精确未命中时）
    if not intent.companies:
        for hit in _fuzzy_company_match(q, known):
            if hit not in intent.companies:
                intent.companies.append(hit)

    # 年份
    for y in re.findall(r"(20\d{2})\s*年", q):
        y_i = int(y)
        if y_i not in intent.years:
            intent.years.append(y_i)
    # 报告期
    for k, v in _PERIOD_ZH.items():
        if k in q and v not in intent.periods:
            intent.periods.append(v)
    # 字段
    for kw in all_keywords():
        if kw in q:
            canon = canonical_field(kw)
            if canon and canon not in intent.fields:
                intent.fields.append(canon)

    # 代词
    if any(p in q for p in _PRONOUNS):
        intent.inherit_context = True

    # 亏损/为负：若问题已指到具体字段（如"经营性现金流量净额为负"）就不要再加 net_profit
    if any(w in q for w in _LOSS_WORDS):
        intent.loss_flag = True
        if not intent.fields:
            intent.fields.append("净利润")
    # "为负数/为负的" 单独触发（更宽松）
    elif re.search(r"为负(?:数|的)?(?!.{0,4}正)", q) or "负数" in q:
        intent.loss_flag = True
        if not intent.fields:
            intent.fields.append("净利润")

    # 数值比较 filters
    intent.filters = _parse_filters(q)

    # 聚合
    intent.aggregate = _detect_aggregate(q)

    # 意图类型
    if any(k in q for k in ["趋势", "变化", "走势", "近几年", "近三年", "近五年", "历年", "每年", "可视化", "折线", "曲线"]):
        intent.intent = "trend"
    elif any(k in q for k in ["top", "Top", "TOP", "前几", "前十", "前三", "前五", "最高", "最低", "排名", "排序"]):
        intent.intent = "rank"
    elif any(k in q for k in ["对比", "相比", "vs", "VS", "比较", "差异"]):
        intent.intent = "compare"
    else:
        intent.intent = "query"

    # 澄清逻辑
    has_aggregate = bool(intent.aggregate)
    has_filters = bool(intent.filters) or intent.loss_flag
    # 多字段 + 比较/差值关键字 = 跨字段/跨表查询，不强求 company
    has_cross_field = len(intent.fields) >= 2 and any(
        w in q for w in ("超过", "大于", "高于", "不一致", "不符", "差值", "差异", "相关", "散点",
                         "分布", "比值", "比例", "比率", "对比", "相比", "前十", "前五", "复合增长率")
    )
    # 分析类意图（分布/散点/对比/复合增长率/相关性）也不强求 company
    has_analytic = any(w in q for w in ("分布", "直方图", "散点", "相关性", "复合增长率", "CAGR"))

    bypass_clarify = has_aggregate or has_filters or has_cross_field or has_analytic

    if intent.intent == "query":
        if not intent.companies and not bypass_clarify:
            intent.need_clarify = True
            intent.clarify_question = "请问要查询哪家公司？"
        elif not intent.fields:
            intent.need_clarify = True
            intent.clarify_question = "请问要查询哪个财务指标？"
    elif intent.intent == "trend" and not intent.companies and not bypass_clarify:
        intent.need_clarify = True
        intent.clarify_question = "请问要分析哪家公司的趋势？"
    return intent


def llm_refine(question: str, base: Intent) -> Intent:
    """若规则识别信心不足或缺信息，交给 LLM 进一步判定。无 LLM 时直接返回 base。"""
    client = get_client()
    if not client.enabled:
        return base
    sys = prompts.INTENT_PROMPT
    usr = f"问题：{question}\n已有规则识别：{base.__dict__}"
    out = client.chat_json([
        {"role": "system", "content": sys},
        {"role": "user", "content": usr},
    ])
    if not out or "intent" not in out:
        return base
    ents = out.get("entities", {}) or {}
    fields = list(base.fields)
    for f in ents.get("field") or []:
        canon = canonical_field(str(f))
        if canon and canon not in fields:
            fields.append(canon)
    return Intent(
        intent=out.get("intent", base.intent),
        companies=list({*base.companies, *(ents.get("company") or [])}),
        years=list({*base.years, *[int(y) for y in (ents.get("year") or []) if str(y).isdigit()]}),
        periods=list({*base.periods, *(ents.get("period") or [])}),
        fields=fields,
        need_clarify=bool(out.get("need_clarify", base.need_clarify)),
        clarify_question=out.get("clarify_question", base.clarify_question) or "",
        inherit_context=base.inherit_context,
        filters=list(base.filters),
        aggregate=base.aggregate,
        loss_flag=base.loss_flag,
    )


def route(question: str, use_llm: bool = True) -> Intent:
    base = rule_based(question)
    if use_llm and base.need_clarify:
        return llm_refine(question, base)
    return base
