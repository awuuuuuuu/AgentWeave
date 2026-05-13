"""
记忆管理器（Memory Manager）

统一入口：协调三层记忆，在对话开始时构建记忆注入上下文，
在对话结束时触发压缩和长期存储。

职责：
- build_context(session_id, user_id, query, db) → 记忆注入字符串（注入 system prompt）
- on_session_end(session_id, user_id, db) → 触发摘要压缩 + 情景记忆写入 + 画像提取

记忆注入格式：
    [历史记忆]
    （2025-01-10）用户询问了关于 RAG 检索的优化方案，重点讨论了混合检索...
    （2025-01-08）用户探讨了 LangGraph 的状态管理...

    [用户偏好] 语言：中文 | 专业水平：中级 | 常用话题：深度学习、自然语言处理
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .long_term import LongTermMemory, MemorySummary
from .short_term import ShortTermMemory
from .user_profile import UserProfileManager

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    三层记忆协调器
    """

    def __init__(
        self,
        short_term: ShortTermMemory,
        long_term: LongTermMemory,
        user_profile: UserProfileManager
    ) -> None:
        self._short = short_term
        self._long = long_term
        self._profile = user_profile
    
    async def build_context(
        self,
        session_id: str,
        user_id: str,
        query: str,
        db: AsyncSession,
        top_k: int = 3,
    ) -> str:
        """
        构建注入 system prompt 的记忆上下文字符串。

        1. 语义检索最相关的历史摘要（LongTermMemory）
        2. 读取用户画像（UserProfileManager）
        3. 拼接为结构化字符串

        返回空字符串表示无可用记忆（新用户或首次对话）
        """
        parts: list[str] = []

        # 1. 情景记忆：历史对话摘要
        try:
            summaries: list[MemorySummary] = await self._long.search_relevant(
                user_id=user_id,
                query=query,
                top_k=top_k,
            )
            if summaries:
                lines = ["[历史记忆]"]
                for s in summaries:
                    dt = datetime.fromtimestamp(s.created_at, tz=timezone.utc)
                    date_str = dt.strftime("%Y-%m-%d")
                    # 截断单条摘要，避免超长
                    content = s.content[:500] + "..." if len(s.content) > 500 else s.content
                    lines.append(f"（{date_str}）{content}")
                parts.append("\n".join(lines))
        except Exception:
            logger.exception("记忆管理器: 为用户 user=%s 检索情景记忆失败", user_id)

        # 2. 语义记忆：用户画像
        try:
            profile_str = await self._profile.format_for_prompt(user_id=user_id, db=db)
            if profile_str:
                parts.append(profile_str)
        except Exception:
            logger.exception("记忆管理器: 为用户 user=%s 加载用户画像失败", user_id)

        return "\n\n".join(parts)
    
    async def on_session_end(
        self,
        session_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> None:
        """
        会话结束后调用：
        1. 从 AsyncPostgresSaver 读真实消息 → LLM 压缩 → 更新 checkpoint
        2. 将摘要写入 LongTermMemory（Milvus）
        3. 从消息提取用户偏好并更新 UserProfile
        """
        logger.info("记忆管理器: 会话结束处理 session=%s user=%s", session_id, user_id)

        # 1. 压缩 checkpoint（读真实消息 → 生成摘要 → 写回）
        summary: str | None = None
        try:
            summary = await self._short.compress(session_id, user_id)
        except Exception:
            logger.exception("记忆管理器: checkpoint 压缩失败 session=%s", session_id)

        # 2. 情景记忆存储
        if summary:
            try:
                await self._long.add_summary(
                    user_id=user_id,
                    session_id=session_id,
                    summary_text=summary,
                )
            except Exception:
                logger.exception("记忆管理器: 情景记忆持久化失败 session=%s", session_id)

        # 3. 用户画像提取（读真实消息）
        try:
            messages = await self._short.aget_messages(session_id, user_id)
            if messages:
                await self._profile.extract_and_update(
                    user_id=user_id,
                    db=db,
                    messages=messages,
                )
        except Exception:
            logger.exception("记忆管理器: 用户画像提取失败 session=%s", session_id)

    def get_thread_config(self, session_id: str, user_id: str = "") -> dict[str, Any]:
        """LangGraph config dict，格式与 agent.py._make_config 对齐"""
        return self._short.get_thread_config(session_id, user_id)

    async def aneeds_compression(self, session_id: str, user_id: str = "") -> bool:
        """判断当前会话是否超过压缩阈值"""
        return await self._short.aneeds_compression(session_id, user_id)
