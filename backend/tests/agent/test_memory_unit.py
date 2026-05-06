"""
记忆系统单元测试（全 mock，不依赖真实服务）

覆盖：
- ShortTermMemory：消息读取、压缩阈值判断、摘要生成
- LongTermMemory：add_summary、search_relevant、delete_by_session、eviction
- UserProfileManager：get_profile（缓存命中/miss）、upsert_profile（merge 语义）、format_for_prompt
- MemoryManager：build_context、on_session_end
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── ShortTermMemory ─────────────────────────────────────────────────────────


class TestExtractText:
    """_extract_text 辅助函数：多模态内容过滤"""

    def test_string_content_returned_as_is(self):
        from agent.memory.short_term import _extract_text
        assert _extract_text("hello world") == "hello world"

    def test_multimodal_list_extracts_text_only(self):
        from agent.memory.short_term import _extract_text
        content = [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc..."}},
        ]
        result = _extract_text(content)
        assert result == "看这张图"
        assert "base64" not in result

    def test_empty_list_returns_empty_string(self):
        from agent.memory.short_term import _extract_text
        assert _extract_text([]) == ""

    def test_unknown_type_returns_empty_string(self):
        from agent.memory.short_term import _extract_text
        assert _extract_text(12345) == ""


class TestShortTermMemoryBasic:
    """工作记忆：消息读取和阈值判断"""

    def _make_stm(self):
        with patch("agent.memory.short_term.ChatOpenAI"):
            from agent.memory.short_term import ShortTermMemory
            return ShortTermMemory(llm_model="gpt-4o-mini")

    def test_get_thread_config_returns_correct_structure(self):
        stm = self._make_stm()
        cfg = stm.get_thread_config("sess-123")
        assert cfg == {"configurable": {"thread_id": "sess-123"}}

    def test_get_messages_returns_empty_when_no_checkpoint(self):
        stm = self._make_stm()
        stm.saver.get_tuple = MagicMock(return_value=None)
        msgs = stm.get_messages("sess-abc")
        assert msgs == []

    def test_get_messages_extracts_from_channel_values(self):
        stm = self._make_stm()
        fake_msg = MagicMock()
        fake_checkpoint = MagicMock()
        fake_checkpoint.checkpoint = {"channel_values": {"messages": [fake_msg]}}
        stm.saver.get_tuple = MagicMock(return_value=fake_checkpoint)

        msgs = stm.get_messages("sess-xyz")
        assert msgs == [fake_msg]

    def test_needs_compression_false_below_threshold(self):
        stm = self._make_stm()
        stm.get_messages = MagicMock(return_value=[MagicMock()] * 19)
        assert stm.needs_compression("sess-1") is False

    def test_needs_compression_true_at_threshold(self):
        stm = self._make_stm()
        stm.get_messages = MagicMock(return_value=[MagicMock()] * 20)
        assert stm.needs_compression("sess-1") is True

    def test_message_count_delegates_to_get_messages(self):
        stm = self._make_stm()
        stm.get_messages = MagicMock(return_value=[MagicMock()] * 7)
        assert stm.message_count("sess-1") == 7


class TestShortTermMemoryCompress:
    """工作记忆：摘要压缩"""

    def _make_stm(self):
        with patch("agent.memory.short_term.ChatOpenAI"):
            from agent.memory.short_term import ShortTermMemory
            return ShortTermMemory()

    @pytest.mark.asyncio
    async def test_compress_returns_none_when_too_few_messages(self):
        stm = self._make_stm()
        stm.get_messages = MagicMock(return_value=[MagicMock()] * 3)
        result = await stm.compress("sess-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_compress_returns_summary_on_success(self):
        stm = self._make_stm()
        msgs = []
        for role in ["human", "ai"] * 4:
            m = MagicMock()
            m.type = role
            m.content = f"消息内容 {role}"
            msgs.append(m)
        stm.get_messages = MagicMock(return_value=msgs)

        mock_response = MagicMock()
        mock_response.content = "这是一段摘要"
        stm._llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await stm.compress("sess-1")
        assert result == "这是一段摘要"

    @pytest.mark.asyncio
    async def test_compress_trims_messages_in_saver(self):
        """compress 成功后应原地裁剪 InMemorySaver 中的消息"""
        stm = self._make_stm()
        msgs = []
        for role in ["human", "ai"] * 4:
            m = MagicMock()
            m.type = role
            m.content = "消息内容"
            msgs.append(m)
        stm.get_messages = MagicMock(return_value=msgs)
        stm._update_messages = MagicMock()

        mock_response = MagicMock()
        mock_response.content = "摘要内容"
        stm._llm.ainvoke = AsyncMock(return_value=mock_response)

        await stm.compress("sess-1")
        # 必须调用 _update_messages 写回裁剪后的消息
        stm._update_messages.assert_called_once()
        trimmed = stm._update_messages.call_args[0][1]
        # 第一条应是 SystemMessage，之后是最近 KEEP_RECENT 条
        from langchain_core.messages import SystemMessage
        assert isinstance(trimmed[0], SystemMessage)
        assert "历史摘要" in trimmed[0].content

    @pytest.mark.asyncio
    async def test_compress_returns_none_on_llm_failure(self):
        stm = self._make_stm()
        stm.get_messages = MagicMock(return_value=[MagicMock()] * 8)
        stm._llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))

        result = await stm.compress("sess-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_compress_concurrent_calls_use_lock(self):
        """同一 session 并发压缩时，第二个请求须等第一个完成（锁保护）"""
        import asyncio as _asyncio
        stm = self._make_stm()
        msgs = [MagicMock(type="human", content="x")] * 6
        stm.get_messages = MagicMock(return_value=msgs)
        stm._update_messages = MagicMock()

        call_order: list[str] = []

        async def slow_llm(*args, **kwargs):
            call_order.append("start")
            await _asyncio.sleep(0.05)
            call_order.append("end")
            r = MagicMock()
            r.content = "摘要"
            return r

        stm._llm.ainvoke = slow_llm

        # 两个并发压缩任务
        await _asyncio.gather(
            stm.compress("sess-1"),
            stm.compress("sess-1"),
        )
        # 锁串行化：必须出现 start→end→start→end 而非 start→start→end→end
        assert call_order == ["start", "end", "start", "end"]

    @pytest.mark.asyncio
    async def test_compress_truncates_long_message_content(self):
        """单条消息内容超过 500 字符时应被截断（不触发 Milvus 超长限制）"""
        stm = self._make_stm()
        msg = MagicMock()
        msg.type = "human"
        msg.content = "x" * 1000
        stm.get_messages = MagicMock(return_value=[msg] * 6)

        mock_response = MagicMock()
        mock_response.content = "摘要"
        stm._llm.ainvoke = AsyncMock(return_value=mock_response)

        await stm.compress("sess-1")
        call_args = stm._llm.ainvoke.call_args[0][0]
        user_message_content = call_args[1]["content"]
        # 每条截断为 500 字符，加角色前缀，不应超过总限制
        assert len(user_message_content) < 1000 * 6 + 100


# ─── LongTermMemory ───────────────────────────────────────────────────────────


class TestLongTermMemoryAddSummary:
    """情景记忆：写入"""

    def _make_ltm(self):
        mock_client = MagicMock()
        mock_client.has_collection = MagicMock(return_value=True)
        with patch("agent.memory.long_term.MilvusClient", return_value=mock_client):
            from agent.memory.long_term import LongTermMemory
            ltm = LongTermMemory()
        return ltm, mock_client

    @pytest.mark.asyncio
    async def test_add_summary_returns_none_without_embedder(self):
        ltm, _ = self._make_ltm()
        result = await ltm.add_summary("u1", "s1", "摘要文本")
        assert result is None

    @pytest.mark.asyncio
    async def test_add_summary_inserts_and_returns_id(self):
        ltm, mock_client = self._make_ltm()
        mock_embedder = MagicMock()
        mock_embedder.aembed_query = AsyncMock(return_value=[0.1] * 1536)
        ltm.set_embedder(mock_embedder)

        # 抑制 eviction 调用
        ltm._evict_oldest_if_needed = AsyncMock()

        result = await ltm.add_summary("u1", "s1", "这是摘要内容")
        assert result is not None
        assert mock_client.insert.called

    @pytest.mark.asyncio
    async def test_add_summary_returns_none_on_empty_text(self):
        ltm, _ = self._make_ltm()
        result = await ltm.add_summary("u1", "s1", "   ")
        assert result is None

    @pytest.mark.asyncio
    async def test_add_summary_truncates_text_over_limit(self):
        ltm, mock_client = self._make_ltm()
        mock_embedder = MagicMock()
        mock_embedder.aembed_query = AsyncMock(return_value=[0.1] * 1536)
        ltm.set_embedder(mock_embedder)
        ltm._evict_oldest_if_needed = AsyncMock()

        long_text = "a" * 70_000
        await ltm.add_summary("u1", "s1", long_text)

        inserted_data = mock_client.insert.call_args[1]["data"][0]
        assert len(inserted_data["content"]) <= 65_535

    @pytest.mark.asyncio
    async def test_add_summary_returns_none_on_milvus_error(self):
        ltm, mock_client = self._make_ltm()
        mock_embedder = MagicMock()
        mock_embedder.aembed_query = AsyncMock(return_value=[0.1] * 1536)
        ltm.set_embedder(mock_embedder)
        mock_client.insert = MagicMock(side_effect=RuntimeError("Milvus down"))

        result = await ltm.add_summary("u1", "s1", "摘要")
        assert result is None


class TestLongTermMemorySearch:
    """情景记忆：检索"""

    def _make_ltm(self):
        mock_client = MagicMock()
        mock_client.has_collection = MagicMock(return_value=True)
        with patch("agent.memory.long_term.MilvusClient", return_value=mock_client):
            from agent.memory.long_term import LongTermMemory
            ltm = LongTermMemory()
        return ltm, mock_client

    @pytest.mark.asyncio
    async def test_search_returns_empty_without_embedder(self):
        ltm, _ = self._make_ltm()
        result = await ltm.search_relevant("u1", "查询内容")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_returns_memory_summaries(self):
        ltm, mock_client = self._make_ltm()
        mock_embedder = MagicMock()
        mock_embedder.aembed_query = AsyncMock(return_value=[0.1] * 1536)
        ltm.set_embedder(mock_embedder)

        mock_client.search.return_value = [
            [
                {
                    "id": "rec-1",
                    "distance": 0.92,
                    "entity": {
                        "session_id": "sess-abc",
                        "content": "历史摘要内容",
                        "created_at": 1700000000,
                    },
                }
            ]
        ]

        from agent.memory.long_term import MemorySummary
        results = await ltm.search_relevant("u1", "RAG 检索")
        assert len(results) == 1
        assert isinstance(results[0], MemorySummary)
        assert results[0].score == pytest.approx(0.92)
        assert results[0].content == "历史摘要内容"

    @pytest.mark.asyncio
    async def test_search_returns_empty_on_milvus_error(self):
        ltm, mock_client = self._make_ltm()
        mock_embedder = MagicMock()
        mock_embedder.aembed_query = AsyncMock(return_value=[0.1] * 1536)
        ltm.set_embedder(mock_embedder)
        mock_client.search = MagicMock(side_effect=RuntimeError("Milvus down"))

        result = await ltm.search_relevant("u1", "查询")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_returns_empty_for_blank_query(self):
        ltm, _ = self._make_ltm()
        mock_embedder = MagicMock()
        ltm.set_embedder(mock_embedder)
        result = await ltm.search_relevant("u1", "   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_filters_low_score_results(self):
        """score < threshold 的结果应被过滤掉"""
        ltm, mock_client = self._make_ltm()
        mock_embedder = MagicMock()
        mock_embedder.aembed_query = AsyncMock(return_value=[0.1] * 1536)
        ltm.set_embedder(mock_embedder)

        mock_client.search.return_value = [
            [
                {
                    "id": "r1",
                    "distance": 0.3,   # 低于默认 threshold=0.5，应被过滤
                    "entity": {
                        "session_id": "s1",
                        "content": "低相关摘要",
                        "created_at": 1700000000,
                    },
                },
                {
                    "id": "r2",
                    "distance": 0.8,   # 高相关，保留
                    "entity": {
                        "session_id": "s2",
                        "content": "高相关摘要",
                        "created_at": 1700001000,
                    },
                },
            ]
        ]

        results = await ltm.search_relevant("u1", "查询")
        assert len(results) == 1
        assert results[0].content == "高相关摘要"

    @pytest.mark.asyncio
    async def test_search_results_sorted_by_time_ascending(self):
        """结果按 created_at 升序排列（旧→新），利用 LLM Recency Bias"""
        ltm, mock_client = self._make_ltm()
        mock_embedder = MagicMock()
        mock_embedder.aembed_query = AsyncMock(return_value=[0.1] * 1536)
        ltm.set_embedder(mock_embedder)

        mock_client.search.return_value = [
            [
                {
                    "id": "r_new",
                    "distance": 0.9,
                    "entity": {"session_id": "s2", "content": "新摘要", "created_at": 1700002000},
                },
                {
                    "id": "r_old",
                    "distance": 0.85,
                    "entity": {"session_id": "s1", "content": "旧摘要", "created_at": 1700000000},
                },
            ]
        ]

        results = await ltm.search_relevant("u1", "查询")
        assert results[0].content == "旧摘要"
        assert results[1].content == "新摘要"

    def test_delete_by_session_calls_milvus_delete(self):
        ltm, mock_client = self._make_ltm()
        mock_client.delete.return_value = {"delete_count": 2}
        count = ltm.delete_by_session("u1", "sess-abc")
        assert count == 2
        assert mock_client.delete.called

    def test_delete_all_for_user_calls_milvus_delete(self):
        ltm, mock_client = self._make_ltm()
        mock_client.delete.return_value = {"delete_count": 10}
        count = ltm.delete_all_for_user("u1")
        assert count == 10
        # filter 应只包含 user_id，不含 session_id
        call_filter = mock_client.delete.call_args[1]["filter"]
        assert "u1" in call_filter
        assert "session_id" not in call_filter


# ─── UserProfileManager ───────────────────────────────────────────────────────


class TestUserProfileManagerGetProfile:
    """用户画像：读取（缓存命中 / miss）"""

    def _make_upm(self, redis=None):
        with patch("agent.memory.user_profile.ChatOpenAI"):
            from agent.memory.user_profile import UserProfileManager
            return UserProfileManager(llm_model="gpt-4o-mini", redis_client=redis)

    @pytest.mark.asyncio
    async def test_get_profile_cache_hit_returns_profile_object(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps({
            "preferred_language": "zh",
            "expertise_level": "expert",
            "frequent_topics": ["深度学习"],
            "frequent_kb_ids": None,
            "preferences": None,
        }))
        upm = self._make_upm(redis=mock_redis)

        mock_db = AsyncMock()
        result = await upm.get_profile("u1", mock_db)

        assert result is not None
        assert result.preferred_language == "zh"
        assert result.expertise_level == "expert"
        # 缓存命中时不应查 DB
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_profile_cache_miss_queries_postgres(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # cache miss

        upm = self._make_upm(redis=mock_redis)

        from db.models import UserProfile
        profile = UserProfile(user_id="u1")
        profile.preferred_language = "zh"
        profile.expertise_level = "intermediate"
        profile.frequent_topics = None
        profile.frequent_kb_ids = None
        profile.preferences = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=profile)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await upm.get_profile("u1", mock_db)
        assert result is profile
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_profile_returns_none_when_not_exist(self):
        upm = self._make_upm()  # no redis

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await upm.get_profile("u1", mock_db)
        assert result is None


class TestUserProfileManagerUpsert:
    """用户画像：合并写入"""

    def _make_upm(self):
        with patch("agent.memory.user_profile.ChatOpenAI"):
            from agent.memory.user_profile import UserProfileManager
            return UserProfileManager()

    @pytest.mark.asyncio
    async def test_upsert_creates_new_profile_when_not_exist(self):
        upm = self._make_upm()
        from db.models import UserProfile

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock(side_effect=lambda p: None)

        profile = await upm.upsert_profile("u1", mock_db, expertise_level="expert")
        mock_db.add.assert_called_once()
        assert profile.expertise_level == "expert"

    @pytest.mark.asyncio
    async def test_upsert_merges_preferences_dict(self):
        upm = self._make_upm()
        from db.models import UserProfile

        existing = UserProfile(user_id="u1")
        existing.preferences = {"theme": "dark"}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=existing)
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock(side_effect=lambda p: None)

        await upm.upsert_profile("u1", mock_db, preferences={"lang": "zh"})
        # 合并后两个 key 都应存在
        assert existing.preferences == {"theme": "dark", "lang": "zh"}

    @pytest.mark.asyncio
    async def test_upsert_deduplicates_frequent_topics(self):
        upm = self._make_upm()
        from db.models import UserProfile

        existing = UserProfile(user_id="u1")
        existing.frequent_topics = ["RAG", "LLM"]

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=existing)
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock(side_effect=lambda p: None)

        await upm.upsert_profile("u1", mock_db, frequent_topics=["LLM", "Agent"])
        # LLM 已存在，不应重复
        assert existing.frequent_topics.count("LLM") == 1
        assert "Agent" in existing.frequent_topics


class TestUserProfileManagerFormatPrompt:
    """用户画像：Prompt 注入格式"""

    def _make_upm(self):
        with patch("agent.memory.user_profile.ChatOpenAI"):
            from agent.memory.user_profile import UserProfileManager
            return UserProfileManager()

    @pytest.mark.asyncio
    async def test_format_returns_empty_when_no_profile(self):
        upm = self._make_upm()
        upm.get_profile = AsyncMock(return_value=None)
        mock_db = AsyncMock()
        result = await upm.format_for_prompt("u1", mock_db)
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_includes_language_and_level(self):
        upm = self._make_upm()
        from db.models import UserProfile
        p = UserProfile(user_id="u1")
        p.preferred_language = "zh"
        p.expertise_level = "expert"
        p.frequent_topics = None

        upm.get_profile = AsyncMock(return_value=p)
        mock_db = AsyncMock()
        result = await upm.format_for_prompt("u1", mock_db)

        assert "中文" in result
        assert "专家" in result

    @pytest.mark.asyncio
    async def test_format_includes_topics(self):
        upm = self._make_upm()
        from db.models import UserProfile
        p = UserProfile(user_id="u1")
        p.preferred_language = "zh"
        p.expertise_level = "intermediate"
        p.frequent_topics = ["深度学习", "RAG", "Agent"]

        upm.get_profile = AsyncMock(return_value=p)
        mock_db = AsyncMock()
        result = await upm.format_for_prompt("u1", mock_db)

        assert "深度学习" in result
        assert "RAG" in result


# ─── MemoryManager ────────────────────────────────────────────────────────────


class TestMemoryManagerBuildContext:
    """记忆管理器：构建注入上下文"""

    def _make_manager(self):
        with patch("agent.memory.short_term.ChatOpenAI"), \
             patch("agent.memory.long_term.MilvusClient"), \
             patch("agent.memory.user_profile.ChatOpenAI"):
            from agent.memory.short_term import ShortTermMemory
            from agent.memory.long_term import LongTermMemory, MemorySummary
            from agent.memory.user_profile import UserProfileManager
            from agent.memory.memory_manager import MemoryManager

            stm = ShortTermMemory()
            ltm = LongTermMemory()
            upm = UserProfileManager()
            mgr = MemoryManager(short_term=stm, long_term=ltm, user_profile=upm)
            return mgr, stm, ltm, upm, MemorySummary

    @pytest.mark.asyncio
    async def test_build_context_empty_when_no_memories(self):
        mgr, stm, ltm, upm, _ = self._make_manager()
        ltm.search_relevant = AsyncMock(return_value=[])
        upm.format_for_prompt = AsyncMock(return_value="")
        mock_db = AsyncMock()

        result = await mgr.build_context("sess-1", "u1", "RAG 查询", mock_db)
        assert result == ""

    @pytest.mark.asyncio
    async def test_build_context_includes_episodic_summaries(self):
        mgr, stm, ltm, upm, MemorySummary = self._make_manager()
        ltm.search_relevant = AsyncMock(return_value=[
            MemorySummary(
                id="r1",
                session_id="s1",
                content="用户讨论了 LangGraph 状态管理",
                score=0.9,
                created_at=1700000000,
            )
        ])
        upm.format_for_prompt = AsyncMock(return_value="")
        mock_db = AsyncMock()

        result = await mgr.build_context("sess-1", "u1", "LangGraph", mock_db)
        assert "[历史记忆]" in result
        assert "LangGraph 状态管理" in result

    @pytest.mark.asyncio
    async def test_build_context_includes_user_profile(self):
        mgr, stm, ltm, upm, _ = self._make_manager()
        ltm.search_relevant = AsyncMock(return_value=[])
        upm.format_for_prompt = AsyncMock(return_value="[用户偏好] 语言：中文 | 专业水平：专家")
        mock_db = AsyncMock()

        result = await mgr.build_context("sess-1", "u1", "查询", mock_db)
        assert "[用户偏好]" in result
        assert "专家" in result

    @pytest.mark.asyncio
    async def test_build_context_graceful_on_ltm_failure(self):
        """情景记忆失败时不应影响用户画像注入"""
        mgr, stm, ltm, upm, _ = self._make_manager()
        ltm.search_relevant = AsyncMock(side_effect=RuntimeError("Milvus down"))
        upm.format_for_prompt = AsyncMock(return_value="[用户偏好] 语言：中文")
        mock_db = AsyncMock()

        result = await mgr.build_context("sess-1", "u1", "查询", mock_db)
        assert "[用户偏好]" in result  # 画像仍应注入


class TestMemoryManagerOnSessionEnd:
    """记忆管理器：会话结束处理"""

    def _make_manager(self):
        with patch("agent.memory.short_term.ChatOpenAI"), \
             patch("agent.memory.long_term.MilvusClient"), \
             patch("agent.memory.user_profile.ChatOpenAI"):
            from agent.memory.short_term import ShortTermMemory
            from agent.memory.long_term import LongTermMemory
            from agent.memory.user_profile import UserProfileManager
            from agent.memory.memory_manager import MemoryManager

            stm = ShortTermMemory()
            ltm = LongTermMemory()
            upm = UserProfileManager()
            return MemoryManager(short_term=stm, long_term=ltm, user_profile=upm), stm, ltm, upm

    @pytest.mark.asyncio
    async def test_on_session_end_calls_compress_and_persist(self):
        mgr, stm, ltm, upm = self._make_manager()
        stm.compress = AsyncMock(return_value="这是摘要")
        ltm.add_summary = AsyncMock(return_value="rec-id")
        stm.get_messages = MagicMock(return_value=[MagicMock()] * 6)
        upm.extract_and_update = AsyncMock()
        mock_db = AsyncMock()

        await mgr.on_session_end("sess-1", "u1", mock_db)

        stm.compress.assert_called_once_with("sess-1")
        ltm.add_summary.assert_called_once_with(
            user_id="u1", session_id="sess-1", summary_text="这是摘要"
        )
        upm.extract_and_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_session_end_skips_ltm_when_no_summary(self):
        mgr, stm, ltm, upm = self._make_manager()
        stm.compress = AsyncMock(return_value=None)  # 无摘要
        ltm.add_summary = AsyncMock()
        stm.get_messages = MagicMock(return_value=[])
        upm.extract_and_update = AsyncMock()
        mock_db = AsyncMock()

        await mgr.on_session_end("sess-1", "u1", mock_db)

        ltm.add_summary.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_session_end_continues_on_compress_failure(self):
        """压缩失败不阻断画像提取"""
        mgr, stm, ltm, upm = self._make_manager()
        stm.compress = AsyncMock(side_effect=RuntimeError("LLM down"))
        ltm.add_summary = AsyncMock()
        stm.get_messages = MagicMock(return_value=[MagicMock()] * 6)
        upm.extract_and_update = AsyncMock()
        mock_db = AsyncMock()

        await mgr.on_session_end("sess-1", "u1", mock_db)

        ltm.add_summary.assert_not_called()
        upm.extract_and_update.assert_called_once()

    def test_get_thread_config_delegates_to_short_term(self):
        mgr, stm, ltm, upm = self._make_manager()
        stm.get_thread_config = MagicMock(return_value={"configurable": {"thread_id": "s1"}})
        result = mgr.get_thread_config("s1")
        assert result == {"configurable": {"thread_id": "s1"}}
