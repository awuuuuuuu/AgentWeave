"""
Crew（应急指挥）图状态定义

与 AgentState 完全独立——Crew Supervisor 是并发 A2A 编排图，
不做 RAG 检索路由，State 字段完全不同。
"""
from __future__ import annotations

from typing import Annotated, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class PlanStep(TypedDict):
    step_id: str
    title: str          # 人类可读的步骤名称
    dept_code: str      # 执行部门代码（对应 A2A server）
    task: str           # 给该部门的具体任务描述
    is_high_risk: bool  # True → 执行前需 HITL-2 审批
    status: str         # pending | approved | running | done | failed | skipped
    map_layer: Optional[str]       # 对应地图图层 ID（有地理操作时非 None）
    result_summary: str            # 执行完成后填入


class CrewState(TypedDict):
    # ── 会话元数据 ──────────────────────────────────────────────────────────
    session_id: str
    user_id: str

    # ── 事故输入 ────────────────────────────────────────────────────────────
    incident: str                       # 用户输入的事故描述
    selected_dept_codes: list[str]      # 指挥官选定的参与部门

    # ── 阶段数据 ────────────────────────────────────────────────────────────
    dept_reports: dict[str, dict]       # dept_code → A2ATaskResponse dict
    dispatch_plan: list[PlanStep]       # LLM 生成的执行计划
    current_step: int                   # 当前执行步骤索引
    map_events: list[dict]              # 累积地图事件（供 SSE 推流）
    phase: str                          # dispatch | plan_review | executing | done

    # ── 消息历史（保留用户追加指令通道）──────────────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]
