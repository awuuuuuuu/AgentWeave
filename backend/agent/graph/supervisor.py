"""
Supervisor 节点: 任务理解与路由

使用结构化输出 (with_structured_output) 决定: 
  - 下一步由哪个 worker 处理
  - 给 worker 的具体任务描述

防护机制: 
  1. 上下文截断: 只取最近 SUPERVISOR_CONTEXT_WINDOW 条消息, 避免历史膨胀拖慢路由
  2. 循环熔断: supervisor_count 达到 MAX_SUPERVISOR_LOOPS 后强制结束, 防止大模型幻觉无限路由
"""
from __future__ import annotations

import logging
import typing
from typing import Any

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .prompts import build_supervisor_system
from .state import MAX_SUPERVISOR_LOOPS, AgentState

logger = logging.getLogger(__name__)

# Supervisor 路由决策时只看最近 N 条消息 (避免长对话后上下文爆炸) 
SUPERVISOR_CONTEXT_WINDOW = 10


def build_supervisor(
    llm_model: str = "gpt-4o",
    agent_cards: list[dict] | None = None
) -> object:
    """构建 Supervisor 节点函数

    agent_cards: 参与路由的 Worker Agent card 列表
    """
    cards = agent_cards or []
    valid_names = [c["name"] for c in cards] + ["__end__"]

    NextType = typing.Union[tuple(typing.Literal[n] for n in valid_names)]

    class _RoutingDecision(BaseModel):
        next: NextType = Field(description="下一步由谁处理") # type: ignore[valid-type]
        task: str = Field(
            description=(
                "给下一个 worker 的任务指令 (中文, 200 字以内 ) 。"
                "必须包含: 核心目标、涉及的关键实体 (时间/对象/技术名词 ) 、对输出的具体要求。"
                "越具体越好, worker 将直接用此字段检索文档或执行分析。"
            )
        )
        reasoning: str = Field(description="路由决策理由 (简短, 用于日志)")

    system_prompt = build_supervisor_system(cards)
    llm = ChatOpenAI(model=llm_model, temperature=0)
    structured_llm = llm.with_structured_output(_RoutingDecision)

    async def supervisor_node(state: AgentState) -> dict:
        messages = state.get("messages", [])
        supervisor_count = state.get("supervisor_count", 0) + 1

        if supervisor_count > MAX_SUPERVISOR_LOOPS:
            logger.warning(
                "Supervisor: 达到最大路由次数 %d, 强制结束", MAX_SUPERVISOR_LOOPS
            )
            return {
                "next_agent": "__end__",
                "task": "已达最大处理轮次, 输出当前最佳结果",
                "supervisor_count": supervisor_count,
            }

        recent = messages[-SUPERVISOR_CONTEXT_WINDOW:]

        # 有记忆上下文时追加到 system prompt
        memory_context = state.get("memory_context", "")
        effective_system = (
            f"{system_prompt}\n\n{memory_context}" if memory_context else system_prompt
        )

        decision: _RoutingDecision = await structured_llm.ainvoke(
            [SystemMessage(content=effective_system), *recent]
        )

        logger.info(
            "Supervisor [%d/%d]: next=%s | task=%r | reason=%r",
            supervisor_count, MAX_SUPERVISOR_LOOPS,
            decision.next, decision.task[:120], decision.reasoning[:80],
        )

        result: dict[str, Any] = {
            "next_agent": decision.next,
            "task": decision.task,
            "supervisor_count": supervisor_count,
        }

        if decision.next == "hitl":
            result["pending_approval"] = {
                "description": decision.task,
                "tool_name": "human_approval_required",
            }

        return result
    
    return supervisor_node