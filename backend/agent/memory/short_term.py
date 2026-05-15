"""
短期记忆（Working Memory）

与 Agent 主图共享同一个 AsyncPostgresSaver，通过 thread_id 隔离每个会话。
thread_id 格式与 agent.py._make_config 对齐：f"{user_id}:{session_id}"

会话消息数超过阈值时，触发 LLM 摘要压缩：
  1. 生成历史摘要文本（返回给调用方，供 LongTermMemory 存储）
  2. 将 checkpoint 中旧消息替换为 [SystemMessage(摘要) + 最近 N 条]
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from weakref import WeakValueDictionary

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

COMPRESSION_THRESHOLD = 20   # 超过此条数触发摘要压缩
KEEP_RECENT = 6              # 压缩后保留最近 N 条

_SUMMARY_SYSTEM_PROMPT = """你是一个对话摘要助手。
请将以下多轮对话内容总结为一段简洁的中文摘要（150-300字）。
摘要须包含：用户的核心问题或任务、讨论的主要知识点、达成的结论或产出。
不要包含无关的寒暄，聚焦有信息量的内容。"""


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p["text"] for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def _build_dialogue_lines(messages: list[BaseMessage]) -> list[str]:
    """
    将消息列表转为摘要用的对话行。
    - HumanMessage → 用户
    - AIMessage     → 助手
    - ToolMessage   → [工具结果] 纳入摘要，防止"刚才查到的数据"在压缩后丢失
    - SystemMessage → 跳过（已是摘要或注入上下文，不重复摘要）
    """
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            text = _extract_text(msg.content)
            if text:
                lines.append(f"用户：{text[:1000]}")
        elif isinstance(msg, AIMessage):
            text = _extract_text(msg.content)
            if text:
                lines.append(f"助手：{text[:1000]}")
        elif isinstance(msg, ToolMessage):
            text = _extract_text(msg.content)
            if text:
                lines.append(f"[工具结果] {text[:500]}")
    return lines


class ShortTermMemory:
    """
    工作记忆：与主图共享 AsyncPostgresSaver，直接读写真实的 checkpoint。

    graph.compile(checkpointer=checkpointer) 与 ShortTermMemory(checkpointer=checkpointer)
    使用同一个实例，thread_id 格式相同，因此 aget_messages() 读到的就是图执行后的真实消息。
    """

    def __init__(self, checkpointer: Any, llm_model: str = "gpt-4o") -> None:
        self.saver = checkpointer
        self._llm = ChatOpenAI(model=llm_model, temperature=0)
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def get_thread_config(self, session_id: str, user_id: str = "") -> dict[str, Any]:
        """与 agent.py._make_config 对齐：thread_id = '{user_id}:{session_id}'"""
        thread_id = f"{user_id}:{session_id}" if user_id else session_id
        return {"configurable": {"thread_id": thread_id}}

    async def aget_messages(self, session_id: str, user_id: str = "") -> list[BaseMessage]:
        """从 AsyncPostgresSaver 读取当前会话的消息列表"""
        config = self.get_thread_config(session_id, user_id)
        checkpoint_tuple = await self.saver.aget_tuple(config)
        if checkpoint_tuple is None:
            return []
        channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
        return channel_values.get("messages", [])

    async def amessage_count(self, session_id: str, user_id: str = "") -> int:
        return len(await self.aget_messages(session_id, user_id))

    async def aneeds_compression(self, session_id: str, user_id: str = "") -> bool:
        return await self.amessage_count(session_id, user_id) >= COMPRESSION_THRESHOLD

    async def acompress_messages(self, messages: list[BaseMessage]) -> str | None:
        """
        对给定消息列表生成摘要文本，不读写 checkpoint。
        供图内 RemoveMessage 压缩使用（图执行中直接写 checkpoint 不安全）。
        """
        if len(messages) < 4:
            return None
        lines = _build_dialogue_lines(messages)
        if not lines:
            return None
        try:
            resp = await self._llm.ainvoke([
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": "对话内容：\n" + "\n".join(lines)},
            ])
            return resp.content.strip() or None
        except Exception:
            logger.exception("ShortTermMemory.acompress_messages: 失败")
            return None

    async def compress(self, session_id: str, user_id: str = "") -> str | None:
        """
        生成 LLM 摘要并将旧消息替换为摘要 + 最近 N 条（原地更新 checkpoint）。

        注意：此方法应在图执行结束后调用（on_session_end 场景），
        不要在图节点内部调用（会与 LangGraph 的 checkpoint 写入冲突）。
        图内压缩请用 RemoveMessage 机制（见 memory_nodes.py）。
        """
        async with self._get_lock(session_id):
            return await self._compress_locked(session_id, user_id)

    # ── 内部实现 ──────────────────────────────────────────────────────────────

    async def _compress_locked(self, session_id: str, user_id: str) -> str | None:
        messages = await self.aget_messages(session_id, user_id)
        if len(messages) < 4:
            return None

        dialogue_lines = _build_dialogue_lines(messages)
        if not dialogue_lines:
            return None

        try:
            response = await self._llm.ainvoke([
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": "对话内容：\n" + "\n".join(dialogue_lines)},
            ])
            summary = response.content.strip()
            if not summary:
                return None

            trimmed: list[BaseMessage] = [
                SystemMessage(
                    content=(
                        f"[重要：为节省上下文，之前 {len(messages) - KEEP_RECENT} 条消息已压缩。"
                        f"以下是核心摘要]\n{summary}"
                    )
                ),
                *messages[-KEEP_RECENT:],
            ]
            await self._aupdate_messages(session_id, user_id, trimmed)
            logger.info(
                "ShortTermMemory: 会话 %s 压缩完成 → %d 字摘要", session_id, len(summary)
            )
            return summary
        except Exception:
            logger.exception("ShortTermMemory: 会话 %s 压缩失败", session_id)
            return None

    async def _aupdate_messages(
        self, session_id: str, user_id: str, new_messages: list[BaseMessage]
    ) -> None:
        """将 checkpoint 中的 messages 替换为 new_messages"""
        base_config = self.get_thread_config(session_id, user_id)
        checkpoint_tuple = await self.saver.aget_tuple(base_config)
        if checkpoint_tuple is None:
            return

        # checkpoint_tuple.config 包含 AsyncPostgresSaver.aput 所需的全部字段
        # (thread_id, checkpoint_ns, checkpoint_id 等)，直接复用避免 KeyError
        full_config = checkpoint_tuple.config

        old_cp = checkpoint_tuple.checkpoint
        old_meta = checkpoint_tuple.metadata or {}
        new_cp = {
            **old_cp,
            "channel_values": {
                **old_cp.get("channel_values", {}),
                "messages": new_messages,
            },
        }
        # 继承现有 step 并 +1，避免 Postgres 版本号回溯导致乐观锁异常
        new_step = (old_meta.get("step") or 0) + 1
        await self.saver.aput(
            config=full_config,
            checkpoint=new_cp,
            metadata={"source": "compression", "step": new_step, "writes": {}},
            new_versions={},
        )
        logger.debug(
            "ShortTermMemory: 会话 %s checkpoint 已裁剪至 %d 条 (step=%d)",
            session_id, len(new_messages), new_step,
        )

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock
