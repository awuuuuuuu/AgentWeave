"""
用户语义记忆 (Semantic Memory)

基于 PostgreSQL UserProfile 表 + Redis 缓存，存储用户偏好、专业方向、常用知识库等
每个用户一条记录 (upsert 语义), LLM 异步提取并合并更新

"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import UserProfile

logger = logging.getLogger(__name__)

# Redis 缓存 TTL(秒)：10 分钟
_CACHE_TTL = 600
_CACHE_KEY_PREFIX = "user_profile:"

_EXTRACT_SYSTEM_PROMPT = """你是一个用户画像提取助手。
根据以下对话内容，提取用户表现出的：
1. 语言偏好(zh/en)
2. 专业水平(beginner/intermediate/expert)
3. 感兴趣的话题(最多5个，用中文短语)
4. 其他有价值的偏好(key-value 格式)

若某字段无法从对话中判断，将其设为 null(不要猜测)。"""

class _ExtractedProfile(BaseModel):
    """LLM Structured Output schema, 字段全部可选(无法判断时返回 None)"""
    preferred_language: str | None = None
    expertise_level: str | None = None
    frequent_topics: list[str] | None = None
    preferences: dict[str, Any] | None = None

class UserProfileManager:
    """
    用户语义记忆管理器
    """
    def __init__(
        self,
        llm_model: str = "gpt-4o",
        redis_client: Any | None = None
    ) -> None:
        _llm = ChatOpenAI(model=llm_model, temperature=0)
        self._structured_llm = _llm.with_structured_output(_ExtractedProfile)
        self._redis = redis_client
    
    async def get_profile(
        self,
        user_id: str,
        db: AsyncSession
    ) -> UserProfile | None:
        """先读 Redis 缓存, miss 时读 PostgreSQL 并回填缓存"""
        # 1. 尝试读取 Redis
        if self._redis is not None:
            cached = await self._get_cache(user_id)
            if cached is not None:
                return self._dict_to_profile(user_id, cached)
        
        # 2. 读取 PostgreSQL
        from sqlalchemy import select
        result = await db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()

        # 3. 回填缓存
        if profile is not None and self._redis is not None:
            await self._set_cache(user_id, self._profile_to_dict(profile))

        return profile


    async def upsert_profile(
        self,
        user_id: str,
        db: AsyncSession,
        **updates: Any
    ) -> UserProfile:
        """
        合并更新 UserProfile(存在则更新，不存在则创建)

        `preferences` 字段做 dict merge(不整体覆盖)，其余字段直接覆盖
        """
        from sqlalchemy import select

        result = await db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()

        if profile is None:
            profile = UserProfile(user_id=user_id)
            db.add(profile)
        
        # 合并 preferences（dict merge）
        new_prefs = updates.pop("preferences", None)
        if new_prefs:
            existing = profile.preferences or {}
            profile.preferences = {**existing, **new_prefs}

        # 合并 frequent_topics（去重追加，保留最近 20 个）
        new_topics = updates.pop("frequent_topics", None)
        if new_topics:
            existing_topics: list[str] = profile.frequent_topics or []
            merged = list(dict.fromkeys(existing_topics + new_topics))
            profile.frequent_topics = merged[-20:]

        # 合并 frequent_kb_ids（去重追加，保留最近 20 个）
        new_kb_ids = updates.pop("frequent_kb_ids", None)
        if new_kb_ids:
            existing_kb_ids: list[str] = profile.frequent_kb_ids or []
            merged_kbs = list(dict.fromkeys(existing_kb_ids + new_kb_ids))
            profile.frequent_kb_ids = merged_kbs[-20:]

        # 其余字段直接覆盖
        for key, val in updates.items():
            if hasattr(profile, key) and val is not None:
                setattr(profile, key, val)

        await db.commit()
        await db.refresh(profile)

        # 失效缓存
        if self._redis is not None:
            await self._del_cache(user_id)

        logger.info("UserProfileManager: 成功保存用户画像 user=%s", user_id)
        return profile


    async def extract_and_update(
        self,
        user_id: str,
        db: AsyncSession,
        messages: list[Any]
    ) -> None:
        """
        从对话消息列表中用 LLM 提取用户偏好，并合并写入 UserProfile。

        此方法设计为 Celery 任务调用，不抛出异常（失败只记日志）
        """
        if len(messages) < 4:
            return
        
        try:
            dialogue_lines: list[str] = []
            for msg in messages:
                role = getattr(msg, "type", "unknown")
                role_label = "用户" if role == "human" else "助手"
                content = msg.content if isinstance(msg.content, str) else ""
                if content:
                    dialogue_lines.append(f"{role_label}：{content[:500]}")

            dialogue_text = "\n".join(dialogue_lines[-20:])  # 只取最近 20 条

            extracted: _ExtractedProfile | None = await self._structured_llm.ainvoke(
                [
                    {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": f"对话内容：\n{dialogue_text}"},
                ]
            )

            if extracted is not None:
                updates = extracted.model_dump(exclude_none=True)
                if updates:
                    await self.upsert_profile(user_id=user_id, db=db, **updates)
                    logger.info(
                        "UserProfileManager: 成功提取用户画像 (用户=%s, 更新字段=%s)",
                        user_id, list(updates.keys()),
                    )
        except Exception:
            logger.exception(
                "UserProfileManager: extract_and_update 执行失败，用户=%s", user_id
            )



    async def format_for_prompt(
        self,
        user_id: str,
        db: AsyncSession,
    ) -> str:
        """
        格式化用户画像为可注入 system prompt 的简短字符串。

        示例：[用户偏好] 语言：中文 | 专业水平：中级 | 常用话题：深度学习、自然语言处理
        """
        profile = await self.get_profile(user_id=user_id, db=db)
        if profile is None:
            return ""

        parts: list[str] = []
        lang_map = {"zh": "中文", "en": "英文"}
        level_map = {"beginner": "入门", "intermediate": "中级", "expert": "专家"}

        if profile.preferred_language:
            parts.append(f"语言：{lang_map.get(profile.preferred_language, profile.preferred_language)}")
        if profile.expertise_level:
            parts.append(f"专业水平：{level_map.get(profile.expertise_level, profile.expertise_level)}")
        if profile.frequent_topics:
            topics = "、".join(profile.frequent_topics[:5])
            parts.append(f"常用话题：{topics}")

        if not parts:
            return ""
        return "[用户偏好] " + " | ".join(parts)

    def _cache_key(self, user_id: str) -> str:
        return f"{_CACHE_KEY_PREFIX}{user_id}"
    
    async def _get_cache(self, user_id: str) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(self._cache_key(user_id))
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            logger.warning("UserProfileManager: 获取 Redis 缓存失败，user_id=%s", user_id)
            return None
    
    async def _set_cache(self, user_id: str, data: dict[str, Any]) -> None:
        try:
            await self._redis.set(
                self._cache_key(user_id),
                json.dumps(data, ensure_ascii=False),
                ex=_CACHE_TTL
            )
        except Exception:
            logger.warning("UserProfileManager: Redis 缓存写入失败 user=%s", user_id)
        
    async def _del_cache(self, user_id: str) -> None:
        try:
            await self._redis.delete(self._cache_key(user_id))
        except Exception:
            logger.warning("UserProfileManager: Redis 缓存删除失败 user=%s", user_id)
            

    @staticmethod
    def _profile_to_dict(profile: UserProfile) -> dict[str, Any]:
        return {
            "preferred_language": profile.preferred_language,
            "expertise_level": profile.expertise_level,
            "frequent_topics": profile.frequent_topics,
            "frequent_kb_ids": profile.frequent_kb_ids,
            "preferences": profile.preferences,
        }

    @staticmethod
    def _dict_to_profile(user_id: str, data: dict[str, Any]) -> UserProfile:
        p = UserProfile(user_id=user_id)
        p.preferred_language = data.get("preferred_language", "zh")
        p.expertise_level = data.get("expertise_level", "intermediate")
        p.frequent_topics = data.get("frequent_topics")
        p.frequent_kb_ids = data.get("frequent_kb_ids")
        p.preferences = data.get("preferences")
        return p