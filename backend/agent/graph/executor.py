"""
Executor 节点：确定性 MCP 写操作执行

两种触发方式（统一路径）：
  1. 普通会话：analyst 输出 HITL_REQUIRED + EXECUTION_INTENT，HITL 批准后 supervisor 路由此节点
  2. Weave 编排：execute_all_parallel 通过 A2A context.execution_intent 触发

与 Analyst 的区别：
  Analyst  — ReAct 探索，纯只读，适合研判阶段（路径未知）
  Executor — 直接执行，适合执行阶段（操作已确定，参数可能需要查询补全）
"""
from __future__ import annotations

import json as _json
import logging
import os
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from .map_extract import extract_map_update
from .state import AgentState

logger = logging.getLogger(__name__)

_TOOL_RESULT_LIMIT = 8_000

# 交通信号默认持续时长
_DEFAULT_SIGNAL_DURATION_MIN = 120

AGENT_CARD = {
    "name": "executor",
    "description": "确定性写操作执行：接收结构化执行意图，直接调用 MCP 写操作工具，无 LLM 介入",
    "tools": ["mcp_tools"],
    "icon": "⚡",
    "color": "red",
}


def build_executor() -> object:
    """构建 Executor 节点函数"""

    async def executor_node(state: AgentState) -> dict:
        dept_code = state.get("dept_code", "")
        mcp_connections = state.get("org_mcp_connections") or []
        execution_intent: dict | None = state.get("execution_intent")

        if not execution_intent:
            logger.warning("Executor [%s]: state 中无 execution_intent，跳过执行", dept_code)
            return {
                "messages": [AIMessage(
                    content="⚠️ 未收到结构化执行意图，跳过执行。",
                    name="executor",
                )],
                "executor_count": state.get("executor_count", 0) + 1,
            }

        tool_name = execution_intent.get("tool_name")
        params = dict(execution_intent.get("params") or {})

        if not tool_name:
            return {
                "messages": [AIMessage(
                    content="⚠️ execution_intent.tool_name 为空，跳过执行。",
                    name="executor",
                )],
                "executor_count": state.get("executor_count", 0) + 1,
            }

        # ── 加载 MCP 工具 ─────────────────────────────────────────────────────
        mcp_tools: list[BaseTool] = []
        mcp_client = None
        if mcp_connections:
            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient
                servers = {
                    conn["name"]: {"url": conn["url"], "transport": "streamable_http"}
                    for conn in mcp_connections
                    if conn.get("name") and conn.get("url")
                }
                if servers:
                    mcp_client = MultiServerMCPClient(servers)
                    mcp_tools = await mcp_client.get_tools()
            except Exception as exc:
                logger.warning("Executor [%s]: MCP 工具加载失败: %s", dept_code, exc)

        mcp_tool_map: dict[str, BaseTool] = {t.name: t for t in mcp_tools}
        tool = mcp_tool_map.get(tool_name)

        if tool is None:
            logger.error("Executor [%s]: 工具 %r 不存在，可用: %s", dept_code, tool_name, list(mcp_tool_map))
            return {
                "messages": [AIMessage(
                    content=f"⚠️ 工具 {tool_name!r} 在 MCP Server 中不存在，执行失败。",
                    name="executor",
                )],
                "executor_count": state.get("executor_count", 0) + 1,
            }

        # ── 参数格式规范化：移除无效字段，补全缺失必填项 ──────────────────────
        params = _normalize_params(tool_name, params)

        # ── "auto" 占位符解析：运行时查询补全参数 ────────────────────────────
        if "auto" in params.values():
            tool_name, params = await _resolve_auto_params(tool_name, params, mcp_tool_map)
            tool = mcp_tool_map.get(tool_name)
            if tool is None:
                logger.error("Executor [%s]: 重定向后工具 %r 不存在", dept_code, tool_name)
                return {
                    "messages": [AIMessage(
                        content=f"⚠️ 工具 {tool_name!r} 在 MCP Server 中不存在，执行失败。",
                        name="executor",
                    )],
                    "executor_count": state.get("executor_count", 0) + 1,
                }

        # ── 召回类工具：ambulance_id 仍为 auto 说明无出车救护车，提前返回友好提示 ──
        if tool_name == "recall_ambulance" and params.get("ambulance_id") == "auto":
            logger.warning("Executor [%s]: recall_ambulance 无出车救护车可召回，跳过执行", dept_code)
            return {
                "messages": [AIMessage(
                    content="⚠️ 查询结果显示当前无救护车处于出车状态，召回操作已跳过。"
                            "若刚执行了派遣指令，可能存在短暂时延，请稍后重试此召回操作。",
                    name="executor",
                )],
                "executor_count": state.get("executor_count", 0) + 1,
            }

        # ── 直接调用写操作工具（无 LLM） ─────────────────────────────────────
        _map_updates: list[dict] = []
        _mcp_sources: list[dict] = []

        await adispatch_custom_event(
            "analyst_tool_status",
            {"step": "tool_call", "tool_name": tool_name},
        )

        try:
            result = await tool.ainvoke(params)
            # 解析 MCP content block 格式，提取可读文本
            parsed_result = _parse_mcp_result(result) or {}
            if isinstance(parsed_result, dict) and parsed_result.get("message"):
                content = parsed_result["message"]
            elif isinstance(parsed_result, dict):
                content = _json.dumps(parsed_result, ensure_ascii=False, indent=2)
            else:
                content = str(result)
            if len(content) > _TOOL_RESULT_LIMIT:
                content = content[:_TOOL_RESULT_LIMIT] + "…（结果过长已截断）"
            if isinstance(parsed_result, dict) and tool_name in ("dispatch_fire_trucks", "dispatch_ambulance"):
                # 派遣类工具：合成含路线的综合地图（跳过 markers-only 地图，避免两张重复气泡）
                route_updates = await _synthesize_dispatch_route(parsed_result, tool_name, dept_code)
                _map_updates.extend(route_updates)
            else:
                extract_map_update(tool_name, result, content, _map_updates, dept_code)
                # 召回工具：通知前端清除对应部门的已渲染路线
                if tool_name in ("recall_fire_trucks", "recall_ambulance"):
                    _map_updates.append({
                        "title": "路线清除",
                        "center": [0.0, 0.0],
                        "clear_routes_for_dept": dept_code,
                    })
            logger.info("Executor [%s]: %r 调用成功，%d 字符", dept_code, tool_name, len(content))
        except Exception as exc:
            content = f"工具 {tool_name} 调用失败: {exc}"
            logger.warning("Executor [%s]: %r 调用失败: %s", dept_code, tool_name, exc)

        _mcp_sources.append({"idx": 1, "tool_name": tool_name, "key_result": content[:400]})
        await adispatch_custom_event(
            "analyst_tool_result",
            {"idx": 1, "tool_name": tool_name, "key_result": content[:400]},
        )

        return {
            "messages": [AIMessage(content=content, name="executor")],
            "map_updates": _map_updates,
            "mcp_sources": _mcp_sources,
            "executor_count": state.get("executor_count", 0) + 1,
            "execution_intent": None,
        }

    return executor_node


