"""
工具体系单元测试（全 mock，不依赖真实外部服务）

覆盖：
- CalculatorTool  — 纯逻辑，无外部依赖
- ToolRegistry    — 注册 / 查找 / schema 导出
- ToolExecutor    — 参数校验 / 超时 / 错误捕获 / 缓存读写（mock Redis）
- KBSearchTool    — mock Retriever + Reranker，验证多 KB 合并和截断

运行：
    uv run pytest backend/tests/agent/test_tools_unit.py -v
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.tools.base_tool import ToolResult
from agent.tools.builtin.calculator import CalculatorTool, _safe_eval
from agent.tools.builtin.kb_search import KBSearchTool
from agent.tools.tool_executor import ToolExecutor
from agent.tools.tool_registry import ToolRegistry
from retrieval.base import RetrievedChunk


# ─── 工具工厂 ─────────────────────────────────────────────────────────────────

def _make_chunk(text: str = "hello", score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        text=text,
        source_file="doc.pdf",
        section_path="",
        chunk_index_in_doc=0,
        fusion_score=score,
        rerank_score=score,
    )


def _make_kb_tool(chunks: list[RetrievedChunk], reranker=None) -> KBSearchTool:
    retriever = MagicMock()
    retriever.aretrieve_by_mode = AsyncMock(return_value=chunks)
    return KBSearchTool(retriever=retriever, reranker=reranker)


# ─── CalculatorTool ───────────────────────────────────────────────────────────

class TestCalculatorSafeEval:
    def test_basic_arithmetic(self):
        assert _safe_eval("2 + 3") == 5.0
        assert _safe_eval("10 - 4") == 6.0
        assert _safe_eval("3 * 4") == 12.0
        assert _safe_eval("10 / 4") == 2.5

    def test_power(self):
        assert _safe_eval("2 ** 10") == 1024.0

    def test_floor_div_and_mod(self):
        assert _safe_eval("10 // 3") == 3.0
        assert _safe_eval("10 % 3") == 1.0

    def test_nested_expression(self):
        assert _safe_eval("(3 + 4) * 2") == 14.0

    def test_constants_pi_e(self):
        result = _safe_eval("pi")
        assert abs(result - 3.14159) < 0.001
        result = _safe_eval("e")
        assert abs(result - 2.71828) < 0.001

    def test_exponent_too_large_raises(self):
        with pytest.raises(ValueError, match="指数过大"):
            _safe_eval("2 ** 9999")

    def test_unary_neg(self):
        assert _safe_eval("-5 + 10") == 5.0

    def test_unsupported_variable_raises(self):
        with pytest.raises(ValueError, match="不支持的变量"):
            _safe_eval("x + 1")

    def test_zero_division_raises(self):
        with pytest.raises(ZeroDivisionError):
            _safe_eval("1 / 0")


class TestCalculatorToolArun:
    @pytest.fixture
    def tool(self):
        return CalculatorTool()

    @pytest.mark.asyncio
    async def test_success_integer_display(self, tool):
        result = await tool._arun(expression="2 ** 10")
        assert result.is_error is False
        assert "1024" in result.content

    @pytest.mark.asyncio
    async def test_banned_keyword_rejected(self, tool):
        result = await tool._arun(expression="__import__('os')")
        assert result.is_error is True
        assert "不允许" in result.content

    @pytest.mark.asyncio
    async def test_syntax_error_returns_error(self, tool):
        # 未闭合括号 → 语法错误
        result = await tool._arun(expression="((2 + 3")
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_metadata_contains_result(self, tool):
        result = await tool._arun(expression="3 * 4")
        assert result.metadata["result"] == 12.0

    @pytest.mark.asyncio
    async def test_float_result_preserved(self, tool):
        result = await tool._arun(expression="1 / 3")
        assert result.is_error is False
        assert result.metadata["result"] != int(result.metadata["result"])


# ─── ToolRegistry ─────────────────────────────────────────────────────────────

class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        tool = CalculatorTool()
        reg.register(tool)
        assert reg.get("calculator") is tool

    def test_contains(self):
        reg = ToolRegistry()
        reg.register(CalculatorTool())
        assert "calculator" in reg
        assert "nonexistent" not in reg

    def test_get_missing_raises(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError, match="calculator"):
            reg.get("calculator")

    def test_list_tools(self):
        reg = ToolRegistry()
        reg.register(CalculatorTool())
        tools = reg.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "calculator"

    def test_to_function_schemas(self):
        reg = ToolRegistry()
        reg.register(CalculatorTool())
        schemas = reg.to_function_schemas()
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "calculator"
        assert "parameters" in schema["function"]

    def test_schema_no_title_at_top(self):
        """顶层 schema 不含 title（Pydantic 自动生成，会污染 OpenAI 结构）"""
        reg = ToolRegistry()
        reg.register(CalculatorTool())
        params = reg.to_function_schemas()[0]["function"]["parameters"]
        assert "title" not in params


# ─── ToolExecutor ─────────────────────────────────────────────────────────────

class TestToolExecutorNoCache:
    @pytest.fixture
    def executor(self):
        reg = ToolRegistry()
        reg.register(CalculatorTool())
        return ToolExecutor(registry=reg, redis_client=None)

    @pytest.mark.asyncio
    async def test_success_path(self, executor):
        result = await executor.execute("calculator", {"expression": "6 * 7"})
        assert result.is_error is False
        assert "42" in result.content

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, executor):
        result = await executor.execute("nonexistent", {})
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_invalid_args_returns_error(self, executor):
        # expression 是必填字段，传空 dict 会导致校验失败
        result = await executor.execute("calculator", {})
        assert result.is_error is True
        assert "参数校验失败" in result.content

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, executor):
        """工具超时应返回 is_error=True 而不是抛异常"""
        # 用 patch 让 calculator._arun 永远挂起
        async def _hang(self_inner, **kwargs):  # noqa: N802
            await asyncio.sleep(999)

        with patch.object(CalculatorTool, "_arun", _hang):
            # 临时把 timeout 改为 0.05s
            calc = executor._registry.get("calculator")
            original = calc.timeout
            calc.timeout = 0.05
            try:
                result = await executor.execute("calculator", {"expression": "1+1"})
            finally:
                calc.timeout = original

        assert result.is_error is True
        assert "超时" in result.content


class TestToolExecutorWithMockRedis:
    @pytest.fixture
    def mock_redis(self):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)   # 默认 cache miss
        redis.set = AsyncMock()
        return redis

    @pytest.fixture
    def executor(self, mock_redis):
        reg = ToolRegistry()
        reg.register(CalculatorTool())
        return ToolExecutor(registry=reg, redis_client=mock_redis)

    @pytest.mark.asyncio
    async def test_cache_miss_then_write(self, executor, mock_redis):
        result = await executor.execute("calculator", {"expression": "2 + 2"})
        assert result.is_error is False
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_execution(self, executor, mock_redis):
        cached = ToolResult(tool_name="calculator", content="2 + 2 = 4", metadata={"result": 4.0})
        mock_redis.get = AsyncMock(return_value=cached.model_dump_json())

        with patch.object(CalculatorTool, "_arun") as mock_run:
            result = await executor.execute("calculator", {"expression": "2 + 2"})
            mock_run.assert_not_called()

        assert result.content == "2 + 2 = 4"

    @pytest.mark.asyncio
    async def test_error_result_not_cached(self, executor, mock_redis):
        result = await executor.execute("calculator", {"expression": "1 / 0"})
        assert result.is_error is True
        mock_redis.set.assert_not_called()


# ─── KBSearchTool ─────────────────────────────────────────────────────────────

class TestKBSearchTool:
    @pytest.mark.asyncio
    async def test_returns_chunks(self):
        chunks = [_make_chunk("Python 是好语言"), _make_chunk("RAG 是检索增强")]
        tool = _make_kb_tool(chunks)
        result = await tool._arun(query="Python", kb_ids=["kb1"])
        assert result.is_error is False
        assert "Python" in result.content
        assert len(result.metadata["chunks"]) == 2

    @pytest.mark.asyncio
    async def test_empty_result(self):
        tool = _make_kb_tool([])
        result = await tool._arun(query="xyz", kb_ids=["kb1"])
        assert result.is_error is False
        assert "未在知识库中找到" in result.content

    @pytest.mark.asyncio
    async def test_multiple_kb_ids(self):
        """多知识库：每个 kb_id 各调用一次 retriever"""
        chunks = [_make_chunk("chunk")]
        tool = _make_kb_tool(chunks)
        await tool._arun(query="q", kb_ids=["kb1", "kb2"])
        assert tool._retriever.aretrieve_by_mode.call_count == 2

    @pytest.mark.asyncio
    async def test_context_truncation(self):
        """超过 8000 字符时内容应被截断"""
        long_text = "A" * 500
        chunks = [_make_chunk(long_text) for _ in range(30)]
        tool = _make_kb_tool(chunks)
        result = await tool._arun(query="q", kb_ids=["kb1"], top_k=20)
        assert "截断" in result.content
        assert len(result.content) < 30 * 500

    @pytest.mark.asyncio
    async def test_reranker_called_when_provided(self):
        chunks = [_make_chunk("a"), _make_chunk("b")]
        reranker = MagicMock()
        reranker.arerank = AsyncMock(return_value=chunks[:1])
        tool = KBSearchTool(
            retriever=MagicMock(aretrieve_by_mode=AsyncMock(return_value=chunks)),
            reranker=reranker,
        )
        result = await tool._arun(query="q", kb_ids=["kb1"])
        reranker.arerank.assert_called_once()
        assert len(result.metadata["chunks"]) == 1
