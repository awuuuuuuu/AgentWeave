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

SUPERVISOR_CONTEXT_WINDOW = 6   # 减少输入 token：Supervisor 只需知道最近几轮
_MSG_CONTENT_LIMIT = 800        # 单条消息内容截断，防止长 Analyst 答案撑爆上下文


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
                "发给当前 next 专家的具体指令（中文，100 字以内）。"
                "只描述该专家需要完成的子任务，不包含其他专家的职责或整体目标。"
                "【给 Researcher】：写成可直接用于知识库检索的具体问题，"
                "包含核心实体和关键维度（判断条件、数值阈值、操作步骤、责任机构、时限等）；"
                "禁止写成'检索X文档的具体内容'等泛化表述，要写清楚要找什么信息。"
                "【给 Analyst】：说明需要调用哪类工具及查询的具体对象/参数。"
            )
        )
        message_to_user: str = Field(
            default="",
            description=(
                "向用户展示的 Supervisor 自然语言回复（中文，50字以内）。"
                "首轮：首行任务意图 + 每专家一行 @专家名说明。"
                "后续轮次：一行，@专家名 + 简短说明。"
                "路由到 __end__ 时：填空字符串。"
            ),
        )
        reasoning: str = Field(description="路由决策理由（10字以内）")

    system_prompt = build_supervisor_system(cards)
    # max_tokens 确保 structured output JSON 不会被截断
    # 当上下文很长时（如 analyst 返回大量工具数据），LLM 可能生成更长的 reasoning；预留 4096
    llm = ChatOpenAI(model=llm_model, temperature=0, max_tokens=4096)
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

        # 截取最近几条，并截断过长的消息内容，防止 Analyst 长答案撑爆 prompt
        raw_recent = messages[-SUPERVISOR_CONTEXT_WINDOW:]
        recent = []
        for msg in raw_recent:
            if isinstance(msg.content, str) and len(msg.content) > _MSG_CONTENT_LIMIT:
                msg = msg.model_copy(update={"content": msg.content[:_MSG_CONTENT_LIMIT] + "…（已截断）"})
            recent.append(msg)

        # 构建 system prompt
        effective_system = system_prompt

        # 注入轮次信息，让 LLM 知道当前是第几轮（影响 message_to_user 格式）
        researcher_count = state.get("researcher_count", 0)
        analyst_count = state.get("analyst_count", 0)
        effective_system += (
            f"\n\n[当前轮次] supervisor_count={supervisor_count}"
            f" | researcher_count={researcher_count}"
            f" | analyst_count={analyst_count}"
        )
        memory_context = state.get("memory_context", "")
        if memory_context and researcher_count == 0 and analyst_count == 0:
            effective_system += (
                "\n[路由约束] 记忆背景仅供路由参考，本轮尚未调用任何专家；"
                "必须至少路由一次 researcher 或 analyst，禁止直接 __end__。"
            )

        # 注入当前 org 可用的 MCP 工具摘要（由 agent_stream 生成，影响路由决策）
        org_mcp_summary = state.get("org_mcp_summary", "")
        if org_mcp_summary:
            effective_system += f"\n\n[当前部门 MCP 工具] {org_mcp_summary}"

        # 注入部门专属路由补充（由 Organization.dept_prompts 提供）
        org_supervisor_hints = state.get("org_supervisor_hints", "")
        if org_supervisor_hints:
            effective_system += f"\n\n[本部门路由补充] {org_supervisor_hints}"

        # 注入记忆上下文（历史背景，仅首轮注入；后续轮次 LLM 已有消息历史，无需重复）
        if memory_context and supervisor_count == 1:
            effective_system += (
                f"\n\n---\n"
                f"[历史背景参考（仅供路由参考，必须路由给专家处理，禁止直接输出 __end__）]\n"
                f"{memory_context}\n---"
            )

        try:
            decision: _RoutingDecision = await structured_llm.ainvoke(
                [SystemMessage(content=effective_system), *recent]
            )
        except Exception as llm_exc:
            # LLM 调用失败（如 max_tokens 截断、网络超时）：根据当前状态选择最安全路由
            logger.error("Supervisor: structured output 失败 (%s)，根据状态恢复路由", llm_exc)
            fallback_next = "reporter" if (
                state.get("researcher_count", 0) >= 1 or state.get("analyst_count", 0) >= 1
            ) else "researcher"
            decision = _RoutingDecision(
                next=fallback_next,
                current_task="整合已有信息输出最终答案",
                message_to_user="",
                reasoning="异常恢复",
            )

        # 硬防护：拦截 LLM 幻觉导致的重复路由
        if decision.next == "researcher" and state.get("researcher_count", 0) >= 1:
            logger.warning(
                "Supervisor: 拦截重复路由 researcher (researcher_count=%d)，强制 reporter",
                state.get("researcher_count", 0),
            )
            decision = decision.model_copy(update={"next": "reporter", "message_to_user": ""})
        elif decision.next == "analyst" and state.get("analyst_count", 0) >= 1:
            logger.warning(
                "Supervisor: 拦截重复路由 analyst (analyst_count=%d)，强制 reporter",
                state.get("analyst_count", 0),
            )
            decision = decision.model_copy(update={"next": "reporter", "message_to_user": ""})
        elif decision.next == "__end__" and (
            state.get("researcher_count", 0) >= 1 or state.get("analyst_count", 0) >= 1
        ):
            logger.warning(
                "Supervisor: 拦截 __end__（researcher_count=%d, analyst_count=%d），强制 reporter",
                state.get("researcher_count", 0),
                state.get("analyst_count", 0),
            )
            decision = decision.model_copy(update={"next": "reporter", "message_to_user": ""})

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
