"""
Phase 2 — 危化品泄漏场景各部门数据质量测试（weave_integration）

验证 env_agency / fire_brigade / medical_ems / traffic_control
对危化品泄漏的研判响应：
- status == "completed"
- RAG 有检索结果（citations >= 1）
- MCP 工具白名单命中（get_sensor_readings / get_fire_stations 等）
- map_events 坐标格式合法且在上海浦东范围内
- key_facts 包含数值
"""
from __future__ import annotations

import re

import pytest

from tests.weave.evaluators import (
    HAZMAT_DEPTS,
    HAZMAT_RESEARCH_MCP_WHITELIST,
    check_key_facts_have_numbers,
    check_map_event_coordinates,
    check_mcp_whitelist,
)

pytestmark = pytest.mark.weave_integration


class TestHazmatDeptResponseStatus:
    @pytest.mark.parametrize("dept_code", HAZMAT_DEPTS)
    def test_status_completed(self, hazmat_dept_responses, dept_code):
        resp = hazmat_dept_responses[dept_code]
        status = resp.get("status")
        assert status == "completed", (
            f"[{dept_code}] status={status!r}，期望 'completed'\n"
            f"  summary: {resp.get('summary', '')[:200]}\n"
            f"  调优建议：查看 A2A Server 日志，检查 LLM 调用是否超时"
        )


class TestHazmatDeptRagCitations:
    @pytest.mark.parametrize("dept_code", HAZMAT_DEPTS)
    def test_has_rag_citations(self, hazmat_dept_responses, dept_code):
        resp = hazmat_dept_responses[dept_code]
        citations = resp.get("citations", [])
        assert len(citations) >= 1, (
            f"[{dept_code}] citations 为空，RAG 未检索到危化品相关内容\n"
            f"  调优建议：检查该部门 KB 是否已索引危化品处置文档"
        )


class TestHazmatDeptMcpSources:
    @pytest.mark.parametrize("dept_code", HAZMAT_DEPTS)
    def test_has_mcp_sources(self, hazmat_dept_responses, dept_code):
        resp = hazmat_dept_responses[dept_code]
        mcp_sources = resp.get("mcp_sources", [])
        assert len(mcp_sources) >= 1, (
            f"[{dept_code}] mcp_sources 为空，Analyst 未调用任何 MCP 工具\n"
            f"  调优建议：在 dept_prompts.analyst_context 中添加「必须调用 MCP 工具获取实时数据」强制约束"
        )

    @pytest.mark.parametrize("dept_code", HAZMAT_DEPTS)
    def test_mcp_whitelist(self, hazmat_dept_responses, dept_code):
        resp = hazmat_dept_responses[dept_code]
        mcp_sources = resp.get("mcp_sources", [])
        ok, msg = check_mcp_whitelist(dept_code, mcp_sources, HAZMAT_RESEARCH_MCP_WHITELIST)
        assert ok, msg


class TestHazmatDeptMapEvents:
    @pytest.mark.parametrize("dept_code", HAZMAT_DEPTS)
    def test_map_event_coordinates_valid(self, hazmat_dept_responses, dept_code):
        resp = hazmat_dept_responses[dept_code]
        events = resp.get("map_events", [])
        ok, msg = check_map_event_coordinates(events)
        assert ok, msg


class TestHazmatDeptKeyFacts:
    @pytest.mark.parametrize("dept_code", HAZMAT_DEPTS)
    def test_key_facts_have_numbers(self, hazmat_dept_responses, dept_code):
        resp = hazmat_dept_responses[dept_code]
        ok, msg = check_key_facts_have_numbers(resp.get("key_facts", []))
        assert ok, msg


