"""
Phase 2 — 各部门数据质量测试（weave_integration）

验证每个部门 A2A Server 的研判响应：
- status == "completed"
- RAG 有检索结果（citations >= 1）
- MCP 工具白名单命中
- map_events 坐标格式合法
- key_facts 包含数值
"""
from __future__ import annotations

import pytest

from tests.weave.evaluators import (
    RESEARCH_MCP_WHITELIST,
    SELECTED_DEPTS,
    check_key_facts_have_numbers,
    check_map_event_coordinates,
    check_mcp_whitelist,
)

pytestmark = pytest.mark.weave_integration


class TestDeptResponseStatus:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_status_completed(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        status = resp.get("status")
        assert status == "completed", (
            f"[{dept_code}] status={status!r}，期望 'completed'\n"
            f"  summary: {resp.get('summary', '')[:200]}\n"
            f"  调优建议：查看 A2A Server 日志，检查 LLM 调用是否超时"
        )


class TestDeptRagCitations:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_has_rag_citations(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        citations = resp.get("citations", [])
        assert len(citations) >= 1, (
            f"[{dept_code}] citations 为空，RAG 未检索到相关内容\n"
            f"  调优建议：检查该部门 KB 是否已索引相关文档"
        )


class TestDeptMcpSources:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_has_mcp_sources(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        mcp_sources = resp.get("mcp_sources", [])
        assert len(mcp_sources) >= 1, (
            f"[{dept_code}] mcp_sources 为空，Analyst 未调用任何 MCP 工具\n"
            f"  调优建议：在 dept_prompts.analyst_context 中添加"
            f"「必须调用 MCP 工具获取实时数据」强制约束"
        )

    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_mcp_whitelist(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        mcp_sources = resp.get("mcp_sources", [])
        ok, msg = check_mcp_whitelist(dept_code, mcp_sources, RESEARCH_MCP_WHITELIST)
        assert ok, msg


class TestDeptMapEvents:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_map_event_coordinates_valid(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        events = resp.get("map_events", [])
        ok, msg = check_map_event_coordinates(events)
        assert ok, msg


class TestDeptKeyFacts:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_key_facts_have_numbers(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        ok, msg = check_key_facts_have_numbers(resp.get("key_facts", []))
        assert ok, msg


class TestFireBrigadeDataQuality:
    """验证消防救援部门 MCP 数据质量。"""

    def test_fire_brigade_uses_fire_station_tools(self, dept_responses):
        """fire_brigade 研判阶段应调用消防站相关工具。"""
        resp = dept_responses["fire_brigade"]
        mcp_sources = resp.get("mcp_sources", [])
        fire_tools = {"get_fire_stations", "get_water_supplies"}
        found_tools = {s.get("tool_name", "") for s in mcp_sources}
        hit = any(t in found_tools for t in fire_tools)
        assert hit, (
            f"fire_brigade 未调用消防站工具（期望含：{fire_tools}）\n"
            f"  实际调用：{list(found_tools) or '（无）'}\n"
            f"  调优建议：在 fire_brigade analyst_context 中强调「必须调用 get_fire_stations 查询附近消防力量」"
        )

    def test_fire_brigade_key_facts_mention_trucks_or_distance(self, dept_responses):
        """fire_brigade key_facts 应包含消防车数量或距离信息。"""
        import re
        resp = dept_responses["fire_brigade"]
        key_facts = resp.get("key_facts", [])
        if not key_facts:
            pytest.skip("key_facts 为空，由 TestDeptKeyFacts 覆盖")
        # 期望包含：辆、km、分钟、消防车、消防站
        truck_pattern = re.compile(r"辆|千米|km|分钟|消防车|消防站|FS\d")
        has_specific = any(truck_pattern.search(fact) for fact in key_facts)
        assert has_specific, (
            f"fire_brigade key_facts 缺少消防力量具体数据（辆数/距离）\n"
            f"  实际：{key_facts}\n"
            f"  调优建议：在 analyst_context 中要求「输出最近消防站可用车辆数和距离」"
        )


class TestMapEventCoordinateRange:
    """验证各部门地图事件坐标在上海浦东合理范围内。"""

    # 上海浦东新区大致范围（含缓冲）
    LNG_MIN, LNG_MAX = 121.3, 121.8
    LAT_MIN, LAT_MAX = 31.0, 31.5

    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_map_events_in_shanghai_range(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        events = resp.get("map_events", [])
        for i, ev in enumerate(events):
            center = ev.get("center")
            if not center or len(center) != 2:
                continue  # 格式错误由 TestDeptMapEvents 覆盖
            lng, lat = center
            assert self.LNG_MIN <= lng <= self.LNG_MAX, (
                f"[{dept_code}] map_events[{i}].center[0]（lng={lng}）超出上海浦东范围\n"
                f"  期望：{self.LNG_MIN} ≤ lng ≤ {self.LNG_MAX}\n"
                f"  调优建议：检查 MCP Server 坐标是否使用上海数据"
            )
            assert self.LAT_MIN <= lat <= self.LAT_MAX, (
                f"[{dept_code}] map_events[{i}].center[1]（lat={lat}）超出上海浦东范围\n"
                f"  期望：{self.LAT_MIN} ≤ lat ≤ {self.LAT_MAX}\n"
                f"  调优建议：检查 MCP Server 坐标是否使用上海数据"
            )