def _normalize_params(tool_name: str, params: dict) -> dict:
    """清理 params 中格式错误的字段，确保符合目标工具的 schema。

    LLM 生成 execution_params 时常用站名/车号/路口名而非 DB 内部 ID，
    此函数将这些"具名"值强制还原为 "auto"，交由 _resolve_auto_params 运行时查询补全。
    """
    p = dict(params)

    if tool_name == "dispatch_ambulance":
        p.pop("destination", None)
        p.pop("priority", None)
        # 若研判阶段已填合法车号（纯 ASCII 字母+数字，如 "A5"），保留推荐值；
        # 仅在缺失、"auto" 或含非 ASCII 字符（中文站名等）时才自动查询补全
        _aid = p.get("ambulance_id", "")
        _aid_valid = (
            bool(_aid)
            and _aid != "auto"
            and _aid.isascii()
            and _aid.replace("-", "").replace("_", "").isalnum()
        )
        if not _aid_valid:
            p["ambulance_id"] = "auto"
        # LLM 有时把坐标生成为字符串（"31.237"），此处统一转浮点；缺失时置 "auto"
        for _coord in ("dest_lat", "dest_lng"):
            if _coord in p:
                if not isinstance(p[_coord], (int, float)):
                    try:
                        p[_coord] = float(p[_coord])
                    except (TypeError, ValueError):
                        p[_coord] = "auto"
            else:
                p[_coord] = "auto"
        if "patient_type" not in p:
            p["patient_type"] = "外伤"

    elif tool_name == "dispatch_fire_trucks":
        p.pop("destination", None)
        # station_id 始终用 auto：LLM 填的站名（中文站名等）与 DB ID（内部编码）不符
        p["station_id"] = "auto"
        # truck_count 必须为正整数；LLM 可能缺失或填 "auto"，尝试解析；
        # 无法确定时保留 "auto"，_resolve_auto_params 会从消防站可用数量中取最小值
        _tc = p.get("truck_count")
        if not (isinstance(_tc, int) and _tc > 0):
            try:
                _tc_int = int(_tc)  # type: ignore[arg-type]
                p["truck_count"] = _tc_int if _tc_int > 0 else "auto"
            except (TypeError, ValueError):
                p["truck_count"] = "auto"
        for _coord in ("dest_lat", "dest_lng"):
            if _coord in p:
                if not isinstance(p[_coord], (int, float)):
                    try:
                        p[_coord] = float(p[_coord])
                    except (TypeError, ValueError):
                        p[_coord] = "auto"
            else:
                p[_coord] = "auto"

    elif tool_name == "recall_fire_trucks":
        # 旧 prompt 模板用了错误字段名 truck_ids，统一丢弃
        p.pop("truck_ids", None)
        # station_id：LLM 填站名（中文站名）与 DB ID（内部编码）不符，统一用 auto 触发运行时查询
        _sid = p.get("station_id", "")
        _sid_valid = (
            bool(_sid) and _sid != "auto"
            and _sid.isascii()
            and _sid.replace("-", "").replace("_", "").isalnum()
        )
        if not _sid_valid:
            p["station_id"] = "auto"
        # truck_count：必须为正整数；LLM 可能缺失或填中文（"全部"），统一用 auto
        _tc = p.get("truck_count")
        if not (isinstance(_tc, int) and _tc > 0):
            try:
                _tc_int = int(_tc)  # type: ignore[arg-type]
                p["truck_count"] = _tc_int if _tc_int > 0 else "auto"
            except (TypeError, ValueError):
                p["truck_count"] = "auto"

    elif tool_name == "recall_ambulance":
        _aid = p.get("ambulance_id", "")
        _aid_valid = (
            bool(_aid) and _aid != "auto"
            and _aid.isascii()
            and _aid.replace("-", "").replace("_", "").isalnum()
        )
        if not _aid_valid:
            p["ambulance_id"] = "auto"

    elif tool_name == "set_mode":
        # intersection_id 始终用 auto：LLM 填的路口名与 DB ID（"INT-01"等）不符
        p["intersection_id"] = "auto"
        if "duration_min" not in p:
            p["duration_min"] = _DEFAULT_SIGNAL_DURATION_MIN

    elif tool_name == "allocate_custom":
        # LLM 常用不同物资名称，但 DB 按精确名称匹配，需规范化
        _NAME_ALIASES: dict[str, str] = {
            "正压式空气呼吸器": "空气呼吸器",
            "正压空气呼吸器": "空气呼吸器",
            "SCBA": "空气呼吸器",
            "scba": "空气呼吸器",
            "备用气瓶": "医用氧气瓶",
            "气瓶": "医用氧气瓶",
            "氧气瓶": "医用氧气瓶",
            "N95口罩": "防护口罩N95",
            "N95": "防护口罩N95",
            "防护口罩": "防护口罩N95",
            "口罩": "防护口罩N95",
            "对讲机": "通信对讲机",
            "警戒带": "隔离警戒带",
        }
        normalized: list[dict] = []
        for item in (p.get("items") or []):
            if not isinstance(item, dict):
                normalized.append(item)
                continue
            item = {k if k != "quantity" else "qty": v for k, v in item.items()}
            item["name"] = _NAME_ALIASES.get(item.get("name", ""), item.get("name", ""))
            normalized.append(item)
        p["items"] = normalized

    return p


