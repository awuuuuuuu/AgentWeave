"""
Crew Supervisor 图

拓扑：
    START → phase_dispatch → phase_aggregate → hitl_plan_review
          → hitl_bulk_highrisk ──(还有高危步骤)──▶ hitl_bulk_highrisk（循环）
                               ──(全部完成)──────▶ execute_all_parallel → final_report → END

SSE 事件（通过 adispatch_custom_event "em_event" 推流）：
    dept_report    — 每个部门 A2A 返回后立即推送
    dispatch_plan  — LLM 生成执行计划后推送（HITL-1 触发前）
    hitl_required  — 高危步骤批量审批前逐一推送
    plan_step      — 每步 running / done / failed 状态变更（并行，多步同时 running）
    map_update     — 步骤执行产生地理数据时推送
    final_answer   — 综合报告完成时推送

HITL resume 值约定：
    HITL-1（计划审批）：resume = list[PlanStep]（修改后计划）或 "approve"
    HITL-2（单步高危审批）：resume = "approve" | "reject"（每次只审批一个步骤，通过条件边循环）
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

import httpx
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.types import RunnableConfig, interrupt

from .crew_state import CrewState, PlanStep

logger = logging.getLogger(__name__)

# ── A2A Server 地址表 ─────────────────────────────────────────────────────────
_A2A_URLS: dict[str, str] = {
    "env_agency":          "http://localhost:9001",
    "medical_ems":         "http://localhost:9002",
    "traffic_control":     "http://localhost:9003",
    "emergency_supplies":  "http://localhost:9004",
    "enterprise_safety":   "http://localhost:9005",
}

_A2A_TIMEOUT = 120


# ── 辅助：A2A HTTP 调用 ───────────────────────────────────────────────────────

async def _call_dept_a2a(
    dept_code: str,
    task: str,
    context: dict | None = None,
    timeout: int = _A2A_TIMEOUT,
) -> dict:
    """向部门 A2A Server 发送任务，返回响应 dict。网络/超时异常时返回 failed 格式，不抛出。"""
    base_url = _A2A_URLS.get(dept_code)
    if not base_url:
        return {
            "dept_code": dept_code, "status": "failed",
            "summary": f"未知部门代码: {dept_code}",
            "key_facts": [], "map_events": [], "citations": [],
        }

    payload = {
        "task_id": str(uuid.uuid4()),
        "task": task,
        "context": context or {},
        "timeout_sec": timeout - 5,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{base_url}/a2a/tasks/send", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.TimeoutException:
        logger.warning("A2A[%s]: 请求超时", dept_code)
        return {
            "dept_code": dept_code, "status": "timeout",
            "summary": f"部门 {dept_code} 响应超时，无法获取报告",
            "key_facts": [], "map_events": [], "citations": [],
        }
    except Exception as exc:
        logger.exception("A2A[%s]: 请求失败", dept_code)
        return {
            "dept_code": dept_code, "status": "failed",
            "summary": f"部门 {dept_code} 通信错误: {exc}",
            "key_facts": [], "map_events": [], "citations": [],
        }


def _mark_step(plan: list[PlanStep], step_id: str, status: str, result_summary: str = "") -> list[PlanStep]:  # noqa: F401 — kept for external callers
    return [
        {**s, "status": status, "result_summary": result_summary}
        if s["step_id"] == step_id else s
        for s in plan
    ]


# ── 节点：阶段 1 — 并发调用各部门 ────────────────────────────────────────────

async def phase_dispatch(state: CrewState, config: RunnableConfig) -> dict:
    """并发向所有选定部门 A2A Server 发送初始研判任务。每个部门返回后立即推送 dept_report 事件。"""
    incident = state["incident"]
    dept_codes = state.get("selected_dept_codes") or list(_A2A_URLS.keys())

    dept_tasks = {
        "env_agency":         f"事故：{incident}\n请评估大气扩散范围、疏散方向及 ERPG 半径。",
        "medical_ems":        f"事故：{incident}\n请评估医疗资源需求，给出可出动救护车及医院容量报告。",
        "traffic_control":    f"事故：{incident}\n请分析路口管控方案及疏散通道，给出信号灯切换建议。",
        "emergency_supplies": f"事故：{incident}\n请核查应急物资库存，给出调拨方案建议。",
        "enterprise_safety":  f"事故：{incident}\n请分析传感器数据，定位泄漏根因及现场处置建议。",
    }

    logger.info("Crew: phase_dispatch 开始，部门=%s", dept_codes)

    # 在并发调用前推送任务分配事件，供前端立即渲染 PLANNER 卡
    await adispatch_custom_event(
        "em_event",
        {
            "type": "research_dispatch",
            "data": {
                "tasks": [
                    {"dept_code": code, "task": dept_tasks.get(code, f"事故：{incident}\n请提供应急响应报告。")}
                    for code in dept_codes
                ]
            },
        },
        config=config,
    )

    dept_reports: dict[str, dict] = {}

    async def call_and_emit(code: str) -> None:
        task = dept_tasks.get(code, f"事故：{incident}\n请提供应急响应报告。")
        result = await _call_dept_a2a(code, task)
        dept_reports[code] = result
        await adispatch_custom_event(
            "em_event",
            {"type": "dept_report", "data": result},
            config=config,
        )
        logger.info("Crew: dept_report 已推送 dept=%s status=%s", code, result.get("status"))

    await asyncio.gather(*[call_and_emit(code) for code in dept_codes])

    return {"dept_reports": dept_reports, "phase": "plan_review"}


# ── 节点：阶段 2 — LLM 聚合，生成执行计划 ────────────────────────────────────

async def phase_aggregate(state: CrewState, config: RunnableConfig) -> dict:
    """将各部门报告交给 LLM 聚合分析，生成结构化执行计划（3-6 步），并推送 dispatch_plan 事件。"""
    from config import settings
    llm = ChatOpenAI(model=settings.llm_model, temperature=0)

    reports_text = "\n\n".join(
        f"【{code}】\n状态: {r.get('status')}\n摘要: {r.get('summary', '')}\n"
        f"关键信息: {'; '.join(r.get('key_facts', []))}"
        for code, r in state["dept_reports"].items()
    )

    system = SystemMessage(content=(
        "你是城市应急指挥中心 AI 协调员。根据各部门的评估报告，"
        "制定一份结构化的应急执行计划（4-6 个步骤，顺序执行）。\n\n"
        "每个步骤必须包含：\n"
        "- step_id: 唯一 ID（如 step-001）\n"
        "- title: 步骤名称，必须具体可操作（≤30字），包含关键数量/地点，"
        "例如：「调派3辆救护车前往港城大道388号」「封闭XX路至XX路口疏散周边居民」"
        "「启动液氨泄漏点50m隔离警戒区」；禁止用「部署救援」「管控交通」等模糊表述\n"
        "- dept_code: 执行部门代码（env_agency/medical_ems/traffic_control/emergency_supplies/enterprise_safety）\n"
        "- task: 给该部门的完整执行指令（2-4句，含具体数量、地点、操作目标）\n"
        "- is_high_risk: 是否高危（true/false），涉及大范围人员疏散/停工/写操作的标 true\n"
        "- map_layer: 涉及地图操作的图层 ID（如 plume_circles/ambulance_route/signal_update/evacuation_route），否则 null\n\n"
        "以 JSON 数组格式返回，不要包含其他内容。"
    ))
    human = HumanMessage(content=(
        f"事故描述：{state['incident']}\n\n"
        f"各部门评估报告：\n{reports_text}\n\n"
        "请生成执行计划 JSON："
    ))

    try:
        resp = await llm.ainvoke([system, human])
        content = str(resp.content).strip()
        # 优先从 markdown code block 提取 JSON 数组（支持多 block、代码块前有说明文字）
        m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", content)
        if m:
            content = m.group(1)
        elif content.startswith("["):
            pass  # 裸 JSON 数组，直接 parse
        else:
            # 兜底：截取第一个 "[" 到最后一个 "]"
            start, end = content.find("["), content.rfind("]")
            if start != -1 and end > start:
                content = content[start : end + 1]
        raw_steps: list[dict] = json.loads(content)
    except Exception:
        logger.exception("Crew: 执行计划生成失败，使用默认计划")
        raw_steps = _default_plan(state)

    plan: list[PlanStep] = []
    for i, s in enumerate(raw_steps):
        plan.append(PlanStep(
            step_id=s.get("step_id", f"step-{i+1:03d}"),
            title=s.get("title", f"步骤 {i+1}"),
            dept_code=s.get("dept_code", ""),
            task=s.get("task", ""),
            is_high_risk=bool(s.get("is_high_risk", False)),
            status="pending",
            map_layer=s.get("map_layer") or None,
            result_summary="",
        ))

    await adispatch_custom_event(
        "em_event",
        {"type": "dispatch_plan", "data": {"steps": plan}},
        config=config,
    )

    logger.info("Crew: 执行计划已生成，共 %d 步", len(plan))
    return {"dispatch_plan": plan}


def _default_plan(state: CrewState) -> list[dict]:
    """LLM 失败时的兜底执行计划"""
    return [
        {
            "step_id": f"step-{i+1:03d}",
            "title": f"{code} 应急响应",
            "dept_code": code,
            "task": state["incident"],
            "is_high_risk": False,
            "map_layer": None,
        }
        for i, code in enumerate(state.get("dept_reports", {}).keys())
    ]


# ── 节点：HITL-1 — 执行计划审批 ─────────────────────────────────────────────

async def hitl_plan_review(state: CrewState) -> dict:
    """暂停等待指挥长审批执行计划。resume 值：list（修改后的计划）或 "approve"。"""
    logger.info("Crew: HITL-1 挂起，等待计划审批")

    resume_val: Any = interrupt({"type": "plan_review", "plan": state["dispatch_plan"]})

    if isinstance(resume_val, list):
        approved_plan: list[PlanStep] = [
            PlanStep(
                step_id=s.get("step_id", f"step-{i+1:03d}"),
                title=s.get("title", ""),
                dept_code=s.get("dept_code", ""),
                task=s.get("task", ""),
                is_high_risk=bool(s.get("is_high_risk", False)),
                status="pending",
                map_layer=s.get("map_layer"),
                result_summary="",
            )
            for i, s in enumerate(resume_val)
        ]
        logger.info("Crew: HITL-1 批准（修改后计划，%d 步）", len(approved_plan))
        return {"dispatch_plan": approved_plan, "current_step": 0, "phase": "executing"}

    logger.info("Crew: HITL-1 批准（原计划）")
    return {"current_step": 0, "phase": "executing"}


# ── 节点：HITL-2 单步 — 每次调用只审批一个高危步骤 ────────────────────────────
#
# 设计原则：每次节点调用只包含一个 interrupt()，与普通会话 hitl_node 保持一致。
# 通过 _route_after_highrisk 条件边循环回自身，直到所有高危步骤审批完毕。
# 避免在同一节点内多次调用 interrupt()——LangGraph 的 replay 机制对此不可靠。

async def hitl_bulk_highrisk(state: CrewState, config: RunnableConfig) -> dict:
    """
    审批当前第一个待审批高危步骤（单次 interrupt）。
    审批结果写入 dispatch_plan[step_id].status：
        "approved" → 批准，进入执行阶段
        "skipped"  → 驳回，跳过执行

    条件边 _route_after_highrisk 决定是否继续循环。
    """
    pending = [s for s in state["dispatch_plan"] if s["is_high_risk"] and s["status"] == "pending"]

    if not pending:
        logger.info("Crew: 无待审批高危步骤，跳过")
        return {}

    step = pending[0]  # 每次只处理第一个
    logger.info("Crew: HITL-2 审批步骤 %s（%s）", step["step_id"], step["title"])

    await adispatch_custom_event(
        "em_event",
        {
            "type": "hitl_required",
            "data": {
                "step_id": step["step_id"],
                "title": step["title"],
                "dept_code": step["dept_code"],
                "timeout_sec": 300,
            },
        },
        config=config,
    )

    decision = interrupt({
        "type": "step_review",
        "step_id": step["step_id"],
        "title": step["title"],
        "dept_code": step["dept_code"],
    })

    decision_str = str(decision).strip().lower() if decision is not None else ""
    if decision_str not in ("approve", "reject", "skip"):
        logger.warning("Crew: 步骤 %s 收到非法 decision=%r，强制驳回", step["step_id"], decision)
        decision_str = "reject"
    new_status = "skipped" if decision_str in ("reject", "skip") else "approved"
    logger.info("Crew: 步骤 %s %s", step["step_id"], "驳回" if new_status == "skipped" else "批准")

    updated_plan = [
        {**s, "status": new_status} if s["step_id"] == step["step_id"] else s
        for s in state["dispatch_plan"]
    ]
    return {"dispatch_plan": updated_plan}


def _route_after_highrisk(state: CrewState) -> str:
    """还有待审批高危步骤 → 循环；全部完成 → 进入执行阶段。"""
    pending = [s for s in state["dispatch_plan"] if s["is_high_risk"] and s["status"] == "pending"]
    return "hitl_bulk_highrisk" if pending else "execute_all_parallel"


# ── 节点：并行执行所有步骤 ────────────────────────────────────────────────────

async def execute_all_parallel(state: CrewState, config: RunnableConfig) -> dict:
    """
    同时启动所有 pending/approved 步骤（asyncio.gather），每步独立推送
    plan_step(running) → [map_update...] → plan_step(done/failed) 事件。
    """
    steps_to_run = [s for s in state["dispatch_plan"] if s["status"] in ("pending", "approved")]

    if not steps_to_run:
        logger.info("Crew: 无可执行步骤，直接进入报告阶段")
        return {}

    logger.info("Crew: 并行执行 %d 个步骤", len(steps_to_run))

    async def _run_one(step: PlanStep) -> tuple[str, str, dict]:
        """返回 (step_id, exec_status, result_dict)；异常时补发 plan_step(failed) 防止前端卡死。"""
        await adispatch_custom_event(
            "em_event",
            {"type": "plan_step", "data": {"step_id": step["step_id"], "status": "running"}},
            config=config,
        )
        try:
            result = await _call_dept_a2a(step["dept_code"], step["task"])
            exec_status = "done" if result.get("status") == "completed" else "failed"

            for me in result.get("map_events", []):
                await adispatch_custom_event("em_event", {"type": "map_update", "data": me}, config=config)

            await adispatch_custom_event(
                "em_event",
                {
                    "type": "plan_step",
                    "data": {
                        "step_id": step["step_id"],
                        "status": exec_status,
                        "summary": result.get("summary", "")[:200],
                    },
                },
                config=config,
            )
            logger.info("Crew: 步骤 %s 执行 %s", step["step_id"], exec_status)
            return step["step_id"], exec_status, result
        except Exception as exc:
            logger.error("Crew: 步骤 %s 执行异常: %s", step["step_id"], exc)
            # 补发 failed 事件，确保前端 Kanban 卡不会永久停在"执行中"
            await adispatch_custom_event(
                "em_event",
                {
                    "type": "plan_step",
                    "data": {
                        "step_id": step["step_id"],
                        "status": "failed",
                        "summary": f"执行异常: {exc}"[:200],
                    },
                },
                config=config,
            )
            return step["step_id"], "failed", {"summary": str(exc)[:300], "map_events": [], "status": "failed"}

    raw_results = await asyncio.gather(*[_run_one(s) for s in steps_to_run], return_exceptions=True)

    # 汇总结果，更新 dispatch_plan
    step_outcomes: dict[str, tuple[str, str]] = {}  # step_id → (status, result_summary)
    all_map_events: list[dict] = list(state.get("map_events") or [])

    for r in raw_results:
        if isinstance(r, BaseException):
            logger.error("Crew: 并行步骤执行异常: %s", r)
            continue
        step_id, exec_status, result = r
        step_outcomes[step_id] = (exec_status, result.get("summary", "")[:300])
        all_map_events.extend(result.get("map_events", []))

    updated_plan = [
        {
            **s,
            "status": step_outcomes[s["step_id"]][0] if s["step_id"] in step_outcomes else s["status"],
            "result_summary": step_outcomes[s["step_id"]][1] if s["step_id"] in step_outcomes else s.get("result_summary", ""),
        }
        for s in state["dispatch_plan"]
    ]

    return {
        "dispatch_plan": updated_plan,
        "map_events": all_map_events,
        "phase": "executing",
    }


# ── 节点：最终报告 ────────────────────────────────────────────────────────────

async def final_report(state: CrewState, config: RunnableConfig) -> dict:
    """生成综合执行报告，推送 final_answer 事件。"""
    from config import settings
    llm = ChatOpenAI(model=settings.llm_model, temperature=0)

    steps_text = "\n".join(
        f"  · {s['title']}（{s['status']}）: {s.get('result_summary', '')[:100]}"
        for s in state["dispatch_plan"]
    )
    prompt = (
        f"事故：{state['incident']}\n\n执行结果摘要：\n{steps_text}\n\n"
        "请生成一份简洁的应急处置综合报告（200字以内），包括：处置成果、未完成项、下一步建议。"
    )

    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        content = str(resp.content)
    except Exception:
        done = [s["title"] for s in state["dispatch_plan"] if s["status"] == "done"]
        content = f"应急处置完成。已执行步骤：{', '.join(done) or '无'}。"

    await adispatch_custom_event(
        "em_event",
        {"type": "final_answer", "data": {"content": content}},
        config=config,
    )

    logger.info("Crew: 综合报告已生成，phase=done")
    return {
        "phase": "done",
        "messages": [AIMessage(content=content, name="crew_supervisor")],
    }


# ── 图工厂 ────────────────────────────────────────────────────────────────────

def build_crew_graph(checkpointer: Any) -> Any:
    """
    构建并返回编译后的 Crew Supervisor 图。

    新拓扑（线性，无条件边）：
        START → phase_dispatch → phase_aggregate → hitl_plan_review
              → hitl_bulk_highrisk → execute_all_parallel → final_report → END

    参数
    ----
    checkpointer   LangGraph checkpointer（与 agent_graph 共享同一 AsyncPostgresSaver）
    """
    graph = StateGraph(CrewState)

    graph.add_node("phase_dispatch",       phase_dispatch)
    graph.add_node("phase_aggregate",      phase_aggregate)
    graph.add_node("hitl_plan_review",     hitl_plan_review)
    graph.add_node("hitl_bulk_highrisk",   hitl_bulk_highrisk)
    graph.add_node("execute_all_parallel", execute_all_parallel)
    graph.add_node("final_report",         final_report)

    graph.add_edge(START,                  "phase_dispatch")
    graph.add_edge("phase_dispatch",       "phase_aggregate")
    graph.add_edge("phase_aggregate",      "hitl_plan_review")
    graph.add_edge("hitl_plan_review",     "hitl_bulk_highrisk")
    # 条件边：还有待审批高危步骤则循环回自身，否则进入执行阶段
    graph.add_conditional_edges(
        "hitl_bulk_highrisk",
        _route_after_highrisk,
        ["hitl_bulk_highrisk", "execute_all_parallel"],
    )
    graph.add_edge("execute_all_parallel", "final_report")
    graph.add_edge("final_report",         END)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("CrewGraph: 编译完成（并行执行模式）")
    return compiled
