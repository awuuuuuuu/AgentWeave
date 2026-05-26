"""
Weave 测试评估函数库。

每个函数返回 (passed: bool, diagnosis_message: str)。
diagnosis_message 在 passed=False 时包含调优建议，
可直接用于 `assert passed, msg`。
"""
from __future__ import annotations

import re

# ── 场景常量 ──────────────────────────────────────────────────────────────────

# ── 场景 1：建筑火灾（5 部门全量） ─────────────────────────────────────────────
INCIDENT = "世纪大道×陆家嘴环路附近建筑起火，火势蔓延，已有2人受伤，浓烟扩散"
SELECTED_DEPTS = [
    "env_agency", "medical_ems", "traffic_control",
    "emergency_supplies", "fire_brigade",
]

# ── 场景 2：交通事故（2 部门精选） ─────────────────────────────────────────────
TRAFFIC_ACCIDENT_INCIDENT = "浦东南路与东方路路口多车追尾，疑似3人受伤，道路严重拥堵"
TRAFFIC_DEPTS = ["traffic_control", "medical_ems"]

# ── 场景 5：危化品泄漏（4 部门） ───────────────────────────────────────────────
HAZMAT_INCIDENT = "张江某化工仓库疑似危化品泄漏，刺激性气味扩散，工人出现头晕症状，周边需疏散"
HAZMAT_DEPTS = ["env_agency", "fire_brigade", "medical_ems", "traffic_control"]

DEPT_KEYWORDS: dict[str, list[str]] = {
    "env_agency":         ["烟雾", "PM2.5", "空气质量", "传感器"],
    "medical_ems":        ["医疗", "救护", "医院", "伤亡"],
    "traffic_control":    ["路口", "信号", "疏散通道", "管控"],
    "emergency_supplies": ["物资", "库存", "调拨"],
    "fire_brigade":       ["消防", "灭火", "消防车", "消防站"],
}

# 研判阶段：各部门必须调用的 MCP 工具（任意一个命中即通过）
RESEARCH_MCP_WHITELIST: dict[str, list[str]] = {
    "env_agency":         ["get_sensor_readings", "get_critical_alarms"],
    "medical_ems":        ["list_ambulances", "get_hospital_capacity"],
    "traffic_control":    ["list_intersections"],
    "emergency_supplies": ["get_inventory"],
    "fire_brigade":       ["get_fire_stations", "get_water_supplies"],
}

# 交通事故场景研判白名单（工具集与主白名单相同，此处明确仅测 2 部门）
TRAFFIC_RESEARCH_MCP_WHITELIST: dict[str, list[str]] = {
    "traffic_control": ["list_intersections"],
    "medical_ems":     ["list_ambulances", "get_hospital_capacity"],
}

# 危化品泄漏场景研判白名单
HAZMAT_RESEARCH_MCP_WHITELIST: dict[str, list[str]] = {
    "env_agency":      ["get_sensor_readings", "get_critical_alarms"],
    "fire_brigade":    ["get_fire_stations", "get_water_supplies"],
    "medical_ems":     ["list_ambulances", "get_hospital_capacity"],
    "traffic_control": ["list_intersections"],
}

# 执行阶段：各部门应有的写操作工具（空列表 = 该部门无写操作要求）
EXECUTION_MCP_WHITELIST: dict[str, list[str]] = {
    "medical_ems":        ["dispatch_ambulance", "recall_ambulance"],
    "traffic_control":    ["set_mode", "apply_evacuation_plan"],
    "emergency_supplies": ["allocate_custom", "allocate_standard_pack"],
    "env_agency":         [],
    "fire_brigade":       ["dispatch_fire_trucks", "recall_fire_trucks"],
}

EXPECTED_MAP_LAYERS: dict[str, list[str]] = {
    "medical_ems":        ["ambulance_route"],
    "traffic_control":    ["signal_update", "evacuation_route"],
    "emergency_supplies": [],
    "env_agency":         [],
    "fire_brigade":       ["fire_route"],
}

# title 中必须含数字、地名/单位词、或应急场景特征词（含化工/仓库/医院等设施词）
_SPECIFIC_TITLE_RE = re.compile(
    r"\d+|路|号|区|街|广场|大道|园|级|辆|套|台|条|处|圈|半径|警戒|火场|灭火|消防"
    r"|仓库|化工|工厂|医院|泄漏|隔离区"
)