class TestEnvAgencyHazmatDataQuality:
    """验证环保局针对危化品场景的 MCP 数据质量。"""

    def test_uses_sensor_tool(self, hazmat_dept_responses):
        """env_agency 应调用传感器工具获取泄漏浓度。"""
        resp = hazmat_dept_responses["env_agency"]
        mcp_sources = resp.get("mcp_sources", [])
        found_tools = {s.get("tool_name", "") for s in mcp_sources}
        sensor_tools = {"get_sensor_readings", "get_critical_alarms"}
        hit = any(any(req in t for t in found_tools) for req in sensor_tools)
        assert hit, (
            f"env_agency 未调用传感器工具（期望含：{sensor_tools}）\n"
            f"  实际调用：{list(found_tools) or '（无）'}\n"
            f"  调优建议：在 env_agency analyst_context 中强调「危化品泄漏必须调用 get_sensor_readings 获取浓度」"
        )

    def test_key_facts_mention_concentration_or_range(self, hazmat_dept_responses):
        """env_agency key_facts 应含浓度/扩散半径等污染监测数据。"""
        resp = hazmat_dept_responses["env_agency"]
        key_facts = resp.get("key_facts", [])
        if not key_facts:
            pytest.skip("key_facts 为空，由 TestHazmatDeptKeyFacts 覆盖")
        hazmat_env_pattern = re.compile(r"浓度|ppm|mg|扩散|半径|传感器|污染|预警|AQI|PM|气体|泄漏")
        has_env = any(hazmat_env_pattern.search(fact) for fact in key_facts)
        assert has_env, (
            f"env_agency key_facts 缺少浓度/扩散相关数据\n"
            f"  实际：{key_facts}\n"
            f"  调优建议：在 analyst_context 中要求「输出当前传感器浓度读数和扩散预警级别」"
        )


class TestFireBrigadeHazmatDataQuality:
    """验证消防部门针对危化品场景的 MCP 数据质量。"""

    def test_uses_fire_station_tool(self, hazmat_dept_responses):
        """fire_brigade 应调用消防站工具查询有防化能力的力量。"""
        resp = hazmat_dept_responses["fire_brigade"]
        mcp_sources = resp.get("mcp_sources", [])
        found_tools = {s.get("tool_name", "") for s in mcp_sources}
        hit = any("fire_station" in t or "water_supply" in t for t in found_tools)
        assert hit, (
            f"fire_brigade 未调用消防站工具\n"
            f"  实际调用：{list(found_tools) or '（无）'}\n"
            f"  调优建议：在 fire_brigade analyst_context 中要求「危化品场景必须查询有防化能力的消防站」"
        )

    def test_key_facts_mention_hazmat_response(self, hazmat_dept_responses):
        """fire_brigade key_facts 应含防护/处置/防化等危化品专项词。"""
        resp = hazmat_dept_responses["fire_brigade"]
        key_facts = resp.get("key_facts", [])
        if not key_facts:
            pytest.skip("key_facts 为空，由 TestHazmatDeptKeyFacts 覆盖")
        hazmat_ff_pattern = re.compile(r"防护|防化|洗消|堵漏|SCBA|呼吸器|隔离|危化|化学|处置|泄漏")
        has_hazmat = any(hazmat_ff_pattern.search(fact) for fact in key_facts)
        assert has_hazmat, (
            f"fire_brigade key_facts 缺少危化品处置相关词\n"
            f"  实际：{key_facts}\n"
            f"  调优建议：在 analyst_context 中要求「危化品场景输出防化等级和所需防护装备」"
        )


class TestHazmatMapEventCoordinateRange:
    """验证危化品泄漏场景地图事件坐标在上海浦东合理范围内。"""

    LNG_MIN, LNG_MAX = 121.3, 121.8
    LAT_MIN, LAT_MAX = 31.0, 31.5

    @pytest.mark.parametrize("dept_code", HAZMAT_DEPTS)
    def test_map_events_in_shanghai_range(self, hazmat_dept_responses, dept_code):
        resp = hazmat_dept_responses[dept_code]
        events = resp.get("map_events", [])
        for i, ev in enumerate(events):
            center = ev.get("center")
            if not center or len(center) != 2:
                continue
            lng, lat = center
            assert self.LNG_MIN <= lng <= self.LNG_MAX, (
                f"[{dept_code}] map_events[{i}].center[0]（lng={lng}）超出上海浦东范围\n"
                f"  期望：{self.LNG_MIN} ≤ lng ≤ {self.LNG_MAX}"
            )
            assert self.LAT_MIN <= lat <= self.LAT_MAX, (
                f"[{dept_code}] map_events[{i}].center[1]（lat={lat}）超出上海浦东范围\n"
                f"  期望：{self.LAT_MIN} ≤ lat ≤ {self.LAT_MAX}"
            )
