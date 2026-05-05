"""
Tools API — 工具调试接口

GET  /api/tools/           列出所有已注册工具及其 Function Calling schema
POST /api/tools/{name}/execute  手动触发工具执行（调试用，需要登录）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agent.tools.base_tool import ToolResult
from auth.dependencies import get_current_user
from db.models import User

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolInfo(BaseModel):
    name: str
    description: str
    cacheable: bool
    timeout: int
    parameters_schema: dict[str, Any]


class ExecuteRequest(BaseModel):
    arguments: dict[str, Any] = {}


@router.get("/", response_model=list[ToolInfo])
async def list_tools(
    request: Request,
    _: User = Depends(get_current_user),
) -> list[ToolInfo]:
    """列出所有已注册工具（含参数 Schema）"""
    registry = request.app.state.tool_registry
    infos: list[ToolInfo] = []
    for tool in registry.list_tools():
        parameters_schema = tool.to_function_schema()["function"]["parameters"]
        infos.append(
            ToolInfo(
                name=tool.name,
                description=tool.description,
                cacheable=tool.cacheable,
                timeout=tool.timeout,
                parameters_schema=parameters_schema,
            )
        )
    return infos


@router.post("/{tool_name}/execute", response_model=ToolResult)
async def execute_tool(
    tool_name: str,
    body: ExecuteRequest,
    request: Request,
    _: User = Depends(get_current_user),
) -> ToolResult:
    """
    手动执行指定工具（调试用）。

    设计说明：工具执行失败（参数错误、超时等）仍返回 HTTP 200，
    错误信息封装在 ToolResult.is_error=True 中。
    这与 Agent Function Calling 语义一致：LLM 需通过 200 响应读取错误以自我纠正。
    """
    executor = request.app.state.tool_executor
    registry = request.app.state.tool_registry

    if tool_name not in registry:
        raise HTTPException(status_code=404, detail=f"工具 '{tool_name}' 不存在")

    result = await executor.execute(tool_name=tool_name, arguments=body.arguments)
    return result
