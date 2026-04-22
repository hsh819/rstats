"""多意图规划：把一条用户问题拆成若干子任务。

offline 模式（无 LLM）：用关键字规则拆分；
online 模式：LLM 按 JSON schema 输出子任务列表。

规则增强：
- 含"原因/为什么/归因/驱动" → [query/trend, attribution]
- 含"TOP/排名/最高/最低" → [rank]，若同时含"同比/涨幅最大/降幅最大" → [rank, compare]
- 含"趋势/近几年/变化" → [trend]
- 其他 → [query]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..llm_client import get_client


@dataclass
class SubTask:
    id: str
    intent: str  # query | trend | rank | compare | attribution
    query: str
    depends_on: list[str] = field(default_factory=list)


_KEYWORDS_ATTR = ("原因", "为什么", "归因", "驱动", "推动", "受益于")
_KEYWORDS_RANK = ("top", "Top", "TOP", "排名", "排序", "最高", "最低", "第一", "前十", "前五", "前三")
_KEYWORDS_TREND = ("趋势", "变化", "近几年", "近三年", "近五年", "历年", "每年", "可视化", "折线")
_KEYWORDS_DELTA = ("同比", "环比", "涨幅最大", "降幅最大", "增速最快", "增幅", "下降最多", "上升最多")


def rule_plan(question: str) -> list[SubTask]:
    tasks: list[SubTask] = []

    has_attr = any(k in question for k in _KEYWORDS_ATTR)
    has_rank = any(k in question for k in _KEYWORDS_RANK)
    has_trend = any(k in question for k in _KEYWORDS_TREND)
    has_delta = any(k in question for k in _KEYWORDS_DELTA)

    next_id = [0]

    def new_id() -> str:
        next_id[0] += 1
        return f"t{next_id[0]}"

    if has_rank:
        rank_id = new_id()
        tasks.append(SubTask(id=rank_id, intent="rank", query=question))
        if has_delta:
            tasks.append(SubTask(id=new_id(), intent="compare", query=question, depends_on=[rank_id]))
    elif has_trend:
        trend_id = new_id()
        tasks.append(SubTask(id=trend_id, intent="trend", query=question))
        if has_attr:
            tasks.append(SubTask(id=new_id(), intent="attribution", query=question, depends_on=[trend_id]))
    elif has_attr:
        structured = question.split("原因")[0].strip("。，,")
        q_id = new_id()
        tasks.append(SubTask(id=q_id, intent="query", query=structured or question))
        tasks.append(SubTask(id=new_id(), intent="attribution", query=question, depends_on=[q_id]))
    else:
        tasks.append(SubTask(id=new_id(), intent="query", query=question))
    return tasks


def llm_plan(question: str) -> Optional[list[SubTask]]:
    client = get_client()
    if not client.enabled:
        return None
    out = client.chat_json([
        {"role": "system", "content": (
            "把用户问题拆成多个子任务，返回 JSON：{\"subtasks\":[{\"id\":\"t1\","
            "\"intent\":\"query|trend|rank|compare|attribution\",\"query\":\"…\","
            "\"depends_on\":[]}]}。若问题含'原因/为什么'，拆成 'query/trend' + 'attribution' 两步。"
        )},
        {"role": "user", "content": question},
    ])
    if not out or "subtasks" not in out:
        return None
    return [SubTask(**t) for t in out["subtasks"]]


def plan(question: str) -> list[SubTask]:
    return llm_plan(question) or rule_plan(question)
