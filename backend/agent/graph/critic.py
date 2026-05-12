"""
Critic 节点：答案质量审核

评分 0-10 分：
  ≥ 7 → approved，路由到 END
  < 7 且未超过 MAX_CRITIC_RETRIES → 打回给 Supervisor 重做
  超过 MAX_CRITIC_RETRIES → 强制通过，避免无限循环
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .prompts import CRITIC_SYSTEM
from .state import MAX_CRITIC_RETRIES, AgentState

logger = logging.getLogger(__name__)

AGENT_CARD = {
    "name": "critic",
    "description": "质量审核，对回答评分（0-10）；评分低于 7 分自动打回重做",
    "tools": [],
    "icon": "✅",
    "color": "red",
}

_PASS_SCORE = 7.0


class _CriticResult(BaseModel):
    score: float = Field(description="综合评分，0-10 分")
    approved: bool = Field(description="是否通过（score >= 7 时应为 true）")
    feedback: Optional[str] = Field(
        default=None, description="改进建议，approved=false 时必填"
    )


def build_critic(llm_model: str = "gpt-4o") -> object:
    """构建 Critic 节点函数"""
    llm = ChatOpenAI(model=llm_model, temperature=0)
    structured_llm = llm.with_structured_output(_CriticResult)

    async def critic_node(state: AgentState) -> dict:
        messages = state.get("messages", [])
        critic_count = state.get("critic_count", 0)

        # 找最后一条 AI 回答
        last_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)), None
        )
        # 找最后一条用户问题
        user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
        user_query = user_msgs[-1].content if user_msgs else ""

        # 无 AI 回答时直接结束
        if last_ai is None or not last_ai.content:
            logger.warning("Critic: 未找到 AI 回答，强制结束")
            return {"next_agent": "__end__", "critic_count": critic_count + 1}

        result: _CriticResult = await structured_llm.ainvoke(
            [
                SystemMessage(content=CRITIC_SYSTEM),
                HumanMessage(
                    content=(
                        f"用户问题：{user_query}\n\n"
                        f"AI 回答：{last_ai.content[:2000]}"
                    )
                ),
            ]
        )
        score = result.score

        approved = result.approved and score >= _PASS_SCORE
        feedback = result.feedback or ""

        new_count = critic_count + 1
        logger.info(
            "Critic [审核节点]: 评分=%.1f | 通过状态=%s | 尝试轮次=%d/%d | 审核意见=%r",
            score, approved, new_count, MAX_CRITIC_RETRIES, feedback[:80],
        )

        # 强制通过：approved 或已达最大重试次数
        if approved or new_count >= MAX_CRITIC_RETRIES:
            return {"next_agent": "__end__", "critic_count": new_count}

        return {
            "next_agent": "supervisor",
            "critic_count": new_count,
            "messages": [
                HumanMessage(
                    content=(
                        f"[系统质检反馈，非用户输入] 第{new_count}次评审，"
                        f"评分 {score:.0f}/10，请改进后重新回答：{feedback}"
                    ),
                    name="critic",
                )
            ],
        }

    return critic_node
