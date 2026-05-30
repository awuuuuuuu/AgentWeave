# backend/tests/weave/conftest.py
"""Weave 测试套件共享 fixtures 和 pytest marker 注册。"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

# 所有场景常量统一从 evaluators 导入，conftest 不再重复定义
from tests.weave.evaluators import (
    INCIDENT,
    SELECTED_DEPTS,
    TRAFFIC_ACCIDENT_INCIDENT,
    TRAFFIC_DEPTS,
    HAZMAT_INCIDENT,
    HAZMAT_DEPTS,
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "weave_unit: 无外部依赖，CI 默认运行"
    )
    config.addinivalue_line(
        "markers",
        "weave_integration: 需要 A2A Server 在线（localhost:9101-9105），手动触发",
    )


A2A_PORTS: dict[str, int] = {
    "env_agency": 9101, "medical_ems": 9102, "traffic_control": 9103,
    "emergency_supplies": 9104, "fire_brigade": 9105,
}


@pytest.fixture(scope="session")
def a2a_available() -> dict[str, int]:
    """
    检查所有 A2A Server 连通性。
    任意 server 不可达则整批 weave_integration 测试 skip。
    返回 {dept_code: port} 供下游 fixture 使用。
    """
    async def _check():
        unreachable = []
        async with httpx.AsyncClient(timeout=5) as c:
            for dept, port in A2A_PORTS.items():
                try:
                    r = await c.get(f"http://localhost:{port}/.well-known/agent.json")
                    r.raise_for_status()
                except Exception:
                    unreachable.append(f"{dept}:{port}")
        return unreachable

    unreachable = asyncio.run(_check())
    if unreachable:
        pytest.skip(f"A2A Server 不可达，跳过集成测试: {unreachable}")
    return A2A_PORTS


# ── 通用：研判 + 计划生成工厂函数 ────────────────────────────────────────────

async def _fetch_dept_responses_for(
    incident: str,
    selected_depts: list[str],
) -> dict[str, dict]:
    """向指定部门的 A2A Server 发送研判任务，返回响应字典。"""
    from agent.graph.weave_supervisor import _generate_dept_tasks_llm
    dept_tasks = await _generate_dept_tasks_llm(incident, selected_depts)

    async def call_one(dept_code: str, port: int) -> tuple[str, dict]:
        payload = {
            "task_id": str(uuid.uuid4()),
            "task": dept_tasks[dept_code],
            "timeout_sec": 180,
        }
        async with httpx.AsyncClient(timeout=210) as c:
            r = await c.post(f"http://localhost:{port}/a2a/tasks/send", json=payload)
            r.raise_for_status()
            return dept_code, r.json()

    pairs = await asyncio.gather(
        *[call_one(dept, A2A_PORTS[dept]) for dept in selected_depts if dept in A2A_PORTS],
        return_exceptions=True,
    )
    results: dict[str, dict] = {}
    for p in pairs:
        if isinstance(p, BaseException):
            raise RuntimeError(f"A2A 调用失败: {p}") from p
        dept_code, resp = p
        results[dept_code] = resp
    return results


async def _build_weave_plan_for(
    dept_reports: dict[str, dict],
    incident: str,
    selected_depts: list[str],
) -> list[dict]:
    """调用 phase_aggregate 生成执行计划（adispatch_custom_event mock 掉）。"""
    from agent.graph.weave_supervisor import phase_aggregate

    state = {
        "session_id": "test-session",
        "user_id": "test-user",
        "incident": incident,
        # 测试用固定坐标（世纪大道×陆家嘴环路），与 evaluators.py 中 INCIDENT 保持一致
        "incident_lat": 31.2380,
        "incident_lng": 121.4970,
        "incident_location_name": "世纪大道×陆家嘴环路",
        "selected_dept_codes": selected_depts,
        "dept_reports": dept_reports,
        "dispatch_plan": [],
        "current_step": 0,
        "map_events": [],
        "phase": "plan_review",
        "messages": [],
    }
    with patch(
        "agent.graph.weave_supervisor.adispatch_custom_event",
        new=AsyncMock(return_value=None),
    ):
        result = await phase_aggregate(state, config={"configurable": {}})
    return result.get("dispatch_plan", [])


async def _run_execution_for(
    plan: list[dict],
    a2a_urls: dict[str, str],
) -> dict[str, tuple[dict, dict]]:
    """
    对计划中有写操作 MCP 要求的步骤直接调用 A2A Server 执行。
    返回 {step_id: (step, a2a_response)}。
    """
    from agent.graph.weave_supervisor import _call_dept_a2a

    steps_to_run = [
        s for s in plan
        if s.get("execution_tool")
        and s.get("status", "pending") in ("pending", "approved")
    ]

    if not steps_to_run:
        return {}

    async def run_one(step: dict) -> tuple[str, dict, dict]:
        execution_context: dict = {}
        if step.get("execution_tool"):
            execution_context["execution_intent"] = {
                "tool_name": step["execution_tool"],
                "params": step.get("execution_params") or {},
            }
        result = await _call_dept_a2a(
            step["dept_code"], step["task"], a2a_urls=a2a_urls,
            context=execution_context,
        )
        return step["step_id"], step, result

    raw = await asyncio.gather(*[run_one(s) for s in steps_to_run], return_exceptions=True)
    out: dict[str, tuple[dict, dict]] = {}
    for r in raw:
        if isinstance(r, BaseException):
            raise RuntimeError(f"执行阶段 A2A 调用失败: {r}") from r
        step_id, step, result = r
        out[step_id] = (step, result)
    return out


# ── 场景 1：建筑火灾（5 部门全量）────────────────────────────────────────────

@pytest.fixture(scope="module")
def dept_responses(a2a_available) -> dict[str, dict]:
    """研判阶段：火灾场景各部门响应，每个测试模块共享一次调用。"""
    return asyncio.run(_fetch_dept_responses_for(INCIDENT, SELECTED_DEPTS))


@pytest.fixture(scope="module")
def weave_plan(dept_responses) -> list[dict]:
    """执行计划：火灾场景 phase_aggregate 输出，每个测试模块共享一次 LLM 调用。"""
    return asyncio.run(_build_weave_plan_for(dept_responses, INCIDENT, SELECTED_DEPTS))


@pytest.fixture(scope="module")
def execution_results(weave_plan, a2a_available) -> dict[str, tuple[dict, dict]]:
    """执行阶段结果，每个测试模块共享一次。"""
    a2a_urls = {dept: f"http://localhost:{port}" for dept, port in A2A_PORTS.items()}
    return asyncio.run(_run_execution_for(weave_plan, a2a_urls))


# ── 场景 2：交通事故（traffic_control + medical_ems）────────────────────────

@pytest.fixture(scope="module")
def traffic_dept_responses(a2a_available) -> dict[str, dict]:
    """研判阶段：交通事故场景（TR + ME）各部门响应。"""
    return asyncio.run(_fetch_dept_responses_for(TRAFFIC_ACCIDENT_INCIDENT, TRAFFIC_DEPTS))


@pytest.fixture(scope="module")
def traffic_weave_plan(traffic_dept_responses) -> list[dict]:
    """执行计划：交通事故场景 phase_aggregate 输出。"""
    return asyncio.run(
        _build_weave_plan_for(traffic_dept_responses, TRAFFIC_ACCIDENT_INCIDENT, TRAFFIC_DEPTS)
    )


# ── 场景 5：危化品泄漏（EN + FF + ME + TR）──────────────────────────────────

@pytest.fixture(scope="module")
def hazmat_dept_responses(a2a_available) -> dict[str, dict]:
    """研判阶段：危化品泄漏场景（4 部门）各部门响应。"""
    return asyncio.run(_fetch_dept_responses_for(HAZMAT_INCIDENT, HAZMAT_DEPTS))


@pytest.fixture(scope="module")
def hazmat_weave_plan(hazmat_dept_responses) -> list[dict]:
    """执行计划：危化品泄漏场景 phase_aggregate 输出。"""
    return asyncio.run(
        _build_weave_plan_for(hazmat_dept_responses, HAZMAT_INCIDENT, HAZMAT_DEPTS)
    )


@pytest.fixture(scope="module")
def traffic_execution_results(traffic_weave_plan, a2a_available) -> dict[str, tuple[dict, dict]]:
    """执行阶段结果：交通事故场景（traffic_control + medical_ems）。"""
    a2a_urls = {dept: f"http://localhost:{port}" for dept, port in A2A_PORTS.items()}
    return asyncio.run(_run_execution_for(traffic_weave_plan, a2a_urls))


@pytest.fixture(scope="module")
def hazmat_execution_results(hazmat_weave_plan, a2a_available) -> dict[str, tuple[dict, dict]]:
    """执行阶段结果：危化品泄漏场景（env_agency + fire_brigade + medical_ems + traffic_control）。"""
    a2a_urls = {dept: f"http://localhost:{port}" for dept, port in A2A_PORTS.items()}
    return asyncio.run(_run_execution_for(hazmat_weave_plan, a2a_urls))
