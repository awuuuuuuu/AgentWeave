"""
Human-in-the-Loop 节点

当 Supervisor 检测到高风险操作时路由到此节点。
Supervisor 在路由时已将操作描述写入 state["pending_approval"]，
此节点直接读取并调用 interrupt()，不再额外调用 LLM。

注意：interrupt() 需要 checkpointer（InMemorySaver 或持久化方案）才能工作。
      生产环境应换用 AsyncPostgresSaver 保证跨重启持久化。
"""
from __future__ import annotations

import logging

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from .state import AgentState

logger = logging.getLogger(__name__)

AGENT_CARD = {
    "name": "hitl",
    "description": "高风险操作前暂停等待人工审批；确认或拒绝后继续执行",
    "routing_hint": "用户请求涉及不可逆的外部操作（写文件、执行代码、发送消息等），或用户明确要求人工审批/确认（如『需要人工审批』『请人工确认』『最后审批』）时",
    "tools": [],
    "icon": "⚠️",
    "color": "yellow",
}

def build_hitl() -> object:
    """构建 HITL 节点函数"""

    async def hitl_node(state: AgentState) -> dict:
        """
        两阶段执行（LangGraph resume 机制）：

        阶段 1（首次进入）：
          - 从 pending_approval 读取操作描述
          - interrupt(payload) → 图挂起，payload 推送给前端展示审批卡片

        阶段 2（resume 后重入同一节点）：
          - interrupt() 检测到已有 resume 值，直接返回用户决策字符串
          - 根据 "approve" / "reject" 写入对应消息
        """
        pending = state.get("pending_approval") or {}
        operation_desc = pending.get("description", state.get("task", "待确认的操作"))

        logger.info("HITL: 触发审批等待 | 操作=%r", operation_desc[:80])

        # interrupt() 第一次调用时挂起；resume 后直接返回用户传入的字符串
        decision: str = interrupt(
            {
                "type": "approval_required",
                "description": operation_desc,
                "message": f"即将执行：{operation_desc}，是否确认？"
            }
        )

        if decision == "approve":
            logger.info("HITL: 用户批准 | 操作=%r", operation_desc[:60])
            return {
                "pending_approval": None,
                "next_agent": "",
                "messages": [
                    AIMessage(content=f"用户已批准，正在执行：{operation_desc}")
                ],
            }
        else:
            logger.info("HITL: 用户拒绝 | 操作=%r", operation_desc[:60])
            return {
                "pending_approval": None,
                "next_agent": "__end__",
                "messages": [
                    AIMessage(
                        content=(
                            f"操作已取消：用户拒绝执行 [{operation_desc}]。"
                            "如需调整，请重新描述您的需求。"
                        )
                    )
                ],
            }

    return hitl_node
