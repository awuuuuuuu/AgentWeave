"""
Phase 4 — 交通事故场景执行阶段测试（traffic_control + medical_ems）

使用 conftest.traffic_execution_results fixture（依赖 traffic_weave_plan）。
"""
from __future__ import annotations

import pytest

from tests.weave.evaluators import (
    EXECUTION_MCP_WHITELIST,
    check_map_event_coordinates,
    check_mcp_whitelist,
)

pytestmark = pytest.mark.weave_integration


class TestTrafficAccidentExecutionStatus:
    def test_at_least_one_step_executed(self, traffic_execution_results):
        assert traffic_execution_results, (
            "交通事故执行阶段无任何步骤被执行\n"
            "  可能原因：weave_plan 中 traffic_control/medical_ems 步骤无写操作标记\n"
            "  调试建议：打印 traffic_weave_plan 各步骤的 is_high_risk 字段"
        )

    def test_all_executed_steps_completed(self, traffic_execution_results):
        failures = [
            f"  {sid} ({step['dept_code']}): status={resp.get('status')}\n"
            f"    summary: {resp.get('summary', '')[:100]}"
            for sid, (step, resp) in traffic_execution_results.items()
            if resp.get("status") != "completed"
        ]
        assert not failures, "以下交通事故步骤执行失败：\n" + "\n".join(failures)


class TestTrafficAccidentMcpWriteOps:
    def test_write_ops_mcp_called(self, traffic_execution_results):
        failures = []
        for step_id, (step, resp) in traffic_execution_results.items():
            dept = step["dept_code"]
            ok, msg = check_mcp_whitelist(dept, resp.get("mcp_sources", []), EXECUTION_MCP_WHITELIST)
            if not ok:
                failures.append(f"  {step_id}: {msg}")
        assert not failures, "以下交通事故步骤缺少写操作 MCP 调用：\n" + "\n".join(failures)


class TestTrafficAccidentMapData:
    def test_map_event_coordinates_valid(self, traffic_execution_results):
        failures = []
        for step_id, (_, resp) in traffic_execution_results.items():
            ok, msg = check_map_event_coordinates(resp.get("map_events", []))
            if not ok:
                failures.append(f"  {step_id}: {msg}")
        assert not failures, "\n".join(failures)


class TestTrafficAccidentMedicalEms:
    def test_dispatch_ambulance_returns_from_coords(self, traffic_execution_results):
        """dispatch_ambulance 应返回 from_lat/from_lng（供地图标注出发位置）。"""
        for step_id, (step, resp) in traffic_execution_results.items():
            if step.get("dept_code") != "medical_ems":
                continue
            for src in resp.get("mcp_sources", []):
                if "dispatch_ambulance" not in src.get("tool_name", ""):
                    continue
                result_str = src.get("key_result", "")
                assert "from_lat" in result_str or "from_lng" in result_str, (
                    f"{step_id}: dispatch_ambulance 结果缺少出发坐标（from_lat/from_lng）\n"
                    f"  实际结果摘要：{result_str[:200]}"
                )


class TestTrafficAccidentSignalControl:
    def test_signal_tools_return_mode_field(self, traffic_execution_results):
        """set_mode / apply_evacuation_plan 结果应包含 mode 字段。"""
        SIGNAL_TOOLS = {"set_mode", "apply_evacuation_plan"}
        for step_id, (step, resp) in traffic_execution_results.items():
            if step.get("dept_code") != "traffic_control":
                continue
            for src in resp.get("mcp_sources", []):
                tool = src.get("tool_name", "")
                if not any(t in tool for t in SIGNAL_TOOLS):
                    continue
                assert "mode" in src.get("key_result", ""), (
                    f"{step_id}: 信号控制工具 {tool!r} 结果缺少 mode 字段\n"
                    f"  实际结果：{src.get('key_result', '')[:200]}"
                )
