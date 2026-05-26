"""evaluators.py 单元测试 — weave_unit，无外部依赖。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.weave_unit


class TestCheckDeptKeywords:
    def test_matching_keyword_passes(self):
        from tests.weave.evaluators import check_dept_keywords
        ok, msg = check_dept_keywords("env_agency", "事故：建筑火灾，传感器显示烟雾浓度超标。")
        assert ok, msg

    def test_no_keyword_fails_with_hint(self):
        from tests.weave.evaluators import check_dept_keywords
        ok, msg = check_dept_keywords("env_agency", "请提供报告。")
        assert not ok
        assert "_build_dept_tasks" in msg

    def test_unknown_dept_passes(self):
        from tests.weave.evaluators import check_dept_keywords
        ok, _ = check_dept_keywords("unknown_dept", "任意文本")
        assert ok


class TestCheckMcpWhitelist:
    def test_required_tool_present_passes(self):
        from tests.weave.evaluators import RESEARCH_MCP_WHITELIST, check_mcp_whitelist
        sources = [{"tool_name": "get_sensor_readings", "idx": 1, "key_result": "x"}]
        ok, msg = check_mcp_whitelist("env_agency", sources, RESEARCH_MCP_WHITELIST)
        assert ok, msg

    def test_missing_tool_fails_with_hint(self):
        from tests.weave.evaluators import RESEARCH_MCP_WHITELIST, check_mcp_whitelist
        ok, msg = check_mcp_whitelist("env_agency", [], RESEARCH_MCP_WHITELIST)
        assert not ok
        assert "analyst_context" in msg

    def test_dept_with_no_required_tools_passes(self):
        from tests.weave.evaluators import EXECUTION_MCP_WHITELIST, check_mcp_whitelist
        ok, _ = check_mcp_whitelist("env_agency", [], EXECUTION_MCP_WHITELIST)
        assert ok

    def test_partial_match_passes(self):
        from tests.weave.evaluators import RESEARCH_MCP_WHITELIST, check_mcp_whitelist
        sources = [{"tool_name": "get_hospital_capacity", "idx": 1, "key_result": "x"}]
        ok, _ = check_mcp_whitelist("medical_ems", sources, RESEARCH_MCP_WHITELIST)
        assert ok


class TestCheckMapEventCoordinates:
    def test_valid_coordinates_passes(self):
        from tests.weave.evaluators import check_map_event_coordinates
        ok, msg = check_map_event_coordinates([{"center": [121.4737, 31.2304], "zoom": 13}])
        assert ok, msg

    def test_missing_center_fails(self):
        from tests.weave.evaluators import check_map_event_coordinates
        ok, msg = check_map_event_coordinates([{"zoom": 13}])
        assert not ok
        assert "center" in msg

    def test_wrong_coord_count_fails(self):
        from tests.weave.evaluators import check_map_event_coordinates
        ok, _ = check_map_event_coordinates([{"center": [121.4737]}])
        assert not ok

    def test_empty_events_passes(self):
        from tests.weave.evaluators import check_map_event_coordinates
        ok, _ = check_map_event_coordinates([])
        assert ok


class TestCheckKeyFactsHaveNumbers:
    def test_fact_with_number_passes(self):
        from tests.weave.evaluators import check_key_facts_have_numbers
        ok, _ = check_key_facts_have_numbers(["扩散半径 500 米", "疏散人员 1200 人"])
        assert ok

    def test_no_numbers_fails(self):
        from tests.weave.evaluators import check_key_facts_have_numbers
        ok, msg = check_key_facts_have_numbers(["请疏散周边居民"])
        assert not ok
        assert "analyst_context" in msg

    def test_empty_list_fails(self):
        from tests.weave.evaluators import check_key_facts_have_numbers
        ok, _ = check_key_facts_have_numbers([])
        assert not ok


class TestCheckPlanSteps:
    def _make_step(self, step_id, dept_code, title, is_high_risk=False):
        return {
            "step_id": step_id, "dept_code": dept_code, "title": title,
            "is_high_risk": is_high_risk, "task": "任务", "status": "pending",
            "map_layer": None, "result_summary": "",
        }

    def _all_depts(self):
        return ["env_agency", "medical_ems", "traffic_control",
                "emergency_supplies", "fire_brigade"]

    def test_valid_plan_returns_no_errors(self):
        from tests.weave.evaluators import check_plan_steps
        steps = [
            self._make_step("step-001", "env_agency",         "世纪大道×陆家嘴环路警戒500米", True),
            self._make_step("step-002", "medical_ems",        "调派3辆救护车前往事故现场"),
            self._make_step("step-003", "traffic_control",    "切换路口信号至疏散模式"),
            self._make_step("step-004", "emergency_supplies", "调拨防护服50套"),
            self._make_step("step-005", "fire_brigade",       "陆家嘴站调派2辆消防车灭火"),
        ]
        errors = check_plan_steps(steps, self._all_depts())
        assert errors == [], f"预期无错误，实际: {errors}"

    def test_too_few_steps_is_error(self):
        from tests.weave.evaluators import check_plan_steps
        steps = [self._make_step("step-001", "env_agency", "疏散")]
        errors = check_plan_steps(steps, ["env_agency"])
        assert any("步骤数" in e for e in errors)

    def test_duplicate_step_id_is_error(self):
        from tests.weave.evaluators import check_plan_steps
        steps = [
            self._make_step("step-001", "env_agency",         "世纪大道×陆家嘴环路警戒500米", True),
            self._make_step("step-001", "medical_ems",        "调派3辆救护车"),
            self._make_step("step-003", "traffic_control",    "切换路口信号"),
            self._make_step("step-004", "emergency_supplies", "调拨防护服50套"),
            self._make_step("step-005", "fire_brigade",       "陆家嘴站调派2辆消防车灭火"),
        ]
        errors = check_plan_steps(steps, self._all_depts())
        assert any("重复" in e for e in errors)

    def test_no_high_risk_step_is_error(self):
        from tests.weave.evaluators import check_plan_steps
        steps = [
            self._make_step("step-001", "env_agency",         "世纪大道×陆家嘴环路警戒500米"),
            self._make_step("step-002", "medical_ems",        "调派3辆救护车前往事故现场"),
            self._make_step("step-003", "traffic_control",    "切换路口信号至疏散模式"),
            self._make_step("step-004", "emergency_supplies", "调拨防护服50套"),
            self._make_step("step-005", "fire_brigade",       "陆家嘴站调派2辆消防车灭火"),
        ]
        errors = check_plan_steps(steps, self._all_depts())
        assert any("高危" in e for e in errors)

    def test_vague_title_is_error(self):
        from tests.weave.evaluators import check_plan_steps
        steps = [
            self._make_step("step-001", "env_agency",         "部署救援", True),
            self._make_step("step-002", "medical_ems",        "调派3辆救护车前往事故现场"),
            self._make_step("step-003", "traffic_control",    "切换路口信号至疏散模式"),
            self._make_step("step-004", "emergency_supplies", "调拨防护服50套"),
            self._make_step("step-005", "fire_brigade",       "陆家嘴站调派2辆消防车灭火"),
        ]
        errors = check_plan_steps(steps, self._all_depts())
        assert any("title" in e for e in errors)


class TestFireBrigadeKeywords:
    """验证 fire_brigade 部门的关键词匹配。"""

    def test_fire_keywords_match(self):
        from tests.weave.evaluators import check_dept_keywords
        ok, msg = check_dept_keywords("fire_brigade", "请查询附近消防站可用消防车数量。")
        assert ok, msg

    def test_fire_mcp_whitelist_research(self):
        from tests.weave.evaluators import RESEARCH_MCP_WHITELIST, check_mcp_whitelist
        sources = [{"tool_name": "get_fire_stations", "idx": 1, "key_result": "FS1 浦东陆家嘴站"}]
        ok, msg = check_mcp_whitelist("fire_brigade", sources, RESEARCH_MCP_WHITELIST)
        assert ok, msg

    def test_fire_mcp_whitelist_execution(self):
        from tests.weave.evaluators import EXECUTION_MCP_WHITELIST, check_mcp_whitelist
        sources = [{"tool_name": "dispatch_fire_trucks", "idx": 1, "key_result": "已调派2辆"}]
        ok, msg = check_mcp_whitelist("fire_brigade", sources, EXECUTION_MCP_WHITELIST)
        assert ok, msg

    def test_water_supply_passes_research(self):
        from tests.weave.evaluators import RESEARCH_MCP_WHITELIST, check_mcp_whitelist
        sources = [{"tool_name": "get_water_supplies", "idx": 1, "key_result": "200吨"}]
        ok, msg = check_mcp_whitelist("fire_brigade", sources, RESEARCH_MCP_WHITELIST)
        assert ok, msg

    def test_recall_passes_execution(self):
        from tests.weave.evaluators import EXECUTION_MCP_WHITELIST, check_mcp_whitelist
        sources = [{"tool_name": "recall_fire_trucks", "idx": 1, "key_result": "已撤回"}]
        ok, msg = check_mcp_whitelist("fire_brigade", sources, EXECUTION_MCP_WHITELIST)
        assert ok, msg