# ── 评估函数 ──────────────────────────────────────────────────────────────────

def check_dept_keywords(dept_code: str, task_text: str) -> tuple[bool, str]:
    """验证研判任务文本包含该部门的领域关键词（至少一个）。"""
    keywords = DEPT_KEYWORDS.get(dept_code)
    if not keywords:
        return True, ""  # 未知部门不强制要求
    hit = any(kw in task_text for kw in keywords)
    if hit:
        return True, ""
    return False, (
        f"[{dept_code}] 任务文本缺少领域关键词（期望至少一个：{keywords}）\n"
        f"  实际文本前100字：{task_text[:100]}\n"
        f"  调优建议：检查 weave_supervisor._build_dept_tasks 中该部门的模板"
    )


def check_mcp_whitelist(
    dept_code: str,
    mcp_sources: list[dict],
    whitelist: dict[str, list[str]],
) -> tuple[bool, str]:
    """验证 mcp_sources 中至少有一个工具名命中白名单。白名单为空表示无要求。"""
    required = whitelist.get(dept_code, [])
    if not required:
        return True, ""
    found_tools = {s.get("tool_name", "") for s in mcp_sources}
    hit = any(any(req in tool for tool in found_tools) for req in required)
    if hit:
        return True, ""
    return False, (
        f"[{dept_code}] 缺少必要 MCP 调用\n"
        f"  期望工具（任意一个）：{required}\n"
        f"  实际调用：{list(found_tools) or '（无）'}\n"
        f"  调优建议：检查 dept_prompts.analyst_context，"
        f"添加「必须调用 MCP 工具获取实时数据」约束"
    )


def check_map_event_coordinates(map_events: list[dict]) -> tuple[bool, str]:
    """验证每条 map_event 包含合法的 center: [lng, lat] 字段。"""
    for i, ev in enumerate(map_events):
        center = ev.get("center")
        if center is None:
            return False, (
                f"map_events[{i}] 缺少 center 字段\n"
                f"  event: {ev}\n"
                f"  调优建议：检查 MCP Server 返回格式，确保包含 center: [lng, lat]"
            )
        if not (isinstance(center, (list, tuple)) and len(center) == 2):
            return False, (
                f"map_events[{i}].center 格式错误（期望 [lng, lat]，实际：{center}）\n"
                f"  调优建议：检查 MCP Server 的 map_event 序列化逻辑"
            )
        if not all(isinstance(v, (int, float)) for v in center):
            return False, (
                f"map_events[{i}].center 包含非数字值：{center}\n"
                f"  调优建议：检查 MCP Server 的坐标类型"
            )
    return True, ""


def check_key_facts_have_numbers(key_facts: list[str]) -> tuple[bool, str]:
    """验证 key_facts 中至少有一条包含数字（数量/距离/浓度等）。"""
    if not key_facts:
        return False, (
            "key_facts 为空\n"
            "  调优建议：检查 dept_prompts.analyst_context，"
            "要求「以数值形式输出关键指标」"
        )
    # 接受：阿拉伯数字、连写罗马数字（如 I/II/III）、全角罗马数字（Ⅰ-Ⅻ）
    _num_re = re.compile(r"\d+|[IVX]{1,4}(?:/[IVX]{1,4})+|[Ⅰ-Ⅻ]")
    has_num = any(_num_re.search(fact) for fact in key_facts)
    if has_num:
        return True, ""
    return False, (
        f"key_facts 中无数值信息：{key_facts}\n"
        "  调优建议：检查 dept_prompts.analyst_context，"
        "要求「量化输出，包含具体数字」"
    )


