"""
工具体系集成测试（真实 Redis + 真实 Tavily API）

依赖：
- backend/.env 中 CELERY_BROKER_URL（Redis 连接串）
- backend/.env 中 API_TAVILY_API_KEY

运行：
    uv run pytest backend/tests/agent/test_tools_integration.py -v -m integration

CI 默认跳过（需 -m integration 显式触发）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

pytestmark = pytest.mark.integration

# ─── 共享 fixture ─────────────────────────────────────────────────────────────

@pytest.fixture
async def redis_client():
    """
    使用 DB=15 做测试，与 Celery（DB=0/1）隔离。

    注意：teardown 不做任何 await，避免 pytest-asyncio function-scope event loop
    在测试结束后关闭导致 RuntimeError。清理在 setup 阶段（yield 前）完成，保证幂等。
    """
    import redis.asyncio as aioredis
    raw_url = os.environ["CELERY_BROKER_URL"]
    url = raw_url.rsplit("/", 1)[0] + "/15"
    client = aioredis.from_url(url, decode_responses=True)
    # 预清理：删除上次测试可能残留的 key
    keys = await client.keys("tool:*")
    if keys:
        await client.delete(*keys)
    yield client
    # teardown: 不做 await，event loop 此时已关闭


@pytest.fixture(scope="module")
def tavily_api_key() -> str:
    key = os.getenv("TAVILY_API_KEY") or os.getenv("API_TAVILY_API_KEY", "")
    if not key:
        pytest.skip("TAVILY_API_KEY 未配置，跳过 Tavily 集成测试")
    return key


# ─── WebSearchTool × 真实 Tavily ─────────────────────────────────────────────

class TestWebSearchIntegration:
    @pytest.mark.asyncio
    async def test_basic_search_returns_results(self, tavily_api_key):
        from agent.tools.builtin.web_search import WebSearchTool

        tool = WebSearchTool(api_key=tavily_api_key)
        result = await tool._arun(query="Python programming language", max_results=3)

        assert result.is_error is False
        assert len(result.content) > 0
        assert result.metadata.get("results")
        assert len(result.metadata["results"]) >= 1

    @pytest.mark.asyncio
    async def test_client_reuse(self, tavily_api_key):
        """懒加载：多次调用应复用同一个 client 实例"""
        from agent.tools.builtin.web_search import WebSearchTool

        tool = WebSearchTool(api_key=tavily_api_key)
        assert tool._client is None

        await tool._arun(query="test", max_results=1)
        client_first = tool._client

        await tool._arun(query="hello", max_results=1)
        client_second = tool._client

        assert client_first is client_second   # 同一实例

    @pytest.mark.asyncio
    async def test_content_truncated_when_long(self, tavily_api_key):
        """大量结果不应超过 6000 字符限制"""
        from agent.tools.builtin.web_search import WebSearchTool

        tool = WebSearchTool(api_key=tavily_api_key)
        result = await tool._arun(query="artificial intelligence", max_results=10)

        assert len(result.content) <= 6500   # 允许少量超出（截断提示本身的字符）


# ─── ToolExecutor × 真实 Redis 缓存 ──────────────────────────────────────────

class TestToolExecutorCacheIntegration:
    @pytest.fixture
    def executor(self, redis_client):
        from agent.tools.builtin.calculator import CalculatorTool
        from agent.tools.tool_executor import ToolExecutor
        from agent.tools.tool_registry import ToolRegistry

        reg = ToolRegistry()
        reg.register(CalculatorTool())
        return ToolExecutor(registry=reg, redis_client=redis_client)

    @pytest.mark.asyncio
    async def test_cache_miss_then_hit(self, executor):
        """第一次执行写缓存，第二次直接命中"""
        args = {"expression": "123 * 456"}

        # 第一次：cache miss，真实计算
        r1 = await executor.execute("calculator", args)
        assert r1.is_error is False
        assert "56088" in r1.content

        # 第二次：应命中 Redis 缓存，结果相同
        r2 = await executor.execute("calculator", args)
        assert r2.content == r1.content

    @pytest.mark.asyncio
    async def test_different_args_different_cache_key(self, executor):
        """不同参数不共享缓存"""
        r1 = await executor.execute("calculator", {"expression": "10 + 1"})
        r2 = await executor.execute("calculator", {"expression": "10 + 2"})
        assert r1.content != r2.content

    @pytest.mark.asyncio
    async def test_error_result_not_persisted(self, executor, redis_client):
        """错误结果不应写入 Redis"""
        await executor.execute("calculator", {"expression": "1 / 0"})
        keys = await redis_client.keys("tool:calculator:*")
        # 只有之前成功的结果会在缓存里，不应有新增的错误结果 key
        # （验证方式：错误 key 不存在，或 key 数量未因本次调用增长）
        for key in keys:
            raw = await redis_client.get(key)
            import json
            data = json.loads(raw)
            assert data["is_error"] is False, f"发现缓存了错误结果：{key}"
