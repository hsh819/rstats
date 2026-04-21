"""意图识别：规则侧（主）+ LLM（回落）。

输出结构化意图字典，供 dialogue.py 决定是否需要澄清。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from ..llm_client import get_client
from . import prompts


@dataclass
class Intent:
    intent: str = "query"  # query | trend | rank | compare | clarify
    companies: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)  # Q1 / HY / Q3 / FY
    fields: list[str] = field(default_factory=list)  # 中文关键字（"利润总额"）
    need_clarify: bool = False
    clarify_question: str = ""


# 报告期汉字映射
_PERIOD_ZH = {
    "一季": "Q1", "Q1": "Q1", "第一季度": "Q1",
    "半年": "HY", "中报": "HY", "HY": "HY",
    "三季": "Q3", "Q3": "Q3", "第三季度": "Q3",
    "年报": "FY", "全年": "FY", "年度": "FY", "FY": "FY",
}
# 常用字段（中文 → 英文，便于 NL2SQL 检索）
FIELD_SYNONYMS = {
    "利润总额": "total_profit",
    "净利润": "net_profit",
    "营业总收入": "total_operating_revenue",
    "营业收入": "total_operating_revenue",
    "主营业务收入": "total_operating_revenue",
    "毛利率": "gross_profit_margin",
    "净利率": "net_profit_margin",
    "每股收益": "eps",
    "ROE": "roe",
    "净资产收益率": "roe",
    "总资产": "asset_total_assets",
    "总负债": "liability_total_liabilities",
    "经营活动现金流": "operating_cf_net_amount",
    "研发费用": "operating_expense_rnd_expenses",
    "资产负债率": "asset_liability_ratio",
    "所有者权益": "equity_total_equity",
}

# 股票简称样例
KNOWN_ABBRS = ["金花股份", "华润三九"]


def rule_based(question: str) -> Intent:
    q = question
    intent = Intent()
    # 公司
    for a in KNOWN_ABBRS:
        if a in q:
            intent.companies.append(a)
    m = re.findall(r"(\d{6})", q)
    intent.companies.extend(m)
    # 年份
    for y in re.findall(r"(20\d{2})\s*年", q):
        intent.years.append(int(y))
    # 报告期
    for k, v in _PERIOD_ZH.items():
        if k in q and v not in intent.periods:
            intent.periods.append(v)
    # 字段
    for kw in FIELD_SYNONYMS:
        if kw in q and kw not in intent.fields:
            intent.fields.append(kw)

    # 意图类型
    if any(k in q for k in ["趋势", "变化", "走势", "近几年", "近三年", "历年"]):
        intent.intent = "trend"
    elif any(k in q for k in ["top", "Top", "TOP", "前几", "最高", "最低", "排名"]):
        intent.intent = "rank"
    elif any(k in q for k in ["对比", "相比", "vs", "与……相比", "比谁"]):
        intent.intent = "compare"
    else:
        intent.intent = "query"

    # 澄清逻辑
    if intent.intent == "query":
        if not intent.companies:
            intent.need_clarify = True
            intent.clarify_question = "请问要查询哪家公司？"
        elif not intent.fields:
            intent.need_clarify = True
            intent.clarify_question = "请问要查询哪个财务指标？"
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
    return Intent(
        intent=out.get("intent", base.intent),
        companies=list({*base.companies, *(ents.get("company") or [])}),
        years=list({*base.years, *[int(y) for y in (ents.get("year") or []) if str(y).isdigit()]}),
        periods=list({*base.periods, *(ents.get("period") or [])}),
        fields=list({*base.fields, *(ents.get("field") or [])}),
        need_clarify=bool(out.get("need_clarify", base.need_clarify)),
        clarify_question=out.get("clarify_question", base.clarify_question) or "",
    )


def route(question: str, use_llm: bool = True) -> Intent:
    base = rule_based(question)
    if use_llm and base.need_clarify:
        return llm_refine(question, base)
    return base