def _parse_mcp_result(data) -> dict | list | None:
    """MCP ainvoke 可能返回原始数据或 content block 列表，统一解析为 Python 对象。

    FastMCP 将 list[dict] 序列化为多个独立 TextContent block（每项一个），
    因此必须解析所有 block 再合并，而非只取第一个。
    """
    import json as _json

    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data:
        parsed_items = []
        for item in data:
            text = (
                getattr(item, "text", None)
                or (item.get("text") if isinstance(item, dict) else None)
            )
            if text:
                try:
                    parsed_items.append(_json.loads(text))
                except Exception:
                    pass
        if parsed_items:
            return parsed_items[0] if len(parsed_items) == 1 else parsed_items
    return data


def _find_mcp_tool(mcp_tool_map: dict, name: str):
    """前缀感知工具查找：精确匹配优先，降级到后缀匹配（兼容 MultiServerMCPClient 服务名前缀）。"""
    if name in mcp_tool_map:
        return mcp_tool_map[name]
    for key, tool in mcp_tool_map.items():
        if key.endswith(f"_{name}"):
            return tool
    return None


async def _resolve_auto_params(
    tool_name: str,
    params: dict,
    mcp_tool_map: dict,
) -> tuple[str, dict]:
    """将 params 中值为 "auto" 的字段通过只读查询补全为真实值（规则驱动，非 LLM）。

    返回 (最终工具名, 补全后的参数) —— tool_name 可能因降级/重定向而改变。
    例如 set_mode(intersection_id="auto") 在无法定位具体路口时重定向为 apply_evacuation_plan。
    """
    resolved = dict(params)

    if tool_name == "dispatch_fire_trucks":
        # 坐标未解析时移除字段，由 MCP 工具决定如何处理缺省坐标
        if resolved.get("dest_lat") == "auto":
            resolved.pop("dest_lat", None)
        if resolved.get("dest_lng") == "auto":
            resolved.pop("dest_lng", None)
        if resolved.get("station_id") == "auto" or resolved.get("truck_count") == "auto":
            query = _find_mcp_tool(mcp_tool_map, "get_fire_stations")
            if query:
                try:
                    # 传入事故坐标（若有），让 get_fire_stations 按距离排序，优先选最近的有车站
                    invoke_params: dict = {}
                    if isinstance(resolved.get("dest_lat"), (int, float)):
                        invoke_params["lat"] = resolved["dest_lat"]
                    if isinstance(resolved.get("dest_lng"), (int, float)):
                        invoke_params["lng"] = resolved["dest_lng"]
                    raw = await query.ainvoke(invoke_params)
                    parsed = _parse_mcp_result(raw)
                    stations = (parsed.get("stations") or []) if isinstance(parsed, dict) else (parsed or [])
                    for s in stations:
                        if s.get("available_trucks", 0) > 0:
                            if resolved.get("station_id") == "auto":
                                resolved["station_id"] = s["id"]
                                logger.info(
                                    "Executor: dispatch_fire_trucks station_id resolved → %r (%.2fkm)",
                                    s["id"], s.get("distance_km", -1),
                                )
                            if resolved.get("truck_count") == "auto":
                                # 取最近有车站的可用数量（上限 3 辆，避免一次性清空）
                                resolved["truck_count"] = min(s.get("available_trucks", 1), 3)
                                logger.info(
                                    "Executor: dispatch_fire_trucks truck_count resolved → %d",
                                    resolved["truck_count"],
                                )
                            break
                except Exception as e:
                    logger.warning("Executor: get_fire_stations 解析失败: %s", e)
            # station_id 仍未解析时工具将以自然错误响应
            if resolved.get("truck_count") == "auto":
                resolved["truck_count"] = 2  # 通用最小调派数，不绑定特定场景
                logger.warning("Executor: dispatch_fire_trucks truck_count 未解析，使用最小值 2")

    elif tool_name == "dispatch_ambulance":
        # 坐标非浮点时移除字段，由 MCP 工具决定如何处理缺省坐标
        if not isinstance(resolved.get("dest_lat"), (int, float)):
            resolved.pop("dest_lat", None)
        if not isinstance(resolved.get("dest_lng"), (int, float)):
            resolved.pop("dest_lng", None)

        if resolved.get("ambulance_id") == "auto":
            query = _find_mcp_tool(mcp_tool_map, "list_ambulances")
            if query:
                try:
                    # 传入事故坐标（若有），让 list_ambulances 按距离排序，返回最近待命救护车
                    invoke_params: dict = {"status": "待命", "k": 1}
                    if isinstance(resolved.get("dest_lat"), (int, float)):
                        invoke_params["lat"] = resolved["dest_lat"]
                    if isinstance(resolved.get("dest_lng"), (int, float)):
                        invoke_params["lng"] = resolved["dest_lng"]
                    raw = await query.ainvoke(invoke_params)
                    parsed = _parse_mcp_result(raw)
                    # list_ambulances 返回 plain list，非 dict
                    ambulances = (parsed.get("ambulances") or []) if isinstance(parsed, dict) else (parsed or [])
                    standby = [a for a in ambulances if a.get("status") == "待命"]
                    candidate = standby or ambulances
                    if candidate:
                        resolved["ambulance_id"] = candidate[0]["id"]
                        logger.info("Executor: dispatch_ambulance ambulance_id resolved → %r (dist=%.2fkm)",
                                    candidate[0]["id"], candidate[0].get("distance_km", -1))
                except Exception as e:
                    logger.warning("Executor: list_ambulances 解析失败: %s", e)
            # list_ambulances 查询失败：让工具以自然错误响应，不使用硬编码 Demo ID
            if resolved.get("ambulance_id") == "auto":
                logger.warning("Executor: dispatch_ambulance list_ambulances 查询失败，ambulance_id 未解析")

    elif tool_name == "recall_fire_trucks":
        if resolved.get("station_id") == "auto" or resolved.get("truck_count") == "auto":
            query = _find_mcp_tool(mcp_tool_map, "get_fire_stations")
            if query:
                try:
                    raw = await query.ainvoke({})
                    parsed = _parse_mcp_result(raw)
                    stations = (parsed.get("stations") or []) if isinstance(parsed, dict) else (parsed or [])
                    # 优先选出勤车辆最多的站；无出勤时降级到任意站（工具会返回"无车可撤"）
                    dispatched_stations = [
                        (s, s.get("total_trucks", 0) - s.get("available_trucks", 0))
                        for s in stations
                        if s.get("total_trucks", 0) - s.get("available_trucks", 0) > 0
                    ]
                    if dispatched_stations:
                        best, dispatched_count = max(dispatched_stations, key=lambda x: x[1])
                        if resolved.get("station_id") == "auto":
                            resolved["station_id"] = best["id"]
                            logger.info(
                                "Executor: recall_fire_trucks station_id resolved → %r (dispatched=%d)",
                                best["id"], dispatched_count,
                            )
                        if resolved.get("truck_count") == "auto":
                            resolved["truck_count"] = dispatched_count
                            logger.info(
                                "Executor: recall_fire_trucks truck_count resolved → %d", dispatched_count,
                            )
                    elif stations and resolved.get("station_id") == "auto":
                        # 无出勤车辆：随便找一个站，truck_count 用 99（工具会 clamp 到 0）
                        resolved["station_id"] = stations[0]["id"]
                        logger.info("Executor: recall_fire_trucks 无出勤车辆，station_id fallback → %r", stations[0]["id"])
                except Exception as e:
                    logger.warning("Executor: get_fire_stations (recall) 解析失败: %s", e)
        # station_id 仍为 auto 时：无出勤车辆可撤，记录 warning 并保持 "auto"
        # 工具会收到非法 station_id 并以自然错误响应，错误信息将在执行卡片中展示
        if resolved.get("station_id") == "auto":
            logger.warning("Executor: recall_fire_trucks 无出勤消防车可召回，station_id 未能解析")
        # 兜底：recall_fire_trucks 工具内部会 clamp 到实际出勤数，传大值安全
        if resolved.get("truck_count") == "auto":
            resolved["truck_count"] = 99
            logger.warning("Executor: recall_fire_trucks truck_count fallback → 99")

    elif tool_name == "recall_ambulance":
        if resolved.get("ambulance_id") == "auto":
            query = _find_mcp_tool(mcp_tool_map, "list_ambulances")
            if query:
                try:
                    raw = await query.ainvoke({"status": "出车"})
                    parsed = _parse_mcp_result(raw)
                    ambulances = (parsed.get("ambulances") or []) if isinstance(parsed, dict) else (parsed or [])
                    dispatched = [a for a in ambulances if a.get("status") == "出车"]
                    if dispatched:
                        resolved["ambulance_id"] = dispatched[0]["id"]
                        logger.info("Executor: recall_ambulance ambulance_id resolved → %r", dispatched[0]["id"])
                except Exception as e:
                    logger.warning("Executor: list_ambulances (recall) 解析失败: %s", e)
            if resolved.get("ambulance_id") == "auto":
                logger.warning("Executor: recall_ambulance 无出车救护车可召回，ambulance_id 未解析")

    elif tool_name == "set_mode":
        if resolved.get("duration_min") is None or resolved.get("duration_min") == "auto":
            resolved["duration_min"] = _DEFAULT_SIGNAL_DURATION_MIN
        if resolved.get("intersection_id") == "auto":
            # 尝试从 list_intersections 获取具体路口 ID
            query = _find_mcp_tool(mcp_tool_map, "list_intersections")
            resolved_id = None
            if query:
                try:
                    raw = await query.ainvoke({})
                    parsed = _parse_mcp_result(raw)
                    # list_intersections 返回 plain list
                    intersections = (parsed.get("intersections") or []) if isinstance(parsed, dict) else (parsed or [])
                    if intersections:
                        resolved_id = intersections[0]["id"]
                        resolved["intersection_id"] = resolved_id
                        logger.info("Executor: set_mode intersection_id resolved → %r", resolved_id)
                except Exception as e:
                    logger.warning("Executor: list_intersections 解析失败: %s", e)

            # 无法定位具体路口时：重定向到 apply_evacuation_plan（批量应急预案）
            if resolved.get("intersection_id") == "auto":
                _mode_to_level = {
                    "消防应急": "Ⅲ",
                    "全红封闭": "Ⅳ",
                    "单向清空": "Ⅱ",
                    "应急绿波": "Ⅲ",
                }
                level = _mode_to_level.get(resolved.get("mode", ""), "Ⅲ")
                logger.info(
                    "Executor: set_mode intersection_id 无法解析，重定向 → apply_evacuation_plan(level=%r)", level
                )
                return "apply_evacuation_plan", {"level": level}

    elif tool_name == "allocate_standard_pack" and resolved.get("level") == "auto":
        resolved["level"] = "Ⅲ"

    return tool_name, resolved


