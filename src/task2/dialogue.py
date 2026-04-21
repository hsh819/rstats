"""多轮对话协调：维护 session state，按需澄清。"""
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
    raw_turns: list[dict] = field(default_factory=list)  # [{"role":..., "text":...}]

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
    state.merge(intent)
    # 用 state 覆盖回 intent，信息累积后重新判断是否仍需澄清
    intent.companies = list(state.companies)
    intent.years = list(state.years)
    intent.periods = list(state.periods)
    intent.fields = list(state.fields)
    # 二次判断
    if intent.intent == "query" and intent.companies and intent.fields:
        intent.need_clarify = False
        intent.clarify_question = ""
    elif intent.intent == "trend" and intent.companies and intent.fields:
        intent.need_clarify = False
        intent.clarify_question = ""
    elif intent.intent in ("rank", "compare"):
        intent.need_clarify = False
        intent.clarify_question = ""
    return intent
