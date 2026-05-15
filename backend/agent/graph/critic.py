"""
Critic 节点：答案质量审核（三段式门控）

评分 0-10 分，分三档处理：
  ≥ 7   → 自动通过，输出最终答案
  5-6   → 向用户询问（critic_gate interrupt）：[重新生成] / [接受]
  < 5   → 自动打回给 Supervisor 重做（最多 1 次，超限强制通过）
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field

from .prompts import CRITIC_SYSTEM
from .state import AgentState

logger = logging.getLogger(__name__)

_AUTO_PASS_SCORE = 7.0    # 高于此分自动通过
_USER_GATE_SCORE = 5.0    # 高于此分询问用户，否则自动打回
_MAX_AUTO_RETRY  = 1      # 低分自动打回的最大次数

AGENT_CARD = {
    "name": "critic",
    "description": "答案质量评审",
    "routing_hint": "复杂多步任务完成后需做质量评审时，且上一步不是 Critic",
    "tools": [],
    "icon": "✅",
    "color": "red",
}


class _CriticResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(description="综合评分，0-10 分")
    approved: bool = Field(description="是否通过（score ≥ 7 时应为 true）")
    feedback: Optional[str] = Field(
        default=None, description="改进建议，approved=false 时必填"
    )


def build_critic(llm_model: str = "gpt-4o") -> object:
    """构建 Critic 节点函数
    """
    llm = ChatOpenAI(model=llm_model, temperature=0)
    structured_llm = llm.with_structured_output(_CriticResult)

    async def critic_node(state: AgentState) -> dict:
        messages = state.get("messages", [])
        critic_count = state.get("critic_count", 0)

        last_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)), None
        )
        user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
        user_query = user_msgs[-1].content if user_msgs else ""

        if last_ai is None or not last_ai.content:
            logger.warning("Critic: 未找到 AI 回答，强制结束")
            return {"next_agent": "__end__", "critic_count": critic_count + 1,
                    "critic_approved": True, "critic_score": 0.0, "critic_feedback": ""}

        # 注入检索文档（snippet），让 Critic 能核对答案是否与知识库一致
        citations = state.get("citations", [])
        if citations:
            ref_block = "\n".join(
                f"[{c['ref']}] {c.get('source_file','')}: {c.get('snippet','')[:300]}"
                for c in citations[:6]
            )
            context_section = f"【参考文档（知识库原文片段）】：\n{ref_block}"
        else:
            context_section = ""

        result: _CriticResult = await structured_llm.ainvoke(
            [
                SystemMessage(content=CRITIC_SYSTEM),
                HumanMessage(content=(
                    f"【用户原始问题】：\n{user_query}\n\n"
                    + (f"{context_section}\n\n" if context_section else "")
                    + f"【需要审核的 AI 回答】：\n{last_ai.content}"
                )),
            ]
        )
        score    = result.score
        feedback = result.feedback or ""
        new_count = critic_count + 1

        logger.info("Critic [%d]: 评分=%.1f | 反馈=%r", new_count, score, feedback[:80])

        # ── ① 高分：自动通过 ──────────────────────────────────────────────────
        if score >= _AUTO_PASS_SCORE:
            return {
                "next_agent": "__end__",
                "critic_count": new_count,
                "critic_score": score,
                "critic_approved": True,
                "critic_feedback": feedback,
            }

        # ── ② 中分：询问用户 ──────────────────────────────────────────────────
        if score >= _USER_GATE_SCORE:
            logger.info("Critic: 评分 %.1f 属于中档，发起用户询问", score)
            decision: str = interrupt({
                "type": "critic_gate",
                "score": score,
                "feedback": feedback,
                "message": (
                    f"答案评分 {score:.1f}/10。"
                    f"改进建议：{feedback or '无具体反馈'}。是否重新生成？"
                ),
            })
            # decision = "retry" | "accept"
            if decision == "retry":
                logger.info("Critic: 用户选择重新生成")
                return {
                    "next_agent": "supervisor",
                    "critic_count": new_count,
                    "critic_score": score,
                    "critic_approved": False,
                    "critic_feedback": feedback,
                    "messages": [HumanMessage(
                        content=(
                            f"[Critic 反馈 · 用户请求重新生成] "
                            f"评分 {score:.0f}/10，需改进：{feedback}"
                        ),
                        name="critic",
                    )],
                }
            # accept
            logger.info("Critic: 用户接受当前答案")
            return {
                "next_agent": "__end__",
                "critic_count": new_count,
                "critic_score": score,
                "critic_approved": True,
                "critic_feedback": feedback,
            }

        # ── ③ 低分：自动打回（超限则强制通过）────────────────────────────────
        if new_count <= _MAX_AUTO_RETRY:
            logger.info("Critic: 评分 %.1f 不足，自动打回（第 %d 次）", score, new_count)
            return {
                "next_agent": "supervisor",
                "critic_count": new_count,
                "critic_score": score,
                "critic_approved": False,
                "critic_feedback": feedback,
                "messages": [HumanMessage(
                    content=(
                        f"[Critic 反馈 · 自动打回] "
                        f"评分 {score:.0f}/10，需改进：{feedback}"
                    ),
                    name="critic",
                )],
            }

        logger.info("Critic: 已达最大打回次数，强制通过（评分=%.1f）", score)
        return {
            "next_agent": "__end__",
            "critic_count": new_count,
            "critic_score": score,
            "critic_approved": False,   # 用户可看到最终评分仍不理想
            "critic_feedback": feedback,
        }

    return critic_node
