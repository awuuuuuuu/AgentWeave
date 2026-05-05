"""
WebSearchTool — 联网搜索工具

使用 Tavily Search API，适合回答时效性强、知识库中没有的问题。
需要环境变量 TAVILY_API_KEY。
"""
from __future__ import annotations

import logging
from typing import Annotated

from pydantic import BaseModel, Field

from agent.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class WebSearchArgs(BaseModel):
    query: Annotated[str, Field(description="搜索查询语句")]
    max_results: Annotated[
        int, Field(description="返回最大结果数", ge=1, le=10)
    ] = 5


class WebSearchTool(BaseTool):
    """联网搜索，获取互联网上的实时信息"""

    name = "web_search"
    description = (
        "在互联网上搜索实时信息。"
        "适合回答最新事件、时效性内容，或知识库中没有的公开信息。"
    )
    cacheable = True
    timeout = 20

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: object | None = None   # 懒加载，首次 _arun 时初始化

    @classmethod
    def get_args_schema(cls) -> type[BaseModel]:
        return WebSearchArgs

    def _get_client(self) -> object:
        """懒加载 AsyncTavilyClient，避免模块级 ImportError 和每次请求重建开销"""
        if self._client is None:
            try:
                from tavily import AsyncTavilyClient  # type: ignore[import]
            except ImportError as e:
                raise RuntimeError("Tavily 未安装，请执行 `pip install tavily-python`") from e
            self._client = AsyncTavilyClient(api_key=self._api_key)
        return self._client

    async def _arun(self, query: str, max_results: int = 5) -> ToolResult:
        try:
            client = self._get_client()
        except RuntimeError as e:
            return ToolResult(tool_name=self.name, content=str(e), is_error=True)

        try:
            response = await client.search(
                query=query,
                max_results=max_results,
                search_depth="basic",
            )
        except Exception as e:
            logger.exception("WebSearchTool: Tavily API error")
            return ToolResult(tool_name=self.name, content=f"搜索失败: {e}", is_error=True)

        results = response.get("results", [])
        if not results:
            return ToolResult(
                tool_name=self.name,
                content="搜索未返回结果。",
                metadata={"query": query},
            )

        _MAX_CHARS = 6000
        lines: list[str] = []
        records: list[dict] = []
        total_chars = 0
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("url", "")
            content = r.get("content", "")
            chunk_str = f"[{i}] {title}\n{content}\n来源：{url}"
            if total_chars + len(chunk_str) > _MAX_CHARS:
                lines.append("... （已截断更多结果）")
                break
            lines.append(chunk_str)
            total_chars += len(chunk_str)
            records.append({"title": title, "url": url, "content": content})

        return ToolResult(
            tool_name=self.name,
            content="\n\n".join(lines),
            metadata={"results": records, "query": query},
        )
