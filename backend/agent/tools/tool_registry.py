"""
ToolRegistry — 工具注册表

使用方式：
    # 注册工具实例（在应用启动时调用）
    registry = ToolRegistry()
    registry.register(KBSearchTool(retriever=..., reranker=...))
    registry.register(CalculatorTool())

    # 按名查找实例
    tool = registry.get("kb_search")

    # 生成所有工具的 Function Calling schema（传给 LLM）
    schemas = registry.to_function_schemas()
"""
from __future__ import annotations

from typing import Any

from .base_tool import BaseTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册工具实例"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        """按名查找工具实例，不存在则抛 KeyError"""
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found. Available: {list(self._tools)}")
        return self._tools[name]
    
    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())
    
    def to_function_schemas(self) -> list[dict[str, Any]]:
        """返回所有工具的 OpenAI Function Calling schema 列表"""
        return [tool.to_function_schema() for tool in self._tools.values()]
    
    def __contains__(self, name: str) -> bool:
        return name in self._tools