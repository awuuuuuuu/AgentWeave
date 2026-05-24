"""
地图数据提取（城市应急 Demo 领域适配层）

把部门 MCP 工具调用的原始结果转换为前端可渲染的 map_update 结构
（markers / circles / route + layer + dept_code）。

定位说明：此模块是**领域专属**适配层（救护车 / ERPG 扩散圈 / 路口信号 / 仓库 / 路线
等城市应急 Demo 概念），与通用 Analyst agent 节点解耦。Analyst 仅调用 extract_map_update，
不感知具体业务工具。后续若需将提取逻辑下放到各部门，可在此基础上引入
dept_code → extractor 注册表。

每条 map_update 附带 layer 字段（供前端图层 toggle 分桶）；路线类附带 dept_code
（供前端按部门着色）。
"""
from __future__ import annotations

import json as _json

# 路口坐标表（与 demo/city_state.py 种子数据保持一致；两处须同步更新）
_ISECT_COORDS: dict[str, tuple[float, float]] = {
    "S1": (39.1235, 117.7120), "S2": (39.1210, 117.7100),
    "S3": (39.1195, 117.7082), "S4": (39.1267, 117.7200),
    "S5": (39.1340, 117.7082), "S6": (39.1380, 117.7178),
    "S7": (39.1130, 117.7380), "S8": (39.1310, 117.7212),
}


def _parse_list_result(result: object, content: str) -> list[dict]:
    """将 MCP 工具调用结果健壮地解析为 list[dict]。

    MCP adapter 可能返回：
    - list[dict]（业务字段）→ 直接返回
    - list[{"type":"text","text":"[...]"}]（FastMCP TextContent as dict）→ 解析 text 字段
    - list[TextContent]（MCP SDK 对象）→ 取 .text 属性后解析
    - JSON 字符串 / content 回退
    """
    def _looks_like_mcp_envelope(d: dict) -> bool:
        """判断 dict 是否是 MCP TextContent 信封（有 type/text 但无业务字段）。
        注意：type 必须显式为 "text" 或 "resource"，不接受 None——
        无 type 字段的业务 dict（如 {"text": "...", "lat": 39.1}）不是信封。
        """
        return "text" in d and d.get("type") in ("text", "resource")

    def _extract_from_text(text: str) -> list[dict]:
        try:
            parsed = _json.loads(text)
            if isinstance(parsed, list):
                return [r for r in parsed if isinstance(r, dict)]
            if isinstance(parsed, dict):
                return [parsed]
        except Exception:
            pass
        return []

    # 1. 已是 list
    if isinstance(result, list) and result:
        # 1a. 全是业务 dict（无 MCP 信封标志）→ 直接返回
        if all(isinstance(r, dict) and not _looks_like_mcp_envelope(r) for r in result):
            return [r for r in result if isinstance(r, dict)]

        # 1b. 含 MCP TextContent（SDK 对象或 dict 形式）→ 解析 text 字段
        merged: list[dict] = []
        for item in result:
            if isinstance(item, dict):
                text = item.get("text", "")
            else:
                text = getattr(item, "text", None) or str(item)
            merged.extend(_extract_from_text(text))
        if merged:
            return merged

    # 2. content 是 JSON 字符串（str(result) 回退路径）
    return _extract_from_text(content)


def extract_map_update(
    tool_name: str,
    result: object,
    content: str,
    out: list[dict],
    dept_code: str = "",
) -> None:
    """从部门 MCP 工具调用结果中提取地图数据，追加到 out 列表。

    工具名匹配用 in 而非 ==，兼容 MultiServerMCPClient 可能添加的服务名前缀
    （如 amap_plan_driving_route）。

    每条 map_update 附带 layer 字段（供前端图层 toggle 分桶）；路线类附带 dept_code
    （供前端按部门着色）。
    """
    # ── 资源位置工具：list_ambulances ─────────────────────────────────────────
    if "list_ambulances" in tool_name:
        items: list[dict] = _parse_list_result(result, content)
        markers = [
            {
                "position": [float(r["lng"]), float(r["lat"])],
                "label": str(r.get("id", "?")),
                "icon": "🚑",
                "meta": f"状态:{r.get('status','')}",
            }
            for r in items
            if isinstance(r, dict) and r.get("lat") and r.get("lng")
        ]
        if markers:
            lngs = [m["position"][0] for m in markers]
            lats = [m["position"][1] for m in markers]
            out.append({
                "title": "救护车待命位置",
                "center": [sum(lngs) / len(lngs), sum(lats) / len(lats)],
                "zoom": 12,
                "markers": markers,
                "layer": "resources",
            })
        return

    # ── 资源位置工具：get_hospital_capacity ───────────────────────────────────
    if "get_hospital_capacity" in tool_name:
        items_h: list[dict] = _parse_list_result(result, content)
        markers_h = [
            {
                "position": [float(r["lng"]), float(r["lat"])],
                "label": str(r.get("name", r.get("id", "?"))),
                "icon": "🏥",
                "meta": f"ICU:{r.get('icu_available',0)} 急诊:{r.get('emergency_available',0)}",
            }
            for r in items_h
            if isinstance(r, dict) and r.get("lat") and r.get("lng")
        ]
        if markers_h:
            lngs_h = [m["position"][0] for m in markers_h]
            lats_h = [m["position"][1] for m in markers_h]
            out.append({
                "title": "医院接诊能力",
                "center": [sum(lngs_h) / len(lngs_h), sum(lats_h) / len(lats_h)],
                "zoom": 12,
                "markers": markers_h,
                "layer": "resources",
            })
        return

    # ── calculate_plume → ERPG 三圈 ──────────────────────────────────────────
    if "calculate_plume" in tool_name:
        parsed_p: dict | None = None
        if isinstance(result, dict):
            parsed_p = result
        elif isinstance(result, list) and result:
            first_p = result[0]
            text_p = (
                getattr(first_p, "text", None)
                or (first_p.get("text") if isinstance(first_p, dict) else None)
                or str(first_p)
            )
            try:
                parsed_p = _json.loads(text_p)
            except Exception:
                pass
        if not isinstance(parsed_p, dict):
            try:
                parsed_p = _json.loads(content)
            except Exception:
                return
        if not isinstance(parsed_p, dict):
            return

        center_d = parsed_p.get("center", {})
        lat_p, lng_p = center_d.get("lat"), center_d.get("lng")
        if not (lat_p and lng_p):
            return

        circles = []
        for key, color, label in [
            ("erpg3_radius_m", "#ef4444", "ERPG-3 致命区"),
            ("erpg2_radius_m", "#f97316", "ERPG-2 重伤区"),
            ("erpg1_radius_m", "#facc15", "ERPG-1 疏散区"),
        ]:
            radius = parsed_p.get(key)
            if radius:
                circles.append({
                    "center": [float(lng_p), float(lat_p)],
                    "radius": float(radius),
                    "color": color,
                    "label": label,
                })
        if circles:
            axis = parsed_p.get("plume_axis_deg", 0)
            out.append({
                "title": f"氨气扩散范围（轴向{axis:.0f}°）",
                "center": [float(lng_p), float(lat_p)],
                "zoom": 13,
                "circles": circles,
                "layer": "plume",
            })
        return

    # ── list_intersections → 路口信号状态（图标按模式区分）──────────────────────
    if "list_intersections" in tool_name:
        items_i: list[dict] = _parse_list_result(result, content)
        _MODE_COLOR = {
            "全红封闭": "#ef4444",
            "应急绿波": "#34d399",
            "单向清空": "#facc15",
            "消防应急": "#f97316",
            "正常":     "#6b7280",
        }
        _MODE_ICON = {
            "全红封闭": "🚫",
            "应急绿波": "🟢",
            "单向清空": "⬆️",
            "消防应急": "🔴",
            "正常":     "🚦",
        }
        markers_i = [
            {
                "position": [float(r["lng"]), float(r["lat"])],
                "label": str(r.get("name", r.get("id", "?"))),
                "icon": _MODE_ICON.get(str(r.get("mode", "正常")), "🚦"),
                "meta": str(r.get("mode", "正常")),
                "color": _MODE_COLOR.get(str(r.get("mode", "正常")), "#6b7280"),
            }
            for r in items_i
            if isinstance(r, dict) and r.get("lat") and r.get("lng")
        ]
        if markers_i:
            lngs_i = [m["position"][0] for m in markers_i]
            lats_i = [m["position"][1] for m in markers_i]
            out.append({
                "title": "路口信号状态",
                "center": [sum(lngs_i) / len(lngs_i), sum(lats_i) / len(lats_i)],
                "zoom": 13,
                "markers": markers_i,
                "layer": "signals",
            })
        return

    # ── set_mode → 单路口信号模式变更（含 lat/lng）─────────────────────────────
    if "set_mode" in tool_name and "list_intersections" not in tool_name:
        parsed_sm: dict | None = None
        if isinstance(result, dict):
            parsed_sm = result
        elif isinstance(result, list) and result:
            first_sm = result[0]
            text_sm = (
                getattr(first_sm, "text", None)
                or (first_sm.get("text") if isinstance(first_sm, dict) else None)
                or str(first_sm)
            )
            try:
                parsed_sm = _json.loads(text_sm)
            except Exception:
                pass
        if not isinstance(parsed_sm, dict):
            try:
                parsed_sm = _json.loads(content)
            except Exception:
                pass
        if isinstance(parsed_sm, dict):
            lat_sm, lng_sm = parsed_sm.get("lat"), parsed_sm.get("lng")
            if lat_sm and lng_sm:
                mode_sm = str(parsed_sm.get("mode", "正常"))
                _MC = {"全红封闭": "#ef4444", "应急绿波": "#34d399",
                       "单向清空": "#facc15", "消防应急": "#f97316", "正常": "#6b7280"}
                _MI = {"全红封闭": "🚫", "应急绿波": "🟢",
                       "单向清空": "⬆️", "消防应急": "🔴", "正常": "🚦"}
                out.append({
                    "title": f"路口信号变更：{parsed_sm.get('name', parsed_sm.get('id', '?'))}",
                    "center": [float(lng_sm), float(lat_sm)],
                    "zoom": 14,
                    "markers": [{
                        "position": [float(lng_sm), float(lat_sm)],
                        "label": str(parsed_sm.get("name", parsed_sm.get("id", "?"))),
                        "icon": _MI.get(mode_sm, "🚦"),
                        "meta": mode_sm,
                        "color": _MC.get(mode_sm, "#6b7280"),
                    }],
                    "layer": "signals",
                })
        return

    # ── apply_evacuation_plan → 批量路口模式变更（用内置坐标表）────────────────
    if "apply_evacuation_plan" in tool_name:
        parsed_ep: dict | None = None
        if isinstance(result, dict):
            parsed_ep = result
        elif isinstance(result, list) and result:
            first_ep = result[0]
            text_ep = (
                getattr(first_ep, "text", None)
                or (first_ep.get("text") if isinstance(first_ep, dict) else None)
                or str(first_ep)
            )
            try:
                parsed_ep = _json.loads(text_ep)
            except Exception:
                pass
        if not isinstance(parsed_ep, dict):
            try:
                parsed_ep = _json.loads(content)
            except Exception:
                return
        if not isinstance(parsed_ep, dict):
            return
        updated_list = parsed_ep.get("intersections", [])
        _MC2 = {"全红封闭": "#ef4444", "应急绿波": "#34d399",
                "单向清空": "#facc15", "消防应急": "#f97316", "正常": "#6b7280"}
        _MI2 = {"全红封闭": "🚫", "应急绿波": "🟢",
                "单向清空": "⬆️", "消防应急": "🔴", "正常": "🚦"}
        markers_ep = []
        for item in updated_list:
            if not isinstance(item, dict):
                continue
            sid = item.get("id", "")
            mode_ep = str(item.get("mode", "正常"))
            coords_ep = _ISECT_COORDS.get(sid)
            if not coords_ep:
                continue
            lat_ep, lng_ep = coords_ep
            markers_ep.append({
                "position": [lng_ep, lat_ep],
                "label": sid,
                "icon": _MI2.get(mode_ep, "🚦"),
                "meta": mode_ep,
                "color": _MC2.get(mode_ep, "#6b7280"),
            })
        if markers_ep:
            lngs_ep = [m["position"][0] for m in markers_ep]
            lats_ep = [m["position"][1] for m in markers_ep]
            level_ep = parsed_ep.get("level", "")
            out.append({
                "title": f"应急预案 {level_ep}：路口信号批量更新",
                "center": [sum(lngs_ep) / len(lngs_ep), sum(lats_ep) / len(lats_ep)],
                "zoom": 13,
                "markers": markers_ep,
                "layer": "signals",
            })
        return

    # ── get_critical_alarms / get_sensor_readings → 传感器报警标记 ────────────
    if "get_critical_alarms" in tool_name or "get_sensor_readings" in tool_name:
        items_s: list[dict] = _parse_list_result(result, content)
        _SENSOR_ICON: dict[str, str] = {
            "氨气浓度": "💨",
            "压力":     "⚡",
            "烟雾":     "💨",
            "可燃气体": "🔥",
            "一氧化碳": "⚠️",
        }
        markers_s = []
        for r in items_s:
            if not isinstance(r, dict):
                continue
            lat_s, lng_s = r.get("lat"), r.get("lng")
            if not (lat_s and lng_s):
                continue
            if "get_sensor_readings" in tool_name and not r.get("is_alarm"):
                continue
            stype = str(r.get("sensor_type", ""))
            val = r.get("current_value", 0)
            unit = r.get("unit", "")
            markers_s.append({
                "position": [float(lng_s), float(lat_s)],
                "label": str(r.get("id", "?")),
                "icon": _SENSOR_ICON.get(stype, "⚠️"),
                "meta": f"{val}{unit}",
            })
        if markers_s:
            lngs_s = [m["position"][0] for m in markers_s]
            lats_s = [m["position"][1] for m in markers_s]
            out.append({
                "title": "现场传感器报警",
                "center": [sum(lngs_s) / len(lngs_s), sum(lats_s) / len(lats_s)],
                "zoom": 15,
                "markers": markers_s,
                "layer": "sensors",
            })
        return

    # ── get_inventory → 📦 仓库位置（从 MCP 响应 warehouse 字段读取）───────────
    if "get_inventory" in tool_name:
        # 解析 MCP 响应：{warehouse: {name, lat, lng, address}, items: [...]}
        parsed_wh: dict | None = None
        if isinstance(result, dict):
            parsed_wh = result
        elif isinstance(result, list) and result:
            first_wh = result[0]
            text_wh = (
                getattr(first_wh, "text", None)
                or (first_wh.get("text") if isinstance(first_wh, dict) else None)
                or str(first_wh)
            )
            try:
                parsed_wh = _json.loads(text_wh)
            except Exception:
                pass
        if not isinstance(parsed_wh, dict):
            try:
                parsed_wh = _json.loads(content)
            except Exception:
                pass
        wh = parsed_wh.get("warehouse", {}) if isinstance(parsed_wh, dict) else {}
        lat_wh = wh.get("lat")
        lng_wh = wh.get("lng")
        name_wh = wh.get("name", "应急物资中转站")
        if lat_wh and lng_wh:
            out.append({
                "title": name_wh,
                "center": [float(lng_wh), float(lat_wh)],
                "zoom": 13,
                "markers": [{
                    "position": [float(lng_wh), float(lat_wh)],
                    "label": name_wh,
                    "icon": "📦",
                    "meta": "应急物资",
                }],
                "layer": "warehouse",
            })
        return

    # ── get_incident_timeline → 🏭 事故源点（物理泄漏位置，区别于 ERPG 圆心）──────
    if "get_incident_timeline" in tool_name:
        parsed_it: dict | None = None
        if isinstance(result, dict):
            parsed_it = result
        elif isinstance(result, list) and result:
            first_it = result[0]
            text_it = (
                getattr(first_it, "text", None)
                or (first_it.get("text") if isinstance(first_it, dict) else None)
                or str(first_it)
            )
            try:
                parsed_it = _json.loads(text_it)
            except Exception:
                pass
        if not isinstance(parsed_it, dict):
            try:
                parsed_it = _json.loads(content)
            except Exception:
                return
        src = parsed_it.get("source_location", {}) if isinstance(parsed_it, dict) else {}
        lat_it, lng_it = src.get("lat"), src.get("lng")
        if lat_it and lng_it:
            name_it = str(src.get("name", "事故源点"))
            out.append({
                "title": f"事故源点：{name_it}",
                "center": [float(lng_it), float(lat_it)],
                "zoom": 15,
                "layer": "incident_source",
                "markers": [{
                    "position": [float(lng_it), float(lat_it)],
                    "label": name_it,
                    "icon": "🏭",
                    "meta": str(src.get("equipment_id", "")),
                    "color": "#dc2626",
                }],
            })
        return

    # ── 原有逻辑：只处理 amap 工具 ────────────────────────────────────────────
    is_route   = "plan_driving_route" in tool_name
    is_geocode = "geocode" in tool_name and not is_route
    if not (is_route or is_geocode):
        return

    # result 可能是：dict、str（JSON）、list[TextContent]（MCP adapter 格式）
    parsed: dict | None = None
    if isinstance(result, dict):
        parsed = result
    elif isinstance(result, list) and result:
        # langchain-mcp-adapters 返回 [TextContent(type='text', text='...')]
        first = result[0]
        text = (
            getattr(first, "text", None)
            or (first.get("text") if isinstance(first, dict) else None)
            or str(first)
        )
        try:
            parsed = _json.loads(text)
        except Exception:
            pass
    if parsed is None:
        try:
            parsed = _json.loads(content)
        except Exception:
            return

    if not isinstance(parsed, dict):
        return

    if "plan_driving_route" in tool_name and "polyline" in parsed:
        frm = parsed.get("from", {})
        to  = parsed.get("to", {})
        _DEPT_ROUTE_LABEL = {
            "medical_ems":        ("ME 派遣路线", "🚑"),
            "traffic_control":    ("TR 疏散通道", "🚓"),
            "emergency_supplies": ("LG 物资运输", "📦"),
        }
        route_title, dest_icon = _DEPT_ROUTE_LABEL.get(dept_code, ("路线规划", "📍"))
        dist_m = parsed.get("distance_m", 0)
        dist_txt = f"{dist_m/1000:.1f}km" if dist_m >= 1000 else f"{dist_m}m"
        out.append({
            "title": f"{route_title}（{dist_txt}）",
            "center": [
                (frm.get("lng", 0) + to.get("lng", 0)) / 2,
                (frm.get("lat", 0) + to.get("lat", 0)) / 2,
            ],
            "zoom": 13,
            "layer": "routes",
            "markers": [
                {"position": [frm.get("lng", 0), frm.get("lat", 0)], "label": "出发点", "icon": "🔵"},
                {"position": [to.get("lng", 0),  to.get("lat", 0)],  "label": "目的地", "icon": dest_icon},
            ],
            "route": {
                "from": frm,
                "to":   to,
                "polyline":         parsed["polyline"],
                "distance_m":       dist_m,
                "duration_seconds": parsed.get("duration_seconds", 0),
                "dept_code":        dept_code,
            },
        })

    # geocode 仅作为中间步骤，不单独渲染地图气泡（路线地图已包含起终点 marker）
