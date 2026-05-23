# backend/tests/crew/conftest.py
"""Crew 测试套件共享 fixtures 和 pytest marker 注册。"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "crew_unit: 无外部依赖，CI 默认运行"
    )
    config.addinivalue_line(
        "markers",
        "crew_integration: 需要 A2A Server 在线（localhost:9001-9005），手动触发",
    )


INCIDENT = "港城大道388号化工厂液氨储罐泄漏，风速4.2m/s，风向东南，已有3人中毒"
SELECTED_DEPTS = [
    "env_agency", "medical_ems", "traffic_control",
    "emergency_supplies", "enterprise_safety",
]
A2A_PORTS: dict[str, int] = {
    "env_agency": 9001, "medical_ems": 9002, "traffic_control": 9003,
    "emergency_supplies": 9004, "enterprise_safety": 9005,
}


@pytest.fixture(scope="session")
def a2a_available() -> dict[str, int]:
    """
    检查所有 A2A Server 连通性。
    任意 server 不可达则整批 crew_integration 测试 skip。
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


# ── 研判阶段响应（module-scoped，每个测试模块只调用一次） ──────────────────────

async def _fetch_dept_responses() -> dict[str, dict]:
    """直接向各部门 A2A Server 发送研判任务，返回响应字典。"""
    from agent.graph.crew_supervisor import _generate_dept_tasks_llm
    dept_tasks = await _generate_dept_tasks_llm(INCIDENT, SELECTED_DEPTS)

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
        *[call_one(dept, port) for dept, port in A2A_PORTS.items()],
        return_exceptions=True,
    )
    results: dict[str, dict] = {}
    for p in pairs:
        if isinstance(p, BaseException):
            raise RuntimeError(f"A2A 调用失败: {p}") from p
        dept_code, resp = p
        results[dept_code] = resp
    return results


@pytest.fixture(scope="module")
def dept_responses(a2a_available) -> dict[str, dict]:
    """研判阶段：各部门 A2ATaskResponse dict，每个测试模块共享一次调用结果。"""
    return asyncio.run(_fetch_dept_responses())


# ── 执行计划（module-scoped） ─────────────────────────────────────────────────

async def _build_crew_plan(dept_reports: dict[str, dict]) -> list[dict]:
    """调用 phase_aggregate 生成执行计划（adispatch_custom_event mock 掉）。"""
    from agent.graph.crew_supervisor import phase_aggregate

    state = {
        "session_id": "test-session",
        "user_id": "test-user",
        "incident": INCIDENT,
        "selected_dept_codes": SELECTED_DEPTS,
        "dept_reports": dept_reports,
        "dispatch_plan": [],
        "current_step": 0,
        "map_events": [],
        "phase": "plan_review",
        "messages": [],
    }
    with patch(
        "agent.graph.crew_supervisor.adispatch_custom_event",
        new=AsyncMock(return_value=None),
    ):
        result = await phase_aggregate(state, config={"configurable": {}})
    return result.get("dispatch_plan", [])


@pytest.fixture(scope="module")
def crew_plan(dept_responses) -> list[dict]:
    """执行计划：phase_aggregate 的输出，每个测试模块共享一次 LLM 调用。"""
    return asyncio.run(_build_crew_plan(dept_responses))


# ── 执行阶段结果（module-scoped） ─────────────────────────────────────────────

async def _run_execution(plan: list[dict]) -> dict[str, tuple[dict, dict]]:
    """
    对计划中有写操作 MCP 要求的步骤，直接调用 A2A Server 执行任务。
    返回 {step_id: (step, a2a_response)}。
    """
    from agent.graph.crew_supervisor import _call_dept_a2a

    EXECUTION_MCP_WHITELIST: dict[str, list[str]] = {
        "medical_ems":        ["dispatch_ambulance", "recall_ambulance"],
        "traffic_control":    ["set_mode", "apply_evacuation_plan"],
        "emergency_supplies": ["allocate_custom", "allocate_standard_pack"],
        "env_agency":         [],
        "enterprise_safety":  [],
    }

    steps_to_run = [
        s for s in plan
        if EXECUTION_MCP_WHITELIST.get(s.get("dept_code", "")) is not None
        and EXECUTION_MCP_WHITELIST[s["dept_code"]]
        and s.get("status", "pending") in ("pending", "approved")
    ]

    if not steps_to_run:
        return {}

    async def run_one(step: dict) -> tuple[str, dict, dict]:
        result = await _call_dept_a2a(step["dept_code"], step["task"])
        return step["step_id"], step, result

    raw = await asyncio.gather(*[run_one(s) for s in steps_to_run], return_exceptions=True)
    out: dict[str, tuple[dict, dict]] = {}
    for r in raw:
        if isinstance(r, BaseException):
            raise RuntimeError(f"执行阶段 A2A 调用失败: {r}") from r
        step_id, step, result = r
        out[step_id] = (step, result)
    return out


@pytest.fixture(scope="module")
def execution_results(crew_plan, a2a_available) -> dict[str, tuple[dict, dict]]:
    """执行阶段结果，每个测试模块共享一次。"""
    return asyncio.run(_run_execution(crew_plan))
