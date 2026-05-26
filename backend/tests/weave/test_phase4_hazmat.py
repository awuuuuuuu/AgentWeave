"""
Phase 4 — 危化品泄漏场景执行阶段测试
（env_agency + fire_brigade + medical_ems + traffic_control）

使用 conftest.hazmat_execution_results fixture（依赖 hazmat_weave_plan）。
"""
from __future__ import annotations

import pytest

from tests.weave.evaluators import (
    EXECUTION_MCP_WHITELIST,
    check_map_event_coordinates,
    check_mcp_whitelist,
)

pytestmark = pytest.mark.weave_integration


class TestHazmatExecutionStatus:
    def test_at_least_one_step_executed(self, hazmat_execution_results):
        assert hazmat_execution_results, (
            "危化品执行阶段无任何步骤被执行\n"
            "  可能原因：hazmat_weave_plan 中 fire_brigade/medical_ems/traffic_control"
            " 步骤无写操作标记（is_high_risk=False）\n"
            "  调试建议：打印 hazmat_weave_plan 各步骤的 is_high_risk 字段"
        )

    def test_all_executed_steps_completed(self, hazmat_execution_results):
        failures = [
            f"  {sid} ({step['dept_code']}): status={resp.get('status')}\n"
            f"    summary: {resp.get('summary', '')[:100]}"
            for sid, (step, resp) in hazmat_execution_results.items()
            if resp.get("status") != "completed"
        ]
        assert not failures, "以下危化品步骤执行失败：\n" + "\n".join(failures)


class TestHazmatMcpWriteOps:
    def test_write_ops_mcp_called(self, hazmat_execution_results):
        """env_agency 无写操作要求（空白名单），其余部门需命中对应工具。"""
        failures = []
        for step_id, (step, resp) in hazmat_execution_results.items():
            dept = step["dept_code"]
            ok, msg = check_mcp_whitelist(dept, resp.get("mcp_sources", []), EXECUTION_MCP_WHITELIST)
            if not ok:
                failures.append(f"  {step_id}: {msg}")
        assert not failures, "以下危化品步骤缺少写操作 MCP 调用：\n" + "\n".join(failures)


class TestHazmatMapData:
    def test_map_event_coordinates_valid(self, hazmat_execution_results):
        failures = []
        for step_id, (_, resp) in hazmat_execution_results.items():
            ok, msg = check_map_event_coordinates(resp.get("map_events", []))
            if not ok:
                failures.append(f"  {step_id}: {msg}")
        assert not failures, "\n".join(failures)


class TestHazmatFireBrigade:
    def test_dispatch_fire_trucks_returns_from_coords(self, hazmat_execution_results):
        """危化品场景 dispatch_fire_trucks 应返回消防站出发坐标（from_lat/from_lng）。"""
        for step_id, (step, resp) in hazmat_execution_results.items():
            if step.get("dept_code") != "fire_brigade":
                continue
            for src in resp.get("mcp_sources", []):
                if "dispatch_fire_trucks" not in src.get("tool_name", ""):
                    continue
                result_str = src.get("key_result", "")
                assert "from_lat" in result_str or "from_lng" in result_str, (
                    f"{step_id}: dispatch_fire_trucks 结果缺少消防站出发坐标\n"
                    f"  实际结果摘要：{result_str[:200]}\n"
                    f"  调优建议：检查 fire_station MCP Server dispatch_fire_trucks 返回格式"
                )


class TestHazmatMedicalEms:
    def test_dispatch_ambulance_returns_from_coords(self, hazmat_execution_results):
        """危化品伤员送医：dispatch_ambulance 应返回 from_lat/from_lng。"""
        for step_id, (step, resp) in hazmat_execution_results.items():
            if step.get("dept_code") != "medical_ems":
                continue
            for src in resp.get("mcp_sources", []):
                if "dispatch_ambulance" not in src.get("tool_name", ""):
                    continue
                result_str = src.get("key_result", "")
                assert "from_lat" in result_str or "from_lng" in result_str, (
                    f"{step_id}: dispatch_ambulance 结果缺少出发坐标\n"
                    f"  实际结果摘要：{result_str[:200]}"
                )


class TestHazmatEnvAgency:
    def test_env_agency_steps_completed(self, hazmat_execution_results):
        """env_agency 危化品监测步骤（只读）应完成，无写操作要求。"""
        for step_id, (step, resp) in hazmat_execution_results.items():
            if step.get("dept_code") != "env_agency":
                continue
            assert resp.get("status") == "completed", (
                f"{step_id}: env_agency 步骤状态非 completed: {resp.get('status')}\n"
                f"  调优建议：检查环保局 A2A Server 日志"
            )
