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
from .prompts import (
    build_phase_aggregate_system,
    build_single_step_plan_system,
    DEPT_TASKS_LLM_SYSTEM,
    DEPT_WRITE_TOOL_MAP,
)
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
    step: PlanStep,
    dept_reports: dict[str, dict],
    incident_override: list[float] | None = None,
) -> tuple[float, float, float, float] | None:
    """从研判阶段 dept_reports 的已知坐标推断路线起终点。

    返回 (from_lat, from_lng, to_lat, to_lng)；无法解析时返回 None（不强行出图）。
    事故中心走 _derive_incident_center 三级推导（incident_source → ERPG → 传感器质心），
    火灾等无 env_agency 研判的场景用 incident_override（WeaveState.incident_lat/lng）兜底。
    起终点资源（🚑 / 📦 / 🚒 / 路口）来自对应部门研判 marker。
    """
    # 优先使用 WeaveState 中用户确认的坐标（location_disambig 精确锁定），
    # 次选 _derive_incident_center（ERPG/传感器质心），火灾场景质心可能偏离真实位置。
    incident = incident_override or _derive_incident_center(dept_reports)
    if incident is None:
        return None

    ambulance: list[float] | None = None
    warehouse: list[float] | None = None
    fire_station_pos: list[float] | None = None
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
                elif icon == "🚒" and fire_station_pos is None:
                    fire_station_pos = pos
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
    if route_kind == "fire_route" and fire_station_pos:
        return _pack(fire_station_pos, incident)
    if route_kind == "evacuation_route" and intersections:
        # 取离事故点最远的路口作为疏散出口方向
        far = max(intersections, key=lambda p: (p[0] - incident[0]) ** 2 + (p[1] - incident[1]) ** 2)
        return _pack(incident, far)
    return None


# 模块级缓存：同时持有 client（保持 HTTP 会话存活）和 tool 对象。
# client 不缓存会在函数返回后被 GC，导致 tool 内部的连接失效。
_amap_client_cache: MultiServerMCPClient | None = None
_amap_tool_cache: object | None = None


