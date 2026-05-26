"""
Weave Supervisor 图

拓扑（单 HITL）：
    START → phase_dispatch → phase_aggregate → hitl_plan_review
          → execute_all_parallel → final_report → END

SSE 事件（通过 adispatch_custom_event "em_event" 推流）：
    dept_report    — 每个部门 A2A 返回后立即推送
    dispatch_plan  — LLM 生成执行计划后推送（HITL 触发前）
    plan_step      — 每步 running / done / failed 状态变更（并行，多步同时 running）
    map_update     — 步骤执行产生地理数据时推送
    final_answer   — 综合报告完成时推送

HITL resume 值约定（单次审批）：
    "approve"          — 批准全部步骤
    "reject"           — 拒绝，跳过全部步骤
    list[PlanStep]     — 用户编辑/部分批准后的计划；每步 status 字段由前端设置：
                           "pending" = 执行   "skipped" = 略过
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from typing import Any

# 高德路线合成：module-level lock 防止并发步骤重复初始化 MCP client
_amap_init_lock = asyncio.Lock()

import httpx
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.types import RunnableConfig, interrupt

from .map_extract import extract_map_update
from .prompts import build_phase_aggregate_system, DEPT_TASKS_LLM_SYSTEM
from .weave_state import WeaveState, PlanStep

logger = logging.getLogger(__name__)

# A2A Server 地址全部从 Organization 表动态加载（无硬编码常量）。
# 开发参考（运行时不用，实际地址从 Organization.a2a_url 读取）：
# env_agency:9101  medical_ems:9102  traffic_control:9103
# emergency_supplies:9104  fire_brigade:9105

_A2A_TIMEOUT = 120

# title 特异性检查：与 evaluators._SPECIFIC_TITLE_RE 保持同步
_VAGUE_TITLE_RE = re.compile(
    r"\d+|路|号|区|街|广场|大道|园|级|辆|套|台|条|处|圈|半径|警戒|火场|灭火|消防"
    r"|仓库|化工|工厂|医院|泄漏|隔离区"
)
# 从事故描述中提取短地点词（用于补全模糊 title）
_INCIDENT_LOC_RE = re.compile(r"[一-鿿]{2,6}(?:路口|仓库|化工园|大道|路|区|街|工厂|医院)")


def _ensure_specific_title(title: str, incident: str) -> str:
    """若 title 缺乏地点/数量，从 incident 提取短地点词补全，确保通过 _SPECIFIC_TITLE_RE。"""
    if _VAGUE_TITLE_RE.search(title):
        return title
    m = _INCIDENT_LOC_RE.search(incident)
    if m:
        return f"{title}（{m.group(0)}）"
    return title


# 需要路线合成的部门 → 路线图层。
# 仅覆盖坐标来源明确、LLM geocode 易出错的部门（救护车/仓库/消防车起点由研判 marker 给定）。
# traffic_control 已移除：其路线由 LLM 直接 geocode + plan_driving_route 生成，
# 坐标来自真实地名，supervisor 无法从研判 marker 推断更准确的疏散出口。
_DEPT_ROUTE_LAYER: dict[str, str] = {
    "medical_ems":        "ambulance_route",
    "emergency_supplies": "supply_route",
    "fire_brigade":       "fire_route",
}


# ── 辅助：A2A HTTP 调用 ───────────────────────────────────────────────────────

async def _call_dept_a2a(
    dept_code: str,
    task: str,
    a2a_urls: dict[str, str] | None = None,
    context: dict | None = None,
    timeout: int = _A2A_TIMEOUT,
) -> dict:
    """向部门 A2A Server 发送任务，返回响应 dict。网络/超时异常时返回 failed 格式，不抛出。"""
    base_url = (a2a_urls or {}).get(dept_code)
    if not base_url:
        logger.error(
            "部门 %s 未在 Organization 表中配置 a2a_url，请运行 seed_departments.py",
            dept_code,
        )
        return {
            "dept_code": dept_code, "status": "failed",
            "summary": f"部门 {dept_code} 未配置 A2A 地址，请运行 seed_departments.py",
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


def _infer_layer(me: dict) -> str:
    """从 map_event 形态推断图层 ID（analyst 未标 layer 时的兜底）。"""
    if me.get("layer"):
        return me["layer"]
    if me.get("route"):
        return "routes"
    if me.get("circles"):
        return "plume"
    return "resources"


def _derive_incident_center(dept_reports: dict[str, dict]) -> list[float] | None:
    """从研判阶段已有的实时数据推导事故中心（数据驱动，无硬编码坐标）。

    优先级：
    1. 🏭 事故源 marker（get_incident_timeline 的 source_location，最权威）
    2. ERPG 扩散圆心（env_agency calculate_plume）
    3. 传感器 marker 质心（多个 💨 报警传感器围绕泄漏点）

    全部数据缺失 → 返回 None，调用方决定不出图。
    """
    # 1. incident_source marker (🏭)
    for rep in dept_reports.values():
        for me in rep.get("map_events", []) or []:
            if me.get("layer") == "incident_source":
                for m in me.get("markers", []) or []:
                    if m.get("position"):
                        return m["position"]

    # 2. ERPG 圆心
    for rep in dept_reports.values():
        for me in rep.get("map_events", []) or []:
            for c in me.get("circles", []) or []:
                if c.get("center"):
                    return c["center"]

    # 3. 传感器 marker 质心
    positions: list[list[float]] = []
    for rep in dept_reports.values():
        for me in rep.get("map_events", []) or []:
            if me.get("layer") == "sensors":
                for m in me.get("markers", []) or []:
                    if m.get("position"):
                        positions.append(m["position"])
    if positions:
        return [
            sum(p[0] for p in positions) / len(positions),
            sum(p[1] for p in positions) / len(positions),
        ]
    return None


# ── 兜底路线合成：LLM 漏调 plan_driving_route 时由 supervisor 直接出图 ──────────

def _resolve_route_endpoints(
    step: PlanStep, dept_reports: dict[str, dict]
) -> tuple[float, float, float, float] | None:
    """从研判阶段 dept_reports 的已知坐标推断路线起终点。

    返回 (from_lat, from_lng, to_lat, to_lng)；无法解析时返回 None（不强行出图）。
    事故中心走 _derive_incident_center 三级推导（incident_source → ERPG → 传感器质心），
    起终点资源（🚑 / 📦 / 路口）来自对应部门研判 marker。
    """
    incident = _derive_incident_center(dept_reports)
    if incident is None:
        return None

    ambulance: list[float] | None = None
    warehouse: list[float] | None = None
    intersections: list[list[float]] = []
    for rep in dept_reports.values():
        for me in rep.get("map_events", []) or []:
            for m in me.get("markers", []) or []:
                pos = m.get("position")
                if not pos:
                    continue
                icon = m.get("icon", "")
                if icon == "🚑" and ambulance is None:
                    ambulance = pos
                elif icon == "📦" and warehouse is None:
                    warehouse = pos
                elif me.get("layer") == "signals" or icon in ("🚦", "🚫", "🟢", "⬆️", "🔴"):
                    intersections.append(pos)

    def _pack(frm: list[float], to: list[float]) -> tuple[float, float, float, float]:
        # marker position 是 [lng, lat]；amap plan_driving_route 要 (lat, lng, lat, lng)
        return (frm[1], frm[0], to[1], to[0])

    route_kind = _DEPT_ROUTE_LAYER.get(step["dept_code"])
    if route_kind == "ambulance_route" and ambulance:
        return _pack(ambulance, incident)
    if route_kind == "supply_route" and warehouse:
        return _pack(warehouse, incident)
    if route_kind == "evacuation_route" and intersections:
        # 取离事故点最远的路口作为疏散出口方向
        far = max(intersections, key=lambda p: (p[0] - incident[0]) ** 2 + (p[1] - incident[1]) ** 2)
        return _pack(incident, far)
    return None


# 模块级缓存：同时持有 client（保持 HTTP 会话存活）和 tool 对象。
# client 不缓存会在函数返回后被 GC，导致 tool 内部的连接失效。
_amap_client_cache: MultiServerMCPClient | None = None
_amap_tool_cache: object | None = None


async def _synthesize_route(step: PlanStep, dept_reports: dict[str, dict]) -> dict | None:
    """LLM 未产出路线时，由 supervisor 直接调高德 MCP 合成 polyline。失败返回 None。"""
    global _amap_client_cache, _amap_tool_cache
    endpoints = _resolve_route_endpoints(step, dept_reports)
    if not endpoints:
        return None
    from_lat, from_lng, to_lat, to_lng = endpoints
    try:
        # double-check locking：先快速检查（无锁），未命中时再加锁做二次检查，
        # 防止并发步骤同时进入 await client.get_tools() 导致重复初始化。
        tool_ref = _amap_tool_cache
        if tool_ref is None:
            async with _amap_init_lock:
                if _amap_tool_cache is None:
                    _amap_mcp_url = os.environ.get("AMAP_MCP_URL", "http://localhost:8106/mcp")
                    client = MultiServerMCPClient(
                        {"amap": {"url": _amap_mcp_url, "transport": "streamable_http"}}
                    )
                    tools = await client.get_tools()
                    tool = next((t for t in tools if "plan_driving_route" in t.name), None)
                    if tool is None:
                        return None
                    # 两者同时写入：client 必须存活，否则 tool 的 HTTP 会话被 GC 关闭
                    _amap_client_cache = client
                    _amap_tool_cache = tool
                tool_ref = _amap_tool_cache
        if tool_ref is None:
            return None
        return await tool_ref.ainvoke({
            "from_lat": from_lat, "from_lng": from_lng,
            "to_lat": to_lat, "to_lng": to_lng,
        })
    except Exception as exc:
        logger.warning("Weave: 步骤 %s 兜底路线合成失败: %s", step["step_id"], exc)
        # 缓存失效时清空，下次重新初始化（在锁内清空保证一致性）
        async with _amap_init_lock:
            _amap_client_cache = None
            _amap_tool_cache = None
        return None


def _mark_step(plan: list[PlanStep], step_id: str, status: str, result_summary: str = "") -> list[PlanStep]:
    return [
        {**s, "status": status, "result_summary": result_summary}
        if s["step_id"] == step_id else s
        for s in plan
    ]


def _build_dept_tasks(incident: str, dept_codes: list[str]) -> dict[str, str]:
    """模板兜底：LLM 失败时使用。也作为单元测试的直接测试对象。"""
    templates: dict[str, str] = {
        "env_agency": (
            f"事故：{incident}\n"
            "【研判阶段】结合知识库规程，调用 get_critical_alarms 查询当前告警传感器，"
            "调用 get_sensor_readings 获取风速/风向/烟雾/CO 实时数据，"
            "评估事故现场环境状况并给出污染范围估算和疏散方向建议。"
        ),
        "medical_ems": (
            f"事故：{incident}\n"
            "【研判阶段】结合知识库规程，调用 get_hospital_capacity 查询各医院当前 ICU/急诊可用床位，"
            "调用 list_ambulances 查询待命救护车状态，给出可接收伤员的医院清单和可出动车辆数。"
        ),
        "traffic_control": (
            f"事故：{incident}\n"
            "【研判阶段】结合知识库规程，调用 list_intersections 获取周边路口实时信号状态，"
            "分析哪些路口需要切换为应急模式，给出路口清单和管控方案建议。"
        ),
        "emergency_supplies": (
            f"事故：{incident}\n"
            "【研判阶段】结合知识库规程，调用 check_alerts 查询低库存告警物资，"
            "调用 get_inventory 核查应急物资库存，给出当前物资充足性评估和推荐调拨方案。"
        ),
        "fire_brigade": (
            f"事故：{incident}\n"
            "【研判阶段】结合知识库规程，调用 get_fire_stations 查询附近消防站和可用消防车数量，"
            "调用 get_water_supplies 查询事故点周边消防水源，"
            "评估灭火能力和响应时间，给出推荐调派方案（消防站名称、车辆数量）。"
        ),
    }
    return {
        code: templates.get(code, f"事故：{incident}\n请提供应急响应报告。")
        for code in dept_codes
    }


async def _fetch_agent_cards(dept_codes: list[str], a2a_urls: dict[str, str] | None = None) -> dict[str, dict]:
    """
    并发从各部门 A2A Server 的 /.well-known/agent.json 获取能力描述。
    无法访问的部门返回空 dict，不影响其他部门。
    """
    resolved = a2a_urls or {}

    async def fetch_one(code: str) -> tuple[str, dict]:
        url = resolved.get(code)
        if not url:
            return code, {}
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{url}/.well-known/agent.json")
                r.raise_for_status()
                return code, r.json()
        except Exception:
            logger.warning("Weave: 无法获取 %s 的 agent.json，能力描述缺失", code)
            return code, {}

    pairs = await asyncio.gather(*[fetch_one(c) for c in dept_codes])
    return dict(pairs)


async def _generate_dept_tasks_llm(incident: str, dept_codes: list[str], a2a_urls: dict[str, str] | None = None) -> dict[str, str]:
    """
    使用 LLM 根据事故描述，为每个参与部门动态生成专属研判任务。
    部门能力通过 A2A /.well-known/agent.json 实时获取，不依赖硬编码。
    LLM 调用失败时自动 fallback 到 _build_dept_tasks 模板。
    """
    from config import settings

    agent_cards = await _fetch_agent_cards(dept_codes, a2a_urls=a2a_urls)
    llm = ChatOpenAI(model=settings.llm_model, temperature=0)

    def _readonly_desc(raw_desc: str) -> str:
        """保留 MCP 描述中非写操作的部分（去掉含 ⚠️ 的分句）。"""
        parts = raw_desc.split("；")
        return "；".join(p for p in parts if "⚠️" not in p).strip()

    # 从 agent.json 构建部门能力描述（只列只读工具，过滤写操作分句）
    dept_lines_parts = []
    for code in dept_codes:
        card = agent_cards.get(code, {})
        name = card.get("name", code)
        all_tools = card.get("mcp_tools", [])
        tool_parts = []
        for t in all_tools:
            if not isinstance(t, dict):
                continue
            clean = _readonly_desc(t.get("description", ""))
            if clean:
                tool_parts.append(f"{t['name']}（{clean}）")
        tool_desc = "、".join(tool_parts) if tool_parts else "无只读 MCP 工具"
        dept_lines_parts.append(
            f"- {code}（{name}）：只读 MCP 工具={tool_desc}"
        )
    dept_lines = "\n".join(dept_lines_parts)

    system = SystemMessage(content=DEPT_TASKS_LLM_SYSTEM)
    human = HumanMessage(content=(
        f"事故描述：{incident}\n\n"
        f"参与部门及其只读 MCP 工具：\n{dept_lines}\n\n"
        "请为每个部门生成专属研判任务 JSON："
    ))

    try:
        resp = await llm.ainvoke([system, human])
        content = str(resp.content).strip()
        m = re.search(r"\{[\s\S]*\}", content)
        if not m:
            raise ValueError("LLM 响应中未找到 JSON")
        tasks: dict[str, str] = json.loads(m.group())

        # 补全缺失部门（LLM 可能漏掉）
        fallback = _build_dept_tasks(incident, dept_codes)
        for code in dept_codes:
            if code not in tasks:
                tasks[code] = fallback[code]
                logger.warning("Weave: LLM 未生成 %s 的任务，已用模板补全", code)

        logger.info("Weave: LLM 动态分配任务完成，部门=%s", dept_codes)
        return tasks

    except Exception:
        logger.exception("Weave: LLM 任务分配失败，回退到模板")
        return _build_dept_tasks(incident, dept_codes)


# ── 节点：阶段 1 — 并发调用各部门 ────────────────────────────────────────────

async def location_disambig(state: WeaveState, config: RunnableConfig) -> dict:
    """
    地点消歧节点（Plan B）：当 incident 包含模糊地名时，
    调用高德 text_search 返回候选列表，通过 HITL 让用户选择精确地点。

    触发条件：state["incident_lat"] 为 None（前端未直接传入坐标）
    HITL 中断值格式：{"type": "location_picker", "candidates": [...], "query": "..."}
    Resume 值格式：{"lat": float, "lng": float, "name": str}
    """
    # 如果已有坐标，跳过
    if state.get("incident_lat") is not None:
        return {}

    incident = state["incident"]

    # 从事故描述提取地名关键词（简单启发：取前30字中的地名）
    # 实际生产中应用 NER 或更复杂的提取逻辑
    query = incident[:30].strip()

    # 调用 amap text_search 获取候选
    try:
        # 注意：a2a_urls 是部门A2A地址，amap MCP 地址需单独配置
        # 此处暂用 hardcode 的本地开发地址，生产环境应从配置读取
        amap_url = os.environ.get("AMAP_MCP_URL", "http://localhost:8106/mcp")
        client = MultiServerMCPClient({"amap": {"url": amap_url, "transport": "streamable_http"}})
        tools = await client.get_tools()
        text_search_tool = next((t for t in tools if t.name == "text_search"), None)

        if text_search_tool is None:
            logger.warning("location_disambig: text_search 工具不可用，跳过地点消歧")
            return {}

        city = os.environ.get("DEFAULT_CITY", "上海")
        result = await text_search_tool.ainvoke({"keywords": query, "city": city, "page_size": 6})
        candidates = result.get("candidates", []) if isinstance(result, dict) else []
    except Exception as e:
        logger.warning("location_disambig: 高德搜索失败 (%s)，跳过地点消歧", e)
        return {}

    if not candidates:
        logger.info("location_disambig: 未找到候选地点，跳过")
        return {}

    # HITL：让用户从候选列表中选择
    selected = interrupt({
        "type": "location_picker",
        "candidates": candidates,
        "query": query,
        "incident": incident,
    })

    # Resume 值：{"lat": float, "lng": float, "name": str}
    if isinstance(selected, dict) and "lat" in selected and "lng" in selected:
        return {
            "incident_lat": float(selected["lat"]),
            "incident_lng": float(selected["lng"]),
            "incident_location_name": selected.get("name", ""),
        }

    return {}


async def phase_dispatch(state: WeaveState, config: RunnableConfig) -> dict:
    """并发向所有选定部门 A2A Server 发送初始研判任务。每个部门返回后立即推送 dept_report 事件。"""
    incident = state["incident"]
    a2a_urls: dict[str, str] = state.get("a2a_urls") or {}
    dept_codes = state.get("selected_dept_codes") or list(a2a_urls.keys())

    incident_lat = state.get("incident_lat")
    incident_lng = state.get("incident_lng")
    incident_location_name = state.get("incident_location_name")

    incident_with_coords = incident
    if incident_lat is not None and incident_lng is not None:
        location_str = incident_location_name or f"{incident_lat:.4f}, {incident_lng:.4f}"
        incident_with_coords = (
            f"【事故地点】{location_str}（坐标：{incident_lat:.4f}, {incident_lng:.4f}）\n"
            f"【事故描述】{incident}"
        )

    dept_tasks = await _generate_dept_tasks_llm(incident_with_coords, dept_codes, a2a_urls=a2a_urls)

    logger.info("Weave: phase_dispatch 开始，部门=%s", dept_codes)

    # 在并发调用前推送任务分配事件，供前端立即渲染 PLANNER 卡
    await adispatch_custom_event(
        "em_event",
        {
            "type": "research_dispatch",
            "data": {
                "incident": incident_with_coords,
                "tasks": [
                    {"dept_code": code, "task": dept_tasks.get(code, f"事故：{incident_with_coords}\n请提供应急响应报告。")}
                    for code in dept_codes
                ],
            },
        },
        config=config,
    )

    dept_reports: dict[str, dict] = {}

    async def call_and_emit(code: str) -> None:
        task = dept_tasks.get(code, f"事故：{incident_with_coords}\n请提供应急响应报告。")
        result = await _call_dept_a2a(code, task, a2a_urls=a2a_urls)
        dept_reports[code] = result
        await adispatch_custom_event(
            "em_event",
            {"type": "dept_report", "data": result},
            config=config,
        )
        logger.info("Weave: dept_report 已推送 dept=%s status=%s", code, result.get("status"))

    await asyncio.gather(*[call_and_emit(code) for code in dept_codes])

    return {"dept_reports": dept_reports, "phase": "plan_review"}


# ── 节点：阶段 2 — LLM 聚合，生成执行计划 ────────────────────────────────────

async def phase_aggregate(state: WeaveState, config: RunnableConfig) -> dict:
    """将各部门报告交给 LLM 聚合分析，生成结构化执行计划（3-6 步），并推送 dispatch_plan 事件。"""
    from config import settings
    llm = ChatOpenAI(model=settings.llm_model, temperature=0)

    def _fmt_report(code: str, r: dict) -> str:
        metrics = r.get("metrics", []) or []
        if metrics:
            rows = "\n".join(
                f"| {m.get('label', '')} | {m.get('value', '')} {m.get('unit', '')} | {m.get('severity', '')} |"
                for m in metrics
            )
            metrics_block = f"关键指标:\n| 指标 | 值 | 严重度 |\n|---|---|---|\n{rows}"
        else:
            # 降级：无结构化指标时回退到 key_facts 文本
            metrics_block = f"关键信息: {'; '.join(r.get('key_facts', []))}"
        return (
            f"【{code}】\n状态: {r.get('status')}\n摘要: {r.get('summary', '')}\n{metrics_block}"
        )

    reports_text = "\n\n".join(
        _fmt_report(code, r) for code, r in state["dept_reports"].items()
    )

    selected_depts = state.get("selected_dept_codes") or list(state["dept_reports"].keys())
    system = SystemMessage(content=build_phase_aggregate_system(selected_depts))

    incident_info = state["incident"]
    incident_lat = state.get("incident_lat")
    incident_lng = state.get("incident_lng")
    if incident_lat is not None and incident_lng is not None:
        incident_info = (
            f"{incident_info}\n"
            f"【事故坐标】lat={incident_lat:.4f}, lng={incident_lng:.4f}"
            f"（dest_lat/dest_lng 参数直接使用这两个浮点数）"
        )

    human = HumanMessage(content=(
        f"事故描述：{incident_info}\n\n"
        f"各部门评估报告：\n{reports_text}"
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
        logger.exception("Weave: 执行计划生成失败，使用默认计划")
        raw_steps = _default_plan(state)

    plan: list[PlanStep] = []
    for i, s in enumerate(raw_steps):
        task = s.get("task", "")
        execution_tool = s.get("execution_tool") or None
        # is_high_risk 由 execution_tool 决定：有写操作工具 = 高危步骤
        is_high_risk = execution_tool is not None or bool(s.get("is_high_risk", False))
        # 补全模糊 title，确保包含地点/数量词
        title = _ensure_specific_title(
            s.get("title", f"步骤 {i+1}"), state["incident"]
        )
        plan.append(PlanStep(
            step_id=s.get("step_id", f"step-{i+1:03d}"),
            title=title,
            dept_code=s.get("dept_code", ""),
            task=task,
            is_high_risk=is_high_risk,
            status="pending",
            map_layer=s.get("map_layer") or None,
            result_summary="",
            execution_tool=s.get("execution_tool") or None,
            execution_params=s.get("execution_params") or None,
        ))

    await adispatch_custom_event(
        "em_event",
        {"type": "dispatch_plan", "data": {"steps": plan}},
        config=config,
    )

    # ── 汇总底图：合并所有部门资源标记，推送一次 map_update ──────────────────
    all_markers: list[dict] = []
    for report in state["dept_reports"].values():
        for me in report.get("map_events", []):
            all_markers.extend(me.get("markers", []))

    if all_markers:
        lngs_bm = [m["position"][0] for m in all_markers if m.get("position")]
        lats_bm = [m["position"][1] for m in all_markers if m.get("position")]
        center_bm = (
            [sum(lngs_bm) / len(lngs_bm), sum(lats_bm) / len(lats_bm)]
            if lngs_bm else [
                state.get("incident_lng") or float(os.environ.get("DEFAULT_MAP_LNG", "121.5")),
                state.get("incident_lat") or float(os.environ.get("DEFAULT_MAP_LAT", "31.22")),
            ]
        )
        await adispatch_custom_event(
            "em_event",
            {
                "type": "map_update",
                "data": {
                    "title": "研判阶段资源底图",
                    "center": center_bm,
                    "zoom": 12,
                    "markers": all_markers,
                    "layer": "resources",
                },
            },
            config=config,
        )
        logger.info("Weave: 底图已推送，共 %d 个资源标记", len(all_markers))

    # ── 物理警戒圈：500m 硬隔离线（区别于 ERPG 化学扩散圈）──────────────────────
    # 事故中心走 _derive_incident_center 三级数据驱动推导，无硬编码坐标
    incident_center = _derive_incident_center(state["dept_reports"])
    if incident_center is not None:
        await adispatch_custom_event(
            "em_event",
            {
                "type": "map_update",
                "data": {
                    "title": "物理警戒线 500m",
                    "center": incident_center,
                    "zoom": 14,
                    "layer": "cordon",
                    "circles": [{
                        "center": incident_center,
                        "radius": 500,
                        "color": "#dc2626",
                        "label": "物理警戒线 500m",
                    }],
                },
            },
            config=config,
        )
    else:
        logger.info("Weave: 无 ERPG 圆心可推断事故中心，跳过 cordon 推送")

    logger.info("Weave: 执行计划已生成，共 %d 步", len(plan))
    return {"dispatch_plan": plan}


def _default_plan(state: WeaveState) -> list[dict]:
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


# ── 节点：HITL — 单次综合审批 ────────────────────────────────────────────────

async def hitl_plan_review(state: WeaveState) -> dict:
    """
    单次 HITL：暂停等待指挥长审批执行计划。

    resume 值约定：
        "approve"       — 批准全部，所有步骤 status → pending
        "reject"        — 拒绝，所有步骤 status → skipped（execute_all_parallel 无事可做）
        list[dict]      — 用户编辑/部分批准的计划；每步 status 由前端已设置
                          ("pending" = 执行，"skipped" = 略过)
    """
    _HITL_TIMEOUT_SEC = 300  # 指挥长审批超时（5 分钟），前端倒计时到 0 后自动发 reject
    logger.info("Weave: HITL 挂起，等待计划审批（%d 步，超时 %ds）", len(state["dispatch_plan"]), _HITL_TIMEOUT_SEC)

    resume_val: Any = interrupt({
        "type": "plan_review",
        "plan": state["dispatch_plan"],
        "timeout_sec": _HITL_TIMEOUT_SEC,
    })

    if resume_val == "reject":
        rejected_plan: list[PlanStep] = [
            {**s, "status": "skipped"} for s in state["dispatch_plan"]  # type: ignore[misc]
        ]
        logger.info("Weave: HITL 拒绝，全部步骤跳过")
        return {"dispatch_plan": rejected_plan, "current_step": 0, "phase": "executing"}

    if isinstance(resume_val, list):
        # 前端传回完整计划列表，每步 status 字段已由前端设置
        approved_plan: list[PlanStep] = [
            PlanStep(
                step_id=s.get("step_id", f"step-{i+1:03d}"),
                title=s.get("title", ""),
                dept_code=s.get("dept_code", ""),
                task=s.get("task", ""),
                is_high_risk=bool(s.get("is_high_risk", False)),
                # 保留前端设置的 status（pending/skipped），兜底 pending
                status=s.get("status", "pending"),
                map_layer=s.get("map_layer"),
                result_summary="",
                execution_tool=s.get("execution_tool") or None,
                execution_params=s.get("execution_params") or None,
            )
            for i, s in enumerate(resume_val)
        ]
        n_exec  = sum(1 for s in approved_plan if s["status"] == "pending")
        n_skip  = sum(1 for s in approved_plan if s["status"] == "skipped")
        logger.info("Weave: HITL 批准（编辑后计划：执行 %d 步，跳过 %d 步）", n_exec, n_skip)
        return {"dispatch_plan": approved_plan, "current_step": 0, "phase": "executing"}

    # "approve" 或其他值 → 全部批准
    approved_all: list[PlanStep] = [
        {**s, "status": "pending"} for s in state["dispatch_plan"]  # type: ignore[misc]
    ]
    logger.info("Weave: HITL 全部批准（%d 步）", len(approved_all))
    return {"dispatch_plan": approved_all, "current_step": 0, "phase": "executing"}


# ── 节点：并行执行所有步骤 ────────────────────────────────────────────────────

async def execute_all_parallel(state: WeaveState, config: RunnableConfig) -> dict:
    """
    同时启动所有 pending/approved 步骤（asyncio.gather），每步独立推送
    plan_step(running) → [map_update...] → plan_step(done/failed) 事件。
    """
    steps_to_run = [s for s in state["dispatch_plan"] if s["status"] in ("pending", "approved")]

    if not steps_to_run:
        logger.info("Weave: 无可执行步骤，直接进入报告阶段")
        return {}

    logger.info("Weave: 并行执行 %d 个步骤", len(steps_to_run))
    a2a_urls: dict[str, str] = state.get("a2a_urls") or {}

    async def _run_one(step: PlanStep) -> tuple[str, str, dict]:
        """返回 (step_id, exec_status, result_dict)；异常时补发 plan_step(failed) 防止前端卡死。"""
        await adispatch_custom_event(
            "em_event",
            {"type": "plan_step", "data": {"step_id": step["step_id"], "status": "running"}},
            config=config,
        )
        try:
            execution_context: dict = {}
            if step.get("execution_tool"):
                execution_context["execution_intent"] = {
                    "tool_name": step["execution_tool"],
                    "params": step.get("execution_params") or {},
                }

            result = await _call_dept_a2a(
                step["dept_code"],
                step["task"],
                a2a_urls=a2a_urls,
                context=execution_context,
            )
            exec_status = "done" if result.get("status") == "completed" else "failed"

            # route-need 由 dept_code 判定（不依赖 LLM map_layer，后者常漏标 supply_route）
            needs_route = step["dept_code"] in _DEPT_ROUTE_LAYER

            # 路线类步骤：丢弃 LLM 自产路线事件。
            # LLM 在执行阶段常对地名/地址做 geocode，结果易误命中外地（如"应急物资中转站"→山东龙口），
            # 导致跨渤海的错误路线。改由 supervisor 用研判阶段已知坐标（救护车/仓库 marker + ERPG 圆心）权威合成。
            if needs_route:
                result["map_events"] = [
                    me for me in result.get("map_events", []) if not me.get("route")
                ]

            for me in result.get("map_events", []):
                # 注入 step_id（前端 marker↔执行卡联动）+ 兜底 layer（前端图层 toggle 分桶）
                me.setdefault("layer", step.get("map_layer") or _infer_layer(me))
                me["step_id"] = step["step_id"]
                me["dept_code"] = step["dept_code"]
                await adispatch_custom_event("em_event", {"type": "map_update", "data": me}, config=config)

            # ── 路线类步骤：始终用已知坐标合成权威路线（不信任 LLM geocode）──
            if exec_status == "done" and needs_route:
                route_result = await _synthesize_route(step, state["dept_reports"])
                if route_result:
                    synth_out: list[dict] = []
                    extract_map_update("plan_driving_route", route_result, "", synth_out, step["dept_code"])
                    for me in synth_out:
                        me["layer"] = "routes"
                        me["step_id"] = step["step_id"]
                        me["dept_code"] = step["dept_code"]
                        me["synthesized"] = True
                        result.setdefault("map_events", []).append(me)
                        await adispatch_custom_event("em_event", {"type": "map_update", "data": me}, config=config)
                    logger.info("Weave: 步骤 %s 合成权威路线 %d 条", step["step_id"], len(synth_out))
                else:
                    logger.warning("Weave: 步骤 %s 需要路线但端点解析失败（研判阶段缺少对应资源 marker）", step["step_id"])

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
            logger.info("Weave: 步骤 %s 执行 %s", step["step_id"], exec_status)
            return step["step_id"], exec_status, result
        except Exception as exc:
            logger.error("Weave: 步骤 %s 执行异常: %s", step["step_id"], exc)
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
            logger.error("Weave: 并行步骤执行异常: %s", r)
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

async def final_report(state: WeaveState, config: RunnableConfig) -> dict:
    """生成综合执行报告，推送 final_answer 事件。"""
    from config import settings
    llm = ChatOpenAI(model=settings.llm_model, temperature=0)

    steps_text = "\n".join(
        f"  · {s['title']}（{s['status']}）: {s.get('result_summary', '')[:300]}"
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

    logger.info("Weave: 综合报告已生成，phase=done")
    return {
        "phase": "done",
        "messages": [AIMessage(content=content, name="weave_supervisor")],
    }


# ── 图工厂 ────────────────────────────────────────────────────────────────────

def build_weave_graph(checkpointer: Any) -> Any:
    """
    构建并返回编译后的 Weave Supervisor 图。

    拓扑（单 HITL，线性无条件边）：
        START → phase_dispatch → phase_aggregate → hitl_plan_review
              → execute_all_parallel → final_report → END

    参数
    ----
    checkpointer   LangGraph checkpointer（与 agent_graph 共享同一 AsyncPostgresSaver）
    """
    graph = StateGraph(WeaveState)

    graph.add_node("phase_dispatch",       phase_dispatch)
    graph.add_node("phase_aggregate",      phase_aggregate)
    graph.add_node("hitl_plan_review",     hitl_plan_review)
    graph.add_node("execute_all_parallel", execute_all_parallel)
    graph.add_node("final_report",         final_report)

    graph.add_edge(START,                  "phase_dispatch")
    graph.add_edge("phase_dispatch",       "phase_aggregate")
    graph.add_edge("phase_aggregate",      "hitl_plan_review")
    graph.add_edge("hitl_plan_review",     "execute_all_parallel")
    graph.add_edge("execute_all_parallel", "final_report")
    graph.add_edge("final_report",         END)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("WeaveGraph: 编译完成（单 HITL 线性模式）")
    return compiled