def check_plan_steps(
    steps: list[dict],
    selected_depts: list[str],
    min_steps: int = 3,
    max_steps: int = 8,
) -> list[str]:
    """
    全量校验执行计划。
    返回违规项描述列表，空列表表示全部通过。
    """
    errors: list[str] = []

    if not (min_steps <= len(steps) <= max_steps):
        errors.append(
            f"步骤数 {len(steps)} 超出合理范围 [{min_steps}, {max_steps}]\n"
            "  调优建议：检查 phase_aggregate system prompt 中的步骤数约束"
        )

    ids = [s["step_id"] for s in steps]
    if len(ids) != len(set(ids)):
        dupes = [sid for sid in ids if ids.count(sid) > 1]
        errors.append(
            f"step_id 重复：{list(set(dupes))}\n"
            "  调优建议：检查 phase_aggregate system prompt 的 step_id 唯一性要求"
        )

    invalid_depts = [s["dept_code"] for s in steps if s["dept_code"] not in selected_depts]
    if invalid_depts:
        errors.append(
            f"dept_code 不在选定部门列表中：{invalid_depts}\n"
            "  调优建议：检查 phase_aggregate system prompt 的部门代码白名单"
        )

    if not any(s.get("is_high_risk") for s in steps):
        errors.append(
            "计划中无高危步骤（至少应有 1 个写操作高危步骤）\n"
            "  调优建议：在 phase_aggregate system prompt 中强化高危判定标准示例"
        )

    vague_titles = [
        s["step_id"] for s in steps
        if not _SPECIFIC_TITLE_RE.search(s.get("title", ""))
    ]
    if vague_titles:
        errors.append(
            f"以下步骤 title 缺少具体数量/地点（step_id: {vague_titles}）\n"
            "  调优建议：在 phase_aggregate system prompt 中补充更多具体 title 示例"
        )

    covered_depts = {s["dept_code"] for s in steps}
    uncovered = [d for d in selected_depts if d not in covered_depts]
    if uncovered:
        errors.append(
            f"以下部门未被分配任何步骤：{uncovered}\n"
            "  调优建议：在 phase_aggregate system prompt 中要求「每个参与部门至少一步」"
        )

    return errors


def check_traffic_plan_steps(steps: list[dict]) -> list[str]:
    """
    交通事故场景专项校验：
    - 步骤数 2-5（小规模事故）
    - 仅 traffic_control / medical_ems 两部门
    - medical_ems 至少有 1 步（送医）
    - traffic_control 至少有 1 步（管控/清障）
    """
    errors = check_plan_steps(steps, TRAFFIC_DEPTS, min_steps=2, max_steps=6)

    tc_steps = [s for s in steps if s.get("dept_code") == "traffic_control"]
    if not tc_steps:
        errors.append(
            "交通事故计划缺少 traffic_control 步骤（需包含路口管控或清障）\n"
            "  调优建议：在 phase_aggregate system prompt 中说明「交通事故必须含交通管控步骤」"
        )

    me_steps = [s for s in steps if s.get("dept_code") == "medical_ems"]
    if not me_steps:
        errors.append(
            "交通事故计划缺少 medical_ems 步骤（需包含救护车派遣）\n"
            "  调优建议：在 phase_aggregate system prompt 中说明「有伤者时必须含医疗救援步骤」"
        )

    return errors


def check_hazmat_plan_steps(steps: list[dict]) -> list[str]:
    """
    危化品泄漏场景专项校验：
    - 步骤数 3-8
    - env_agency/fire_brigade/medical_ems/traffic_control 全部覆盖
    - env_agency 步骤应含监测或预警相关词
    - fire_brigade 步骤应含防护/处置词（非普通灭火）
    """
    errors = check_plan_steps(steps, HAZMAT_DEPTS, min_steps=3, max_steps=8)

    env_steps = [s for s in steps if s.get("dept_code") == "env_agency"]
    if env_steps:
        env_keywords = ["监测", "预警", "污染", "浓度", "传感器", "扩散", "隔离"]
        has_env_kw = any(
            any(kw in s.get("task", "") + s.get("title", "") for kw in env_keywords)
            for s in env_steps
        )
        if not has_env_kw:
            errors.append(
                "env_agency 步骤缺少污染监测/预警相关词（监测/预警/污染/浓度/传感器/扩散/隔离）\n"
                "  调优建议：在 phase_aggregate prompt 示例中加入危化品污染圈相关步骤"
            )

    ff_steps = [s for s in steps if s.get("dept_code") == "fire_brigade"]
    if ff_steps:
        hazmat_keywords = ["防护", "洗消", "堵漏", "隔离区", "危化", "化学", "处置", "SCBA", "呼吸器"]
        has_hazmat_kw = any(
            any(kw in s.get("task", "") + s.get("title", "") for kw in hazmat_keywords)
            for s in ff_steps
        )
        if not has_hazmat_kw:
            errors.append(
                "fire_brigade 危化品步骤缺少专项处置词（防护/洗消/堵漏/隔离区）\n"
                "  调优建议：在 phase_aggregate prompt 中说明「危化品场景消防步骤应含防护和处置指令」"
            )

    return errors
