"""
ToolExecutor — 工具执行引擎

职责：
1. 从 ToolRegistry 查找工具实例
2. 参数校验（通过工具的 args_schema Pydantic 模型）
3. Redis 结果缓存（key = tool:{name}:{sha256(json(args))}，TTL 300s）
4. asyncio.wait_for 超时保护
5. 异常捕获 → ToolResult(is_error=True)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from .base_tool import ToolResult
from .tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, redis_client: Any | None = None) -> None:
        """
         Args:
            registry:     工具注册表
            redis_client: redis异步客户端（可选，None 则跳过缓存）
        """
        self._registry = registry
        self._redis = redis_client

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """
        工具的执行

        流程：
        1. 查找工具
        2. 参数校验
        3. 命中缓存则直接返回
        4. 执行（含超时）
        5. 写缓存
        """
        try:
            tool = self._registry.get(tool_name)
        except KeyError as e:
            return ToolResult(tool_name=tool_name, content=str(e), is_error=True)
        
        try:
            schema_cls = tool.get_args_schema()
            validated = schema_cls(**arguments)
            validated_args = validated.model_dump()
        except Exception as e:
            return ToolResult(
                tool_name=tool_name,
                content=f"参数校验失败: {e}",
                is_error=True,
            )
        
        cache_key = self._make_cache_key(tool_name, validated_args)
        if tool.cacheable and self._redis is not None:
            cached = await self._read_cache(cache_key)
            if cached is not None:
                logger.debug("ToolExecutor cache hit: %s", cache_key)
                return cached
            
        try:
            result = await asyncio.wait_for(
                tool._arun(**validated_args),
                timeout=tool.timeout
            )
        except asyncio.TimeoutError:
            result = ToolResult(
                tool_name=tool_name,
                content=f"工具 '{tool_name}' 执行超时（>{tool.timeout}s），请尝试缩小查询范围或稍后重试。",
                is_error=True,
            )
        except Exception as e:
            logger.exception("ToolExecutor: tool '%s' raised an exception", tool_name)
            result = ToolResult(
                tool_name=tool_name,
                content=f"工具执行异常: {e}",
                is_error=True,
            )

        if tool.cacheable and self._redis is not None and not result.is_error:
            await self._write_cache(cache_key, result)

        return result
    
    @staticmethod
    def _make_cache_key(tool_name: str, args: dict[str, Any]) -> str:
        args_hash = hashlib.sha256(
            json.dumps(args, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:32]
        return f"tool:{tool_name}:{args_hash}"
    
    async def _read_cache(self, key: str) -> ToolResult | None:
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return ToolResult.model_validate_json(raw)
        except Exception:
            logger.warning("ToolExecutor: cache read error for key %s", key, exc_info=True)
            return None

    async def _write_cache(self, key: str, result: ToolResult, ttl: int = 300) -> None:
        try:
            await self._redis.set(key, result.model_dump_json(), ex=ttl)
        except Exception:
            logger.warning("ToolExecutor: cache write error for key %s", key, exc_info=True)
