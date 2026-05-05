"""
BaseTool — 工具抽象基类

设计参考：
- CrewAI BaseTool：Pydantic BaseModel + ABC，自动推导 args_schema
- Dify ToolInvokeMessage：类型化输出（text / json / error）

每个工具继承 BaseTool，实现 _arun(**kwargs) → ToolResult。
args_schema 通过 @classmethod get_args_schema() 返回 Pydantic 模型，
供 to_function_schema() 序列化为 OpenAI Function Calling JSON。
"""
from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel


class ToolResult(BaseModel):
    """Tool 调用结果"""
    tool_name: str
    content: str                        # 主要文本输出（供LLM 消费）
    metadata: dict[str, Any] = {}       # 附加数据（来源、分数等）
    is_error: bool = False


class BaseTool(ABC):
    """
    工具抽象基类

    子类必须：
    1. 定义类属性 name / description
    2. 实现 _arun(**kwargs) → ToolResult
    3. 实现 get_args_schema() → type[BaseModel]，返回参数 Pydantic 模型

    可选覆盖：
    - cacheable: bool — 是否允许 ToolExecutor 缓存结果（默认 True）
    - timeout: int      — 执行超时秒数（默认 30）
    """

    name: str
    description: str
    cacheable: bool = True
    timeout: int = 30

    @classmethod
    @abstractmethod
    def get_args_schema(cls) -> type[BaseModel]:
        """
        返回 Tool 参数描述的 Pydantic 模型类
        """
    
    @abstractmethod
    async def _arun(self, **kwargs: Any) -> ToolResult:
        """
        Tool 核心执行逻辑（异步）
        """

    def to_function_schema(self) -> dict[str, Any]:
        """
        返回 OpenAI Function Calling 格式的工具 schema：
        {
            "type": "function",
            "function": { "name": ..., "description": ..., "parameters": ... }
        }
        """
        schema = self.get_args_schema().model_json_schema()

        # 移除 title/description
        schema.pop("title", None)
        schema.pop("description", None)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema
            }
        }
    
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 校验子类是否定义了 name / description
        for attr in ("name", "description"):
            if not hasattr(cls, attr) or not isinstance(getattr(cls, attr, None), str):
                # 抽象子类（还有 abstractmethod 未实现）跳过校验
                if inspect.isabstract(cls):
                    continue
                raise TypeError(f"{cls.__name__} must define a string class attribute '{attr}'")
