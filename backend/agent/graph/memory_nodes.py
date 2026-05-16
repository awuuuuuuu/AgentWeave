"""
记忆节点：memory_inject + memory_save

memory_inject（入口）：
  1. 若 session 消息超过阈值，用 RemoveMessage 原地压缩（图内安全机制，不直接写 checkpoint）
  2. 从 LongTermMemory + UserProfile 构建 memory_context，写入 state 供 Supervisor 使用

memory_save（出口）：
  Critic 通过后 fire-and-forget 触发 memory_manager.on_session_end()：
    - ShortTermMemory.compress() 读真实 checkpoint → 生成摘要 → 写回 checkpoint
    - 摘要写入 LongTermMemory（Milvus）
    - UserProfile 更新
  立即返回，不阻塞 done 事件

设计说明：
  - 图内压缩用 RemoveMessage + acompress_messages（不碰 checkpoint，LangGraph 安全写入）
  - 图外压缩（on_session_end）用 ShortTermMemory.compress()，读写共享的 AsyncPostgresSaver
  - memory_context 用独立 state 字段，不污染 messages 历史
"""
from __future__ import annotations

import asyncio
import logging

from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage

from agent.memory.memory_manager import MemoryManager
from db.session import SessionLocal

from .state import AgentState

logger = logging.getLogger(__name__)

_COMPRESSION_THRESHOLD = 20
_KEEP_RECENT = 6


def build_memory_nodes(memory_manager: MemoryManager):
    """返回 (memory_inject_fn, memory_save_fn) 两个节点函数"""

    async def memory_inject_node(state: AgentState) -> dict:
        """
        并发执行两件事：
          A. checkpoint 内压缩（超阈值时，RemoveMessage 删旧 + 摘要 SystemMessage）
          B. 跨会话记忆注入（LongTermMemory + UserProfile → memory_context）
        """
        user_id = state.get("user_id", "")
        session_id = state.get("session_id", "")
        messages = state.get("messages", [])
        user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
        query = user_msgs[-1].content if user_msgs else ""

        if not user_id or not query:
            return {}

        async def _compress():
            """生成摘要，返回 [RemoveMessage…, SystemMessage] 或 None"""
            if len(messages) < _COMPRESSION_THRESHOLD:
                return None
            old_msgs = messages[:-_KEEP_RECENT]
            summary = await memory_manager._short.acompress_messages(old_msgs)
            if not summary:
                return None
            removals = [RemoveMessage(id=m.id) for m in old_msgs if m.id]
            return [*removals, SystemMessage(content=f"[会话压缩摘要] {summary}")]

        async def _fetch_context():
            async with SessionLocal() as db:
                return await memory_manager.build_context(
                    session_id=session_id,
                    user_id=user_id,
                    query=query,
                    db=db,
                )

        try:
            compress_result, context = await asyncio.gather(
                _compress(), _fetch_context(), return_exceptions=True
            )
        except Exception:
            logger.exception("memory_inject: gather 失败 user=%s", user_id)
            return {}

        result: dict = {"memory_injected": True}

        if isinstance(compress_result, list) and compress_result:
            result["messages"] = compress_result
            logger.info(
                "memory_inject: checkpoint 已压缩，保留最近 %d 条 session=%s",
                _KEEP_RECENT, session_id,
            )
        elif isinstance(compress_result, Exception):
            logger.warning("memory_inject: 压缩失败（不影响流程）: %s", compress_result)

        if isinstance(context, str) and context:
            result["memory_context"] = context
            logger.info(
                "memory_inject: 注入跨会话上下文 %d 字 user=%s", len(context), user_id
            )
        elif isinstance(context, Exception):
            logger.warning("memory_inject: 上下文拉取失败（不影响流程）: %s", context)

        return result

    async def memory_save_node(state: AgentState) -> dict:
        """
        fire-and-forget 触发 on_session_end，立即返回不阻塞 done 事件。
        on_session_end：compress checkpoint → 写 LongTermMemory → 更新 UserProfile。
        """
        user_id = state.get("user_id", "")
        session_id = state.get("session_id", "")

        if not user_id:
            return {}

        asyncio.create_task(run_on_session_end(memory_manager, session_id, user_id))
        logger.info("memory_save: 后台 on_session_end 已触发 session=%s", session_id)
        return {}

    return memory_inject_node, memory_save_node


async def run_on_session_end(
    memory_manager: MemoryManager,
    session_id: str,
    user_id: str,
) -> None:
    try:
        async with SessionLocal() as db:
            await memory_manager.on_session_end(session_id, user_id, db)
    except Exception:
        logger.exception("memory_save: on_session_end 失败 session=%s", session_id)
