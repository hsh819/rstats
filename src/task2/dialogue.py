"""多轮对话协调：维护 session state，按需澄清。

设计要点：
- `SessionState` 累积本会话内的公司 / 年份 / 报告期 / 字段；
- `step()` 每轮合并本轮意图到 state，再用 state 覆盖 intent，以实现多轮上下文继承；
- 若上一轮 `need_clarify`，本轮视作"补充信息"而非独立新问题，保留上一轮 intent 类型；
- 本轮 intent 若只给了 period 或 year（典型第二轮补全），使用 state 里最近的 field 与 company。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import intent_router


@dataclass
class SessionState:
    companies: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    raw_turns: list[dict] = field(default_factory=list)
    last_intent_type: str = ""          # 上一轮最终意图类型，供继承
    last_need_clarify: bool = False     # 上一轮是否触发过澄清

    def merge(self, intent: intent_router.Intent) -> None:
        for c in intent.companies:
            if c not in self.companies:
                self.companies.append(c)
        for y in intent.years:
            if y not in self.years:
                self.years.append(y)
        for p in intent.periods:
            if p not in self.periods:
                self.periods.append(p)
        for f in intent.fields:
            if f not in self.fields:
                self.fields.append(f)


def step(state: SessionState, question: str) -> intent_router.Intent:
    """推进一步：返回本轮意图（可能需要澄清，也可能信息已齐）。"""
    state.raw_turns.append({"role": "user", "text": question})
    intent = intent_router.route(question, use_llm=False)

    # 多轮继承规则：
    # 1. 本轮含代词（"该公司/本期"）或上一轮刚澄清过 → 沿用上一轮 intent 类型。
    # 2. 本轮若完全没提字段，但 state 已有 → 使用 state 最近一个字段。
    # 3. 本轮若只新增 period/year 而没动 company，仍看作"对同公司的追问"。
    inherit_type = intent.inherit_context or state.last_need_clarify
    had_only_period_or_year = (
        not intent.fields and not intent.companies and (intent.periods or intent.years)
    )
    if inherit_type or had_only_period_or_year:
        if state.last_intent_type and state.last_intent_type not in ("", "clarify"):
            intent.intent = state.last_intent_type

    state.merge(intent)

    # 用 state 覆盖回 intent
    intent.companies = list(state.companies)
    intent.years = list(state.years)
    intent.periods = list(state.periods)
    intent.fields = list(state.fields)

    # 二次判断是否仍需澄清
    if intent.intent == "query" and intent.companies and intent.fields:
        intent.need_clarify = False
        intent.clarify_question = ""
    elif intent.intent == "trend" and intent.companies and intent.fields:
        intent.need_clarify = False
        intent.clarify_question = ""
    elif intent.intent in ("rank", "compare"):
        intent.need_clarify = False
        intent.clarify_question = ""

    state.last_intent_type = intent.intent
    state.last_need_clarify = bool(intent.need_clarify)
    return intent
