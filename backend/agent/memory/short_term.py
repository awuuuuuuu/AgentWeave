"""
短期记忆 (Working Memory)

基于 LangGraph InMemorySaver, 以 thread_id = session_id 隔离每个会话状态。
当会话消息数超过阈值时，触发 LLM 摘要压缩：
  1. 生成历史摘要文本（供 LongTermMemory 存储）
  2. 将 InMemorySaver 中旧消息替换为 [SystemMessage(摘要) + 最近 N 条]，防止无限膨胀
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from weakref import WeakValueDictionary

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

logger = logging.getLogger(__name__)


COMPRESSION_THRESHOLD = 20      # 超过此条数阈值触发摘要压缩
KEEP_RECENT = 6                 # 保留最近 KEEP_RECENT 条 + 压缩之前的

_SUMMARY_SYSTEM_PROMPT = """你是一个对话摘要助手。
请将以下多轮对话内容总结为一段简洁的中文摘要（150-300字）。
摘要须包含：用户的核心问题或任务、讨论的主要知识点、达成的结论或产出。
不要包含无关的寒暄，聚焦有信息量的内容。"""


def _extract_text(content: Any) -> str:
    """
    从消息 content 中提取纯文本，忽略图片/音频等多模态部分

    LangChain 多模态消息的 content 是 list[dict], 每个 dict 有 type 字段
    - {"type": "text", "text": "..."} → 取 text
    - {"type": "image_url", ...} → 忽略（防止 base64 污染摘要上下文）
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            part["text"] for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return " ".join(texts)
    return ""


class ShortTermMemory:
    """
    工作记忆

    所有会话共享同一个 InMemorySaver, 通过 thread_id 隔离
    """

    def __init__(self, llm_model: str = "gpt-4o") -> None:
        self.saver = InMemorySaver()
        self._llm = ChatOpenAI(model=llm_model, temperature=0)
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def compress(self, session_id: str) -> str | None:
        """
        对当前会话消息生成 LLM 摘要，并原地裁剪 InMemorySaver 中的旧消息。

        两步操作：
        1. LLM 生成摘要文本（返回给调用方，供 LongTermMemory 存储）
        2. 将 saver 中的消息替换为 [SystemMessage(摘要)] + 最近 KEEP_RECENT 条
           防止无限增长导致后续对话上下文爆炸

        若消息过少（< 4 条）则返回 None, 不值得压缩
        """
        async with self._get_lock(session_id):
            return await self._compress_locked(session_id)
        
    async def _compress_locked(self, session_id: str) -> str | None:
        """compress() 的实际逻辑"""
        messages = self.get_messages(session_id)
        if len(messages) < 4:
            return None
        
        dialogue_lines: list[str] = []
        for msg in messages:
            role = getattr(msg, "type", "unknown")
            role_label = "用户" if role == "human" else "助手"
            text = _extract_text(msg.content)
            if text:
                dialogue_lines.append(f"{role_label}：{text[:1000]}")

        dialogue_text = "\n".join(dialogue_lines)

        try:
            response = await self._llm.ainvoke(
                [
                    {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": f"对话内容：\n{dialogue_text}"},
                ]
            )
            summary = response.content.strip()
            logger.info(
                "ShortTermMemory: 会话 %s 压缩完成 → 生成摘要 %d 字符", session_id, len(summary)
            )

            recent_messages = messages[-KEEP_RECENT:]
            trimmed: list[BaseMessage] = [
                SystemMessage(content=f"[历史摘要] {summary}"),
                *recent_messages
            ]
            self._update_messages(session_id, trimmed)
            
            return summary
        except Exception:
            logger.exception("ShortTermMemory: 会话 %s 压缩失败", session_id)
            return None

    def _update_messages(self, session_id: str, new_messages: list[BaseMessage]) -> None:
        """
        将 InMemorySaver 中指定会话的消息列表替换为 new_messages

        通过 put() 写入一个新的 checkpoint, 保留其他 channel_values 不变
        """
        config = self.get_thread_config(session_id)
        checkpoint_tuple = self.saver.get_tuple(config)
        if checkpoint_tuple is None:
            return
        
        old_checkpoint = checkpoint_tuple.checkpoint
        new_channel_values = {
            **old_checkpoint.get("channel_values", {}),
            "messages": new_messages
        }
        new_checkpoint = {
            **old_checkpoint,
            "channel_values": new_channel_values
        }

        self.saver.put(
            config=config,
            checkpoint=new_checkpoint,
            metadata={"source": "compression", "step": -1, "writes": {}},
            new_versions={}
        )
        logger.debug("短期记忆: 会话 %s 已裁剪至 %d 条消息", session_id, len(new_messages))


    def _get_lock(self, session_id: str) -> asyncio.Lock:
        """获取指定 session_id 的异步锁"""
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock
    
    def get_messages(self, session_id: str) -> list[BaseMessage]:
        """读取指定会话的消息列表（从 checkpoint 中提取）"""
        config = self.get_thread_config(session_id)
        checkpoint_tuple = self.saver.get_tuple(config)
        if checkpoint_tuple is None:
            return []
        channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
        return channel_values.get("messages", [])

    def message_count(self, session_id: str) -> int:
        return len(self.get_messages(session_id))

    def needs_compression(self, session_id: str) -> bool:
        return self.message_count(session_id) >= COMPRESSION_THRESHOLD

    def get_thread_config(self, session_id: str) -> dict[str, Any]:
        """返回 LangGraph 调用所需的 config dict"""
        return {"configurable": {"thread_id": session_id}}