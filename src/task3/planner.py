"""多意图规划：把一条用户问题拆成若干子任务。

offline 模式（无 LLM）：用关键字规则拆分；
online 模式：LLM 按 JSON schema 输出子任务列表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..llm_client import get_client


@dataclass
class SubTask:
    id: str
    intent: str  # query | trend | rank | attribution
    query: str
    depends_on: list[str] = field(default_factory=list)


_KEYWORDS_ATTR = ("原因", "为什么", "归因", "驱动", "推动")


def rule_plan(question: str) -> list[SubTask]:
    tasks: list[SubTask] = []
    # 简单启发式：若问题含"原因/为什么"，拆成 1) 查询 + 2) 归因
    if any(k in question for k in _KEYWORDS_ATTR):
        tasks.append(SubTask(id="t1", intent="query", query=question.split("原因")[0].strip("。，,")))
        tasks.append(SubTask(id="t2", intent="attribution", query=question, depends_on=["t1"]))
    elif any(k in question for k in ("top", "Top", "TOP", "排名", "最高", "最低")):
        tasks.append(SubTask(id="t1", intent="rank", query=question))
    elif any(k in question for k in ("趋势", "变化", "近几年", "近三年", "历年", "可视化")):
        tasks.append(SubTask(id="t1", intent="trend", query=question))
    else:
        tasks.append(SubTask(id="t1", intent="query", query=question))
    return tasks


def llm_plan(question: str) -> Optional[list[SubTask]]:
    client = get_client()
    if not client.enabled:
        return None
    out = client.chat_json([
        {"role": "system", "content": (
            "把用户问题拆成多个子任务，返回 JSON：{\"subtasks\":[{\"id\":\"t1\",\"intent\":\"query|trend|rank|attribution\",\"query\":\"…\",\"depends_on\":[]}]}"
        )},
        {"role": "user", "content": question},
    ])
    if not out or "subtasks" not in out:
        return None
    return [SubTask(**t) for t in out["subtasks"]]


def plan(question: str) -> list[SubTask]:
    return llm_plan(question) or rule_plan(question)
