"""
Phase 2 — 交通事故场景各部门数据质量测试（weave_integration）

验证 traffic_control 和 medical_ems 对交通事故的研判响应：
- status == "completed"
- RAG 有检索结果（citations >= 1）
- MCP 工具白名单命中（list_intersections / list_ambulances 等）
- map_events 坐标格式合法且在上海浦东范围内
- key_facts 包含数值
"""
from __future__ import annotations

import pytest

from tests.weave.evaluators import (
    TRAFFIC_DEPTS,
    TRAFFIC_RESEARCH_MCP_WHITELIST,
    check_key_facts_have_numbers,
    check_map_event_coordinates,
    check_mcp_whitelist,
)

pytestmark = pytest.mark.weave_integration


class TestTrafficDeptResponseStatus:
    @pytest.mark.parametrize("dept_code", TRAFFIC_DEPTS)
    def test_status_completed(self, traffic_dept_responses, dept_code):
        resp = traffic_dept_responses[dept_code]
        status = resp.get("status")
        assert status == "completed", (
            f"[{dept_code}] status={status!r}，期望 'completed'\n"
            f"  summary: {resp.get('summary', '')[:200]}\n"
            f"  调优建议：查看 A2A Server 日志，检查 LLM 调用是否超时"
        )


class TestTrafficDeptRagCitations:
    @pytest.mark.parametrize("dept_code", TRAFFIC_DEPTS)
    def test_has_rag_citations(self, traffic_dept_responses, dept_code):
        resp = traffic_dept_responses[dept_code]
        citations = resp.get("citations", [])
        assert len(citations) >= 1, (
            f"[{dept_code}] citations 为空，RAG 未检索到相关内容\n"
            f"  调优建议：检查该部门 KB 是否已索引交通事故相关文档"
        )


class TestTrafficDeptMcpSources:
    @pytest.mark.parametrize("dept_code", TRAFFIC_DEPTS)
    def test_has_mcp_sources(self, traffic_dept_responses, dept_code):
        resp = traffic_dept_responses[dept_code]
        mcp_sources = resp.get("mcp_sources", [])
        assert len(mcp_sources) >= 1, (
            f"[{dept_code}] mcp_sources 为空，Analyst 未调用任何 MCP 工具\n"
            f"  调优建议：在 dept_prompts.analyst_context 中添加「必须调用 MCP 工具获取实时数据」强制约束"
        )

    @pytest.mark.parametrize("dept_code", TRAFFIC_DEPTS)
    def test_mcp_whitelist(self, traffic_dept_responses, dept_code):
        resp = traffic_dept_responses[dept_code]
        mcp_sources = resp.get("mcp_sources", [])
        ok, msg = check_mcp_whitelist(dept_code, mcp_sources, TRAFFIC_RESEARCH_MCP_WHITELIST)
        assert ok, msg


class TestTrafficDeptMapEvents:
    @pytest.mark.parametrize("dept_code", TRAFFIC_DEPTS)
    def test_map_event_coordinates_valid(self, traffic_dept_responses, dept_code):
        resp = traffic_dept_responses[dept_code]
        events = resp.get("map_events", [])
        ok, msg = check_map_event_coordinates(events)
        assert ok, msg


class TestTrafficDeptKeyFacts:
    @pytest.mark.parametrize("dept_code", TRAFFIC_DEPTS)
    def test_key_facts_have_numbers(self, traffic_dept_responses, dept_code):
        resp = traffic_dept_responses[dept_code]
        ok, msg = check_key_facts_have_numbers(resp.get("key_facts", []))
        assert ok, msg


class TestTrafficControlDataQuality:
    """验证交通管控部门针对事故场景的 MCP 数据质量。"""

    def test_uses_intersection_tool(self, traffic_dept_responses):
        """traffic_control 应调用 list_intersections 查询路口状态。"""
        resp = traffic_dept_responses["traffic_control"]
        mcp_sources = resp.get("mcp_sources", [])
        found_tools = {s.get("tool_name", "") for s in mcp_sources}
        hit = any("intersection" in t for t in found_tools)
        assert hit, (
            f"traffic_control 未调用路口工具（含 'intersection'）\n"
            f"  实际调用：{list(found_tools) or '（无）'}\n"
            f"  调优建议：在 traffic_control analyst_context 中强调「交通事故必须调用 list_intersections 获取事发路口状态」"
        )

    def test_key_facts_mention_road_or_intersection(self, traffic_dept_responses):
        """traffic_control key_facts 应含路口/车道/拥堵等交通相关词。"""
        import re
        resp = traffic_dept_responses["traffic_control"]
        key_facts = resp.get("key_facts", [])
        if not key_facts:
            pytest.skip("key_facts 为空，由 TestTrafficDeptKeyFacts 覆盖")
        road_pattern = re.compile(r"路口|车道|拥堵|封路|管控|信号|绕行|限行|清障|追尾")
        has_traffic = any(road_pattern.search(fact) for fact in key_facts)
        assert has_traffic, (
            f"traffic_control key_facts 缺少交通事故相关词\n"
            f"  实际：{key_facts}\n"
            f"  调优建议：在 analyst_context 中要求「输出事发路口拥堵状态和管控方案」"
        )


class TestMedicalEmsTrafficDataQuality:
    """验证医疗急救部门针对交通事故场景的 MCP 数据质量。"""

    def test_uses_ambulance_or_hospital_tool(self, traffic_dept_responses):
        """medical_ems 应调用救护车或医院容量工具。"""
        resp = traffic_dept_responses["medical_ems"]
        mcp_sources = resp.get("mcp_sources", [])
        found_tools = {s.get("tool_name", "") for s in mcp_sources}
        ems_tools = {"list_ambulances", "get_hospital_capacity"}
        hit = any(any(req in t for t in found_tools) for req in ems_tools)
        assert hit, (
            f"medical_ems 未调用急救工具（期望含：{ems_tools}）\n"
            f"  实际调用：{list(found_tools) or '（无）'}\n"
            f"  调优建议：在 medical_ems analyst_context 中强调「有伤者时必须调用 list_ambulances 查询待命救护车」"
        )

    def test_key_facts_mention_ambulance_or_hospital(self, traffic_dept_responses):
        """medical_ems key_facts 应含救护车/医院/伤者相关词。"""
        import re
        resp = traffic_dept_responses["medical_ems"]
        key_facts = resp.get("key_facts", [])
        if not key_facts:
            pytest.skip("key_facts 为空，由 TestTrafficDeptKeyFacts 覆盖")
        ems_pattern = re.compile(r"救护车|急救|医院|伤者|伤亡|ICU|床位|送医|车辆|待命")
        has_ems = any(ems_pattern.search(fact) for fact in key_facts)
        assert has_ems, (
            f"medical_ems key_facts 缺少急救相关词\n"
            f"  实际：{key_facts}\n"
            f"  调优建议：在 analyst_context 中要求「输出可派遣救护车数量和最近医院ICU床位」"
        )


class TestTrafficMapEventCoordinateRange:
    """验证交通事故场景地图事件坐标在上海浦东合理范围内。"""

    LNG_MIN, LNG_MAX = 121.3, 121.8
    LAT_MIN, LAT_MAX = 31.0, 31.5

    @pytest.mark.parametrize("dept_code", TRAFFIC_DEPTS)
    def test_map_events_in_shanghai_range(self, traffic_dept_responses, dept_code):
        resp = traffic_dept_responses[dept_code]
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