async def _synthesize_route(
    step: PlanStep,
    dept_reports: dict[str, dict],
    incident_override: list[float] | None = None,
) -> dict | None:
    """LLM 未产出路线时，由 supervisor 直接调高德 MCP 合成 polyline。失败返回 None。"""
    global _amap_client_cache, _amap_tool_cache
    endpoints = _resolve_route_endpoints(step, dept_reports, incident_override=incident_override)
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
    templates: dict[str, str] = {
        "env_agency": (
            f"事故：{incident}\n"
            "【研判阶段】结合知识库规程，从事故描述中提取坐标后，"
            "调用 get_critical_alarms(lat=<纬度>, lng=<经度>) 查询事故点附近告警传感器，"
            "调用 get_sensor_readings 获取风速/风向/烟雾/CO 实时数据，"
            "评估事故现场环境状况并给出污染范围估算和疏散方向建议。"
        ),
        "medical_ems": (
            f"事故：{incident}\n"
            "【研判阶段】结合知识库规程，从事故描述中提取坐标后，"
            "调用 get_hospital_capacity(lat=<纬度>, lng=<经度>) 查询附近医院当前 ICU/急诊可用床位，"
            "调用 list_ambulances(lat=<纬度>, lng=<经度>, status=\"待命\") 查询最近待命救护车，"
            "给出可接收伤员的医院清单和可出动车辆数。"
        ),
        "traffic_control": (
            f"事故：{incident}\n"
            "【研判阶段】结合知识库规程，从事故描述中提取坐标后，"
            "调用 get_nearby_intersections(lat=<纬度>, lng=<经度>, radius_km=3.0) 获取事故点周边路口实时信号状态，"
            "分析哪些路口需要切换为应急模式，给出路口清单和管控方案建议。"
        ),
        "emergency_supplies": (
            f"事故：{incident}\n"
            "【研判阶段】结合知识库规程，调用 check_alerts 查询低库存告警物资，"
            "调用 get_inventory 核查应急物资库存，给出当前物资充足性评估和推荐调拨方案。"
        ),
        "fire_brigade": (
            f"事故：{incident}\n"
            "【研判阶段】结合知识库规程，从事故描述中提取坐标后，"
            "调用 get_fire_stations(lat=<纬度>, lng=<经度>) 查询附近消防站和可用消防车数量，"
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


# ── 节点：意图分类 ───────────────────────────────────────────────────────────

async def classify_intent(state: WeaveState, config: RunnableConfig) -> dict:
    """
    Turn 0：直接标记为 incident_response（首次上报事故，无需 LLM 判断）。
    Turn ≥ 1：用 LLM 判断用户输入是 direct_command 还是 follow_up/incident_response。
    """
    from config import settings

    turn = state.get("conversation_turn") or 0
    incident = state.get("incident", "")

    if turn == 0:
        _CITYWIDE_KEYWORDS = (
            "台风", "暴雨", "预警", "全市", "全区", "全域",
            "寒潮", "大风蓝色", "大风橙色", "黄色预警", "橙色预警",
        )
        scope = "citywide" if any(kw in incident for kw in _CITYWIDE_KEYWORDS) else "localized"
        logger.info("Weave: classify_intent turn=0 → incident_response, scope=%s", scope)
        return {"intent": "incident_response", "conversation_turn": 1, "event_scope": scope}

    llm = ChatOpenAI(model=settings.llm_model, temperature=0)
    system = SystemMessage(content=(
        "判断用户应急指令类型，只输出一个词：\n"
        "direct_command：含明确行动动词（派/调/增援/封锁/通知/撤离/回撤/取消/关闭 等）\n"
        "escalation：事故情况升级（「发现危化品」「火势扩大至相邻楼」「发现更多伤亡」等），需重新全部门研判\n"
        "incident_response：报告新事故或重新描述事故情况\n"
        "follow_up：状态查询、追问或补充说明\n"
        "只输出 direct_command 或 escalation 或 incident_response 或 follow_up，不含其他内容。"
    ))
    human = HumanMessage(content=f"用户指令：{incident}")

    try:
        resp = await llm.ainvoke([system, human])
        intent = str(resp.content).strip().lower()
        if intent not in ("direct_command", "escalation", "incident_response", "follow_up"):
            intent = "direct_command"
    except Exception:
        logger.warning("Weave: classify_intent LLM 失败，默认 direct_command")
        intent = "direct_command"

    logger.info("Weave: classify_intent turn=%d → %s", turn, intent)
    return {"intent": intent, "conversation_turn": turn + 1, "event_scope": "localized"}


# ── 节点：阶段 1 — 并发调用各部门 ────────────────────────────────────────────

async def location_disambig(state: WeaveState, config: RunnableConfig) -> dict:
    """
    用 LLM 从事故描述提取地点关键词 → 调 AMap text_search → 推送 location_candidates SSE
    → HITL 等待用户选择 → 将选定地点注入 incident_location（新）+ incident_lat/lng（Plan C 兼容）。

    跳过条件：incident_location 已存在（多轮时地点已确认，无需重复消歧）。

    resume 值约定：
        dict{"name","address","lat","lng"}  — 用户选定的地点
        "search:关键词"                      — 用户输入新关键词重新搜索（循环）
        其他任何值                            — 跳过地点消歧，继续无精确坐标
    """
    from config import settings

    if state.get("incident_location") is not None:
        return {}

    incident = state["incident"]
    llm = ChatOpenAI(model=settings.llm_model, temperature=0)

    async def _search_amap(query: str, exact: bool = False) -> tuple[list[dict], str]:
        """用 LLM 提取关键词并调 AMap text_search，返回 (candidates, keywords)。
        exact=True 时跳过 LLM 提取，直接用 query 作为搜索关键词（用于用户手动重新搜索）。
        """
        if exact:
            keywords = query.strip()[:40]
        else:
            try:
                default_city = os.environ.get("DEFAULT_CITY", "上海")
                kw_resp = await llm.ainvoke([
                    SystemMessage(content=(
                        f"从应急事件描述中提取最精确的地点关键词，用于高德 POI 搜索（城市：{default_city}）。"
                        "只输出关键词，不超过 20 字，不含任何标点或额外说明。"
                        "示例：「朝阳公园附近发生火灾」→「朝阳公园」"
                    )),
                    HumanMessage(content=query),
                ])
                keywords = str(kw_resp.content).strip()[:40]
            except Exception:
                keywords = query[:20]

        logger.info("Weave: location_disambig 关键词=%r", keywords)

        amap_url = os.environ.get("AMAP_MCP_URL", "http://localhost:8106/mcp")
        found: list[dict] = []
        try:
            client = MultiServerMCPClient(
                {"amap": {"url": amap_url, "transport": "streamable_http"}}
            )
            tools = await client.get_tools()
            search_tool = next((t for t in tools if "text_search" in t.name), None)
            if search_tool:
                city = os.environ.get("DEFAULT_CITY", "")
                result = await search_tool.ainvoke({"keywords": keywords, "city": city})
                logger.info("Weave: text_search raw result type=%s value=%r", type(result).__name__, str(result)[:200])
                # MCP tool returns list of content blocks: [{"type":"text","text":"..."}]
                if isinstance(result, list):
                    for block in result:
                        if isinstance(block, dict) and block.get("type") == "text":
                            try:
                                parsed = json.loads(block["text"])
                                found = (parsed.get("candidates") or [])[:6]
                            except Exception:
                                pass
                            break
                elif isinstance(result, dict):
                    found = (result.get("candidates") or [])[:6]
                elif isinstance(result, str):
                    try:
                        parsed = json.loads(result)
                        found = (parsed.get("candidates") or [])[:6]
                    except Exception:
                        pass
            else:
                logger.warning("Weave: 未找到 text_search 工具，可用: %s", [t.name for t in tools])
        except Exception as exc:
            logger.warning("Weave: AMap text_search 失败: %s", exc, exc_info=True)

        return found, keywords

    # 使用重新搜索关键词（self-loop 时由路由函数注入）或原始事故描述（首次）
    retry_query = state.get("location_retry_query")
    if retry_query:
        # 用户手动输入关键词 → 跳过 LLM 提取，直接搜索
        candidates, keywords = await _search_amap(retry_query, exact=True)
    else:
        candidates, keywords = await _search_amap(incident)

    await adispatch_custom_event(
        "em_event",
        {
            "type": "location_candidates",
            "data": {"candidates": candidates, "query": keywords},
        },
        config=config,
    )

    selected: Any = interrupt({
        "type":       "location_select",
        "candidates": candidates,
        "query":      keywords,
    })

    # 用户输入新关键词重新搜索 → 写入 state 由路由函数触发 self-loop
    if isinstance(selected, str) and selected.startswith("search:"):
        new_query = selected[7:].strip()
        logger.info("Weave: 用户重新搜索，关键词=%r", new_query)
        return {"location_retry_query": new_query or incident}

    # 用户选定了候选地点
    # 注意：不修改 state["incident"]，保留原始用户描述。
    # phase_dispatch 会用 incident_lat/lng + incident_location_name 单独拼【事故地点】块，
    # 避免地点标签被重复拼接（incident 本身可能已含地名文字）。
    if isinstance(selected, dict) and selected.get("lat"):
        logger.info("Weave: 地点确认 → %s", selected["name"])
        return {
            "incident_location": selected,
            "incident_lat": float(selected["lat"]),
            "incident_lng": float(selected["lng"]),
            "incident_location_name": selected.get("name", ""),
            "location_retry_query": None,
        }

    # 其他情况（跳过）
    logger.info("Weave: 地点消歧用户跳过，继续无确认坐标")
    return {"incident_location": None, "location_retry_query": None}


async def create_single_step_plan(state: WeaveState, config: RunnableConfig) -> dict:
    """
    跳过研判阶段，将用户直接指令转化为携带 execution_tool/execution_params 的单步计划。
    与 phase_aggregate 输出格式相同，走相同的：
      hitl_plan_review → execute_all_parallel → A2A(execution_intent) → executor
    """
    from config import settings

    llm = ChatOpenAI(model=settings.llm_model, temperature=0)

    incident = state["incident"]
    incident_location = state.get("incident_location") or {}
    location_name = incident_location.get("name", "（地点未确认）")
    incident_lat = state.get("incident_lat") or incident_location.get("lat")
    incident_lng = state.get("incident_lng") or incident_location.get("lng")
    a2a_urls: dict[str, str] = state.get("a2a_urls") or {}
    dept_codes = state.get("selected_dept_codes") or list(a2a_urls.keys())
    dept_list = "、".join(dept_codes)

    tool_enum_lines = [
        f"  {' / '.join(tools)}（{dept}）"
        for dept, tools in DEPT_WRITE_TOOL_MAP.items()
        if dept in dept_codes
    ]
    tool_enum = "\n".join(tool_enum_lines) or "  （无可用写操作工具）"

    coord_hint = ""
    if incident_lat and incident_lng:
        coord_hint = (
            f"\n事故坐标：lat={incident_lat:.4f}, lng={incident_lng:.4f}"
            f"（dest_lat/dest_lng 参数直接使用这两个浮点数）"
        )

    system = SystemMessage(content=build_single_step_plan_system(tool_enum, dept_list))
    human = HumanMessage(
        content=f"用户指令：{incident}\n当前事故地点：{location_name}{coord_hint}"
    )

    try:
        resp = await llm.ainvoke([system, human])
        content = str(resp.content).strip()
        m = re.search(r"\{[\s\S]*\}", content)
        raw = json.loads(m.group()) if m else {}
    except Exception:
        logger.exception("Weave: create_single_step_plan LLM 失败，使用默认步骤")
        raw = {}

    fallback_dept = dept_codes[0] if dept_codes else "fire_brigade"
    step = PlanStep(
        step_id=raw.get("step_id", "step-001"),
        title=_ensure_specific_title(raw.get("title", incident[:30]), incident),
        dept_code=raw.get("dept_code", fallback_dept),
        task=raw.get("task") or f"立即执行：{incident}",
        is_high_risk=bool(raw.get("is_high_risk", True)),
        status="pending",
        map_layer=raw.get("map_layer") or None,
        result_summary="",
        execution_tool=raw.get("execution_tool") or None,
        execution_params=raw.get("execution_params") or None,
    )

    await adispatch_custom_event(
        "em_event",
        {"type": "dispatch_plan", "data": {"steps": [step]}},
        config=config,
    )
    logger.info(
        "Weave: 单步直接指令计划已生成: %s | execution_tool=%s",
        step["title"], step.get("execution_tool"),
    )
    return {"dispatch_plan": [step]}


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
    incident_info = state["incident"]
    system = SystemMessage(content=build_phase_aggregate_system(selected_depts, incident=incident_info))
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

    _llm_raw: str = "(LLM 未调用)"
    try:
        resp = await llm.ainvoke([system, human])
        _llm_raw = str(resp.content)
        content = _llm_raw.strip()
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
        logger.exception(
            "Weave: 执行计划生成失败，使用默认计划；LLM 原始输出(前2000字):\n%s",
            _llm_raw[:2000],
        )
        raw_steps = _default_plan(state)

    # ── 场景规则兜底：LLM 有时忽略 execution_tool 要求，此处代码层强制修正 ─────────
    # 事故坐标直接来自 WeaveState（用户 location_disambig 确认），无则传 "auto"
    _dest_lat: float | str = state.get("incident_lat") or "auto"
    _dest_lng: float | str = state.get("incident_lng") or "auto"
    _DEPT_REQUIRED_TOOL: dict[str, tuple[str, dict]] = {
        "medical_ems": (
            "dispatch_ambulance",
            {"ambulance_id": "auto", "dest_lat": _dest_lat, "dest_lng": _dest_lng, "patient_type": "外伤"},
        ),
        "fire_brigade": (
            "dispatch_fire_trucks",
            # truck_count 填 "auto"，executor 的 _normalize_params 会从研判报告里取整数；
            # 无法解析时工具会以最小可用数量执行（不绑定具体数字）
            {"station_id": "auto", "truck_count": "auto", "dest_lat": _dest_lat, "dest_lng": _dest_lng},
        ),
        "traffic_control": (
            "apply_evacuation_plan",
            {"level": "Ⅲ"},
        ),
        "emergency_supplies": (
            # 使用通用 Ⅲ 级标准调拨包，而非硬编码特定物资名称和数量
            "allocate_standard_pack",
            {"level": "Ⅲ"},
        ),
    }
    for dept_code, (required_tool, default_params) in _DEPT_REQUIRED_TOOL.items():
        if dept_code not in selected_depts:
            continue
        # 已有该部门的执行步骤 → 跳过
        if any(s.get("dept_code") == dept_code and s.get("execution_tool") for s in raw_steps):
            continue
        # 找该部门的第一个步骤并注入 execution_tool
        for s in raw_steps:
            if s.get("dept_code") == dept_code:
                s["execution_tool"] = required_tool
                if not s.get("execution_params"):
                    s["execution_params"] = default_params
                logger.warning(
                    "phase_aggregate: LLM 未遵守场景规则，强制修正 %s 步骤 execution_tool=%r",
                    dept_code, required_tool,
                )
                break

    plan: list[PlanStep] = []
    for i, s in enumerate(raw_steps):
        task = s.get("task", "")
        execution_tool = s.get("execution_tool") or None
        # is_high_risk 由 execution_tool 严格决定：有写操作工具 = 高危步骤
        # 不接受 LLM 对无写操作步骤的高危标记（防止 env_agency 等纯监测步骤被误标）
        is_high_risk = execution_tool is not None
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

    # ── 物理警戒圈：500m 硬隔离线（区别于 ERPG 化学扩散圈）──────────────────────
    # 优先使用地点消歧后的精确坐标；否则从研判数据三级推导（ERPG 圆心 / 传感器质心）
    _cordon_lat = state.get("incident_lat")
    _cordon_lng = state.get("incident_lng")
    if _cordon_lat is not None and _cordon_lng is not None:
        incident_center: list[float] | None = [float(_cordon_lng), float(_cordon_lat)]
    else:
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
    """LLM 失败时的兜底计划。标题/任务均含明显 [兜底] 标记，方便测试时识别 LLM 是否正常工作。"""
    incident = state.get("incident", "")
    return [
        {
            "step_id": f"step-{i+1:03d}",
            "title": f"[兜底] {code} 应急响应",
            "dept_code": code,
            "task": (
                f"[FALLBACK — LLM 执行计划生成失败，请检查后台日志定位原因]\n"
                f"事故描述：{incident[:80]}"
            ),
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
                params = dict(step.get("execution_params") or {})
                # 从用户编辑后的 title/task 文本中重新提取关键参数，使文字修改真正生效
                _text = f"{step.get('title', '')} {step.get('task', '')}"
                if "truck_count" in params:
                    _m = re.search(r"(\d+)\s*辆", _text)
                    if _m:
                        params["truck_count"] = int(_m.group(1))
                if "unit_count" in params:
                    _m = re.search(r"(\d+)\s*辆", _text)
                    if _m:
                        params["unit_count"] = int(_m.group(1))
                execution_context["execution_intent"] = {
                    "tool_name": step["execution_tool"],
                    "params": params,
                }

            logger.info(
                "Weave: 步骤 %s [%s] execution_tool=%r → execution_intent=%s",
                step["step_id"], step["dept_code"],
                step.get("execution_tool"),
                "已注入" if execution_context else "未注入（纯研判步骤）",
            )

            # 执行阶段：追加说明让 reporter 不对比任务描述中的具体编号
            # （LLM 计划可能写"A5 A3 两辆"，但 executor 按就近原则自动解析实际车号）
            exec_task = step["task"]
            if execution_context:
                exec_task += "\n\n【执行说明】实际调度资源（车辆编号/站点等）由执行器就近自动解析，以下方执行器返回的确认结果为准，不要对比或核查计划描述中列举的具体编号。"

            result = await _call_dept_a2a(
                step["dept_code"],
                exec_task,
                a2a_urls=a2a_urls,
                context=execution_context,
            )
            exec_status = "done" if result.get("status") == "completed" else "failed"

            # route-need 由 dept_code 判定（不依赖 LLM map_layer，后者常漏标 supply_route）
            # 召回/撤销步骤不合成路线，否则 clear_routes_for_dept 事件会被新路线覆盖
            _RECALL_TOOLS = {"recall_fire_trucks", "recall_ambulance"}
            needs_route = (
                step["dept_code"] in _DEPT_ROUTE_LAYER
                and step.get("execution_tool") not in _RECALL_TOOLS
            )

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
                _inc_lat = state.get("incident_lat")
                _inc_lng = state.get("incident_lng")
                _inc_override = [_inc_lng, _inc_lat] if _inc_lat and _inc_lng else None
                route_result = await _synthesize_route(step, state["dept_reports"], incident_override=_inc_override)
                if route_result:
                    synth_out: list[dict] = []
                    extract_map_update("plan_driving_route", route_result, "", synth_out, step["dept_code"])
                    _uc_raw = (step.get("execution_params") or {}).get("truck_count") \
                        or (step.get("execution_params") or {}).get("unit_count") or 1
                    try:
                        _unit_count = int(_uc_raw)
                    except (TypeError, ValueError):
                        _unit_count = 1
                    for me in synth_out:
                        me["layer"] = "routes"
                        me["step_id"] = step["step_id"]
                        me["dept_code"] = step["dept_code"]
                        me["synthesized"] = True
                        me["unit_count"] = _unit_count
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
        "请生成一份简洁的应急处置综合报告（200字以内），"
        "区分【已完成】【已跳过】【失败】步骤，给出处置成果和下一步建议。"
    )
    _system = SystemMessage(
        content=(
            "你是城市应急指挥中心汇报专家。"
            "严格基于提供的执行结果摘要生成报告，不得推测或补充摘要中未提及的事实，"
            "不得捏造步骤执行结果，不得使用「应该」「可能」等不确定性措辞。"
        )
    )

    try:
        resp = await llm.ainvoke([_system, HumanMessage(content=prompt)])
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


# ── 路由函数 ──────────────────────────────────────────────────────────────────

def _route_after_classify(state: WeaveState) -> str:
    intent      = state.get("intent", "incident_response")
    has_location = state.get("incident_location") is not None
    is_citywide  = state.get("event_scope") == "citywide"

    if intent == "direct_command":
        return "location_disambig" if not has_location else "create_single_step_plan"

    if intent == "escalation":
        return "phase_dispatch"

    if is_citywide:
        return "phase_dispatch"
    return "location_disambig" if not has_location else "phase_dispatch"


def _route_after_location(state: WeaveState) -> str:
    # 用户要求重新搜索地点 → self-loop 回到 location_disambig
    if state.get("location_retry_query"):
        return "location_disambig"
    intent = state.get("intent", "incident_response")
    if intent == "direct_command":
        return "create_single_step_plan"
    return "phase_dispatch"


# ── 图工厂 ────────────────────────────────────────────────────────────────────

def build_weave_graph(checkpointer: Any) -> Any:
    """
    构建并返回编译后的 Weave Supervisor 图。

    拓扑（多轮 + 地点消歧，Plan B + Plan C）：
        START → classify_intent → (条件边)
          → location_disambig → (条件边) → phase_dispatch | create_single_step_plan
          → phase_dispatch → phase_aggregate → hitl_plan_review
          → create_single_step_plan → hitl_plan_review
          → hitl_plan_review → execute_all_parallel → final_report → END
    """
    graph = StateGraph(WeaveState)

    graph.add_node("classify_intent",        classify_intent)
    graph.add_node("location_disambig",       location_disambig)
    graph.add_node("create_single_step_plan", create_single_step_plan)
    graph.add_node("phase_dispatch",          phase_dispatch)
    graph.add_node("phase_aggregate",         phase_aggregate)
    graph.add_node("hitl_plan_review",        hitl_plan_review)
    graph.add_node("execute_all_parallel",    execute_all_parallel)
    graph.add_node("final_report",            final_report)

    graph.add_edge(START, "classify_intent")

    graph.add_conditional_edges(
        "classify_intent",
        _route_after_classify,
        {
            "location_disambig":       "location_disambig",
            "create_single_step_plan": "create_single_step_plan",
            "phase_dispatch":          "phase_dispatch",
        },
    )
    graph.add_conditional_edges(
        "location_disambig",
        _route_after_location,
        {
            "location_disambig":       "location_disambig",   # 重新搜索 self-loop
            "create_single_step_plan": "create_single_step_plan",
            "phase_dispatch":          "phase_dispatch",
        },
    )

    graph.add_edge("create_single_step_plan", "hitl_plan_review")
    graph.add_edge("phase_dispatch",           "phase_aggregate")
    graph.add_edge("phase_aggregate",          "hitl_plan_review")
    graph.add_edge("hitl_plan_review",         "execute_all_parallel")
    graph.add_edge("execute_all_parallel",     "final_report")
    graph.add_edge("final_report",             END)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("WeaveGraph: 编译完成（多轮 + 地点消歧，Plan B + Plan C）")
    return compiled
