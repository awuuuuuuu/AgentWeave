"""
Supervisor 节点: 任务理解与路由

使用结构化输出 (with_structured_output) 决定:
  - 下一步由哪个 worker 处理（next）
  - 当前 worker 的具体任务指令（current_task）
  - 首轮向用户展示的自然语言说明（message_to_user）

路由可靠性由代码层（route_from_supervisor）保证，不依赖 LLM 记住计划。

防护机制:
  1. 上下文截断: 只取最近 SUPERVISOR_CONTEXT_WINDOW 条消息
  2. 循环熔断: supervisor_count 达到 MAX_SUPERVISOR_LOOPS 后强制结束
"""
from __future__ import annotations

import logging
import typing
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from .prompts import build_supervisor_system
from .state import MAX_SUPERVISOR_LOOPS, AgentState

logger = logging.getLogger(__name__)

SUPERVISOR_CONTEXT_WINDOW = 10


def build_supervisor(
    llm_model: str = "gpt-4o",
    agent_cards: list[dict] | None = None
) -> object:
    cards = agent_cards or []
    valid_names = [c["name"] for c in cards] + ["__end__"]

    NextType = typing.Union[tuple(typing.Literal[n] for n in valid_names)]

    class _RoutingDecision(BaseModel):
        model_config = ConfigDict(extra="forbid")

        next: NextType = Field(  # type: ignore[valid-type]
            description="当前立刻要激活的专家名称。任务已完成则填 '__end__'"
        )
        current_task: str = Field(
            description=(
                "发给当前 next 专家的具体指令（中文，200 字以内）。"
                "只描述需要该专家完成的子任务，不要包含其他专家的职责或整体最终目标。"
                "例如给 Researcher 的指令只写《检索 X 数据》，不要写《数据将用于生成报告并发送》。"
                "必须包含：核心目标、涉及的关键实体、对输出的具体要求。"
            )
        )
        message_to_user: str = Field(
            default="",
            description=(
                "向用户展示的 Supervisor 自然语言回复（中文）。\n"
                "首轮（supervisor_count=1）：多段格式，首行任务意图 + 每专家一段 @专家名说明（含 @Critic）。\n"
                "后续轮次路由给某专家：仅一行，@专家名 + 简短过渡说明，例如 '@Critic 检索完成，请开始评审。'\n"
                "路由到 __end__ 时：填空字符串。"
            ),
        )
        reasoning: str = Field(description="路由决策理由（简短，用于日志）")

    system_prompt = build_supervisor_system(cards)
    llm = ChatOpenAI(model=llm_model, temperature=0)
    structured_llm = llm.with_structured_output(_RoutingDecision)

    async def supervisor_node(state: AgentState) -> dict:
        messages = state.get("messages", [])
        supervisor_count = state.get("supervisor_count", 0) + 1

        if supervisor_count > MAX_SUPERVISOR_LOOPS:
            logger.warning("Supervisor: 达到最大路由次数 %d, 强制结束", MAX_SUPERVISOR_LOOPS)
            return {
                "next_agent": "__end__",
                "task": "已达最大处理轮次，输出当前最佳结果",
                "supervisor_count": supervisor_count,
            }

        recent = messages[-SUPERVISOR_CONTEXT_WINDOW:]

        # 构建 system prompt
        effective_system = system_prompt

        # 注入轮次信息，让 LLM 知道当前是第几轮（影响 message_to_user 格式）
        effective_system += f"\n\n[当前轮次] supervisor_count={supervisor_count}"

        # 注入记忆上下文（历史背景，不代表已回答）
        memory_context = state.get("memory_context", "")
        if memory_context:
            effective_system += (
                f"\n\n---\n"
                f"[历史背景参考（仅供路由参考，必须路由给专家处理，禁止直接输出 __end__）]\n"
                f"{memory_context}\n---"
            )

        decision: _RoutingDecision = await structured_llm.ainvoke(
            [SystemMessage(content=effective_system), *recent]
        )

        logger.info(
            "Supervisor [%d/%d]: next=%s | reason=%r",
            supervisor_count, MAX_SUPERVISOR_LOOPS,
            decision.next,
            decision.reasoning[:80],
        )

        # 有内容就展示（首轮完整分配 + 后续轮次单行过渡）；__end__ 时 LLM 填空字符串
        effective_message = decision.message_to_user.strip()

        result: dict[str, Any] = {
            "next_agent": decision.next,
            "task": decision.current_task,
            "supervisor_count": supervisor_count,
            "message_to_user": effective_message,
        }

        if effective_message:
            result["messages"] = [AIMessage(content=effective_message, name="supervisor")]

        if decision.next == "hitl":
            result["pending_approval"] = {
                "description": decision.current_task,
                "tool_name": "human_approval_required",
            }

        return result

    return supervisor_node