async def _synthesize_dispatch_route(parsed_result: dict, tool_name: str, dept_code: str) -> list[dict]:
    """派遣完成后，通过 AMap MCP 合成驾车路线，并用调度专属图标替换默认 marker，产出一张综合地图。"""
    from_lat = parsed_result.get("from_lat")
    from_lng = parsed_result.get("from_lng")
    to_lat = parsed_result.get("to_lat") or parsed_result.get("dest_lat")
    to_lng = parsed_result.get("to_lng") or parsed_result.get("dest_lng")
    if not (from_lat and from_lng and to_lat and to_lng):
        logger.debug("Executor: route synthesis skipped — missing coords in %s", list(parsed_result.keys()))
        return []
    amap_url = os.environ.get("AMAP_MCP_URL", "http://localhost:8106/mcp")
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        client = MultiServerMCPClient({"amap": {"url": amap_url, "transport": "streamable_http"}})
        tools = await client.get_tools()
        route_tool = next((t for t in tools if "plan_driving_route" in t.name), None)
        if not route_tool:
            logger.warning("Executor: plan_driving_route not found in AMap MCP tools")
            return []
        route_result = await route_tool.ainvoke({
            "from_lat": float(from_lat), "from_lng": float(from_lng),
            "to_lat": float(to_lat), "to_lng": float(to_lng),
        })
        route_maps: list[dict] = []
        extract_map_update("plan_driving_route", route_result, "", route_maps, dept_code)
        if not route_maps:
            logger.warning("Executor: route synthesis returned no map updates")
            return []

        # 用调度专属图标覆盖默认 marker（🔵/📍 → 🚒/🔥 或 🚑/🏥）
        is_fire = tool_name == "dispatch_fire_trucks"
        from_icon = "🚒" if is_fire else "🚑"
        dest_icon = "🔥" if is_fire else "🏥"
        station = parsed_result.get("station_name") or parsed_result.get("station_id", "出发地")
        truck_count = parsed_result.get("truck_count")
        from_label = f"{station}（{truck_count}辆）" if (is_fire and truck_count) else station
        dest_label = "事故现场"

        rmap = route_maps[0]
        frm = (rmap.get("route") or {}).get("from", {})
        to  = (rmap.get("route") or {}).get("to", {})
        rmap["markers"] = [
            {"position": [frm.get("lng", float(from_lng)), frm.get("lat", float(from_lat))], "label": from_label, "icon": from_icon},
            {"position": [to.get("lng", float(to_lng)),   to.get("lat", float(to_lat))],   "label": dest_label, "icon": dest_icon},
        ]
        rmap["title"] = f"消防车调派路线：{station} → 事故现场" if is_fire else "救护车调派路线 → 事故现场"
        rmap["layer"] = "fire_route" if is_fire else "ambulance_route"
        rmap["dept_code"] = dept_code

        logger.info("Executor: route synthesis produced %d map update(s)", len(route_maps))
        return route_maps
    except Exception as exc:
        logger.warning("Executor: route synthesis failed: %s", exc)
        return []
