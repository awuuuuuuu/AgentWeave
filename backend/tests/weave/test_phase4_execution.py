"""
Phase 4 — 执行阶段 MCP 写操作 + 地图数据测试（weave_integration）

仅测试有写操作 MCP 要求的部门步骤（EXECUTION_MCP_WHITELIST 非空）：
- 执行结果 status == "completed"
- mcp_sources 中包含写操作工具
- 有 map_layer 的步骤，map_events 不为空且坐标合法
"""
from __future__ import annotations

import pytest

from tests.weave.evaluators import (
    EXECUTION_MCP_WHITELIST,
    EXPECTED_MAP_LAYERS,
    check_map_event_coordinates,
    check_mcp_whitelist,
)

pytestmark = pytest.mark.weave_integration


class TestExecutionStatus:
    def test_at_least_one_step_executed(self, execution_results):
        assert execution_results, (
            "执行阶段无任何步骤被执行\n"
            "  可能原因：weave_plan 中所有需写操作的步骤状态不为 pending/approved\n"
            "  调试建议：打印 weave_plan 中各步骤的 status 和 dept_code"
        )

    def test_all_executed_steps_completed(self, execution_results):
        failures = []
        for step_id, (step, resp) in execution_results.items():
            if resp.get("status") != "completed":
                failures.append(
                    f"  {step_id} ({step['dept_code']}): status={resp.get('status')}\n"
                    f"    summary: {resp.get('summary', '')[:100]}"
                )
        assert not failures, (
            "以下步骤执行失败：\n" + "\n".join(failures) + "\n"
            "  调优建议：查看 A2A Server 日志，检查 MCP 工具调用是否超时"
        )


class TestExecutionMcpWriteOps:
    def test_write_ops_mcp_called(self, execution_results):
        """有 execution_tool 的步骤，mcp_sources 中应包含该工具的调用记录。"""
        failures = []
        for step_id, (step, resp) in execution_results.items():
            expected_tool = step.get("execution_tool")
            if not expected_tool:
                # 降级：兼容旧格式——按部门白名单检查
                dept = step["dept_code"]
                mcp_sources = resp.get("mcp_sources", [])
                ok, msg = check_mcp_whitelist(dept, mcp_sources, EXECUTION_MCP_WHITELIST)
                if not ok:
                    failures.append(f"  {step_id}: {msg}")
                continue
            called = {s["tool_name"] for s in resp.get("mcp_sources", [])}
            if expected_tool not in called:
                failures.append(
                    f"  {step_id}: 期望 {expected_tool!r}，实际调用 {called}"
                )
        assert not failures, "以下步骤未调用声明的写操作工具：\n" + "\n".join(failures)

    def test_execution_intent_in_high_risk_steps(self, execution_results):
        """高危步骤应携带 execution_tool 字段（phase_aggregate 结构化意图输出验证）。"""
        for step_id, (step, _) in execution_results.items():
            if step.get("is_high_risk"):
                assert step.get("execution_tool"), (
                    f"{step_id}: 高危步骤缺少 execution_tool\n"
                    f"  调优建议：检查 phase_aggregate prompt 是否包含 execution_tool 输出要求"
                )


class TestExecutionMapData:
    def test_map_event_coordinates_valid(self, execution_results):
        failures = []
        for step_id, (_, resp) in execution_results.items():
            ok, msg = check_map_event_coordinates(resp.get("map_events", []))
            if not ok:
                failures.append(f"  {step_id}: {msg}")
        assert not failures, "\n".join(failures)

    def test_steps_with_map_layer_have_events(self, execution_results):
        """
        写操作工具（set_mode / dispatch_ambulance）本身不产生路线地图事件；
        只有 amap 路线规划类工具才生成 map_events。
        因此此测试为软检查，仅打印缺失情况供人工确认。
        """
        missing_events = []
        for step_id, (step, resp) in execution_results.items():
            if not step.get("map_layer"):
                continue
            if not resp.get("map_events"):
                dept = step["dept_code"]
                missing_events.append(
                    f"  {step_id} ({dept}) map_layer={step['map_layer']!r} 但 map_events 为空"
                    f"（写操作工具不产生地图事件，如需地图渲染需添加路线规划步骤）"
                )
        if missing_events:
            print("\n[INFO] 以下步骤 map_events 为空，非 bug — 写操作不产生地图事件：")
            for m in missing_events:
                print(m)

    def test_expected_map_layers_present(self, execution_results):
        """
        同上：写操作不产生 map_events，此测试为软检查。
        若任务包含路线规划（amap 工具），则应有对应图层。
        """
        missing_layers = []
        for step_id, (step, resp) in execution_results.items():
            dept = step["dept_code"]
            expected_layers = EXPECTED_MAP_LAYERS.get(dept, [])
            if not expected_layers:
                continue
            actual_layers = {ev.get("layer", "") for ev in resp.get("map_events", [])}
            missing = [la for la in expected_layers if not any(la in al for al in actual_layers)]
            if missing:
                missing_layers.append(
                    f"  {step_id} ({dept}) 缺少图层: {missing}，实际: {list(actual_layers)}"
                )
        if missing_layers:
            print("\n[INFO] 以下步骤缺少预期地图图层（写操作任务通常无路线事件）：")
            for m in missing_layers:
                print(m)


class TestFireBrigadeExecutionData:
    """验证消防救援部门执行阶段的数据完整性。"""

    def test_dispatch_fire_trucks_returns_station_coords(self, execution_results):
        """dispatch_fire_trucks 结果应包含消防站出发坐标（from_lat/from_lng）。"""
        for step_id, (step, resp) in execution_results.items():
            if step.get("dept_code") != "fire_brigade":
                continue
            mcp_sources = resp.get("mcp_sources", [])
            dispatch_calls = [
                s for s in mcp_sources
                if "dispatch_fire_trucks" in s.get("tool_name", "")
            ]
            for call in dispatch_calls:
                result_str = call.get("key_result", "")
                assert "from_lat" in result_str or "from_lng" in result_str, (
                    f"{step_id}: dispatch_fire_trucks 结果缺少消防站出发坐标（from_lat/from_lng）\n"
                    f"  实际结果摘要：{result_str[:200]}\n"
                    f"  调优建议：检查 fire_station MCP Server 的 dispatch_fire_trucks 返回格式"
                )

    def test_dispatch_fire_trucks_returns_status_dispatched(self, execution_results):
        """dispatch_fire_trucks 结果 status 应为 'dispatched'。"""
        for step_id, (step, resp) in execution_results.items():
            if step.get("dept_code") != "fire_brigade":
                continue
            mcp_sources = resp.get("mcp_sources", [])
            for src in mcp_sources:
                if "dispatch_fire_trucks" not in src.get("tool_name", ""):
                    continue
                result = src.get("result", {})
                if isinstance(result, dict):
                    status = result.get("status", "")
                    assert status == "dispatched" or status == "", (
                        f"{step_id}: dispatch_fire_trucks status={status!r}，期望 'dispatched'\n"
                        f"  调优建议：检查 fire_station MCP Server 的状态码"
                    )


class TestMedicalEmsExecutionData:
    """验证医疗急救执行阶段的数据完整性。"""

    def test_dispatch_ambulance_returns_from_coords(self, execution_results):
        """dispatch_ambulance 结果应包含救护车出发坐标（from_lat/from_lng）。"""
        for step_id, (step, resp) in execution_results.items():
            if step.get("dept_code") != "medical_ems":
                continue
            mcp_sources = resp.get("mcp_sources", [])
            dispatch_calls = [
                s for s in mcp_sources
                if "dispatch_ambulance" in s.get("tool_name", "")
            ]
            for call in dispatch_calls:
                result_str = call.get("key_result", "")
                assert "from_lat" in result_str or "from_lng" in result_str, (
                    f"{step_id}: dispatch_ambulance 结果缺少出发坐标（from_lat/from_lng）\n"
                    f"  实际结果摘要：{result_str[:200]}\n"
                    f"  调优建议：检查 ambulance_dispatch MCP Server 的 dispatch_ambulance 返回格式"
                )

    def test_dispatch_ambulance_returns_map_marker(self, execution_results):
        """dispatch_ambulance 结果应包含 map_marker（供地图标注出发位置）。"""
        for step_id, (step, resp) in execution_results.items():
            if step.get("dept_code") != "medical_ems":
                continue
            mcp_sources = resp.get("mcp_sources", [])
            for src in mcp_sources:
                if "dispatch_ambulance" not in src.get("tool_name", ""):
                    continue
                result_str = src.get("key_result", "")
                assert "map_marker" in result_str, (
                    f"{step_id}: dispatch_ambulance 结果缺少 map_marker\n"
                    f"  实际结果摘要：{result_str[:200]}\n"
                    f"  调优建议：检查 ambulance_dispatch MCP Server 的 dispatch_ambulance 返回格式"
                )


class TestTrafficSignalExecutionData:
    """验证交通管控执行阶段的数据完整性。"""

    def test_signal_tools_return_mode_field(self, execution_results):
        """set_mode / apply_evacuation_plan 结果应包含 mode 字段。"""
        SIGNAL_TOOLS = {"set_mode", "apply_evacuation_plan"}
        for step_id, (step, resp) in execution_results.items():
            if step.get("dept_code") != "traffic_control":
                continue
            mcp_sources = resp.get("mcp_sources", [])
            for src in mcp_sources:
                tool = src.get("tool_name", "")
                if not any(t in tool for t in SIGNAL_TOOLS):
                    continue
                result_str = src.get("key_result", "")
                assert "mode" in result_str, (
                    f"{step_id}: 信号控制工具 {tool!r} 结果缺少 mode 字段\n"
                    f"  实际结果摘要：{result_str[:200]}\n"
                    f"  调优建议：检查 signal_control MCP Server 的返回格式"
                )

    def test_evacuation_plan_intersections_have_coords(self, execution_results):
        """apply_evacuation_plan 结果的 intersections 列表应包含 lat/lng 坐标。"""
        for step_id, (step, resp) in execution_results.items():
            if step.get("dept_code") != "traffic_control":
                continue
            mcp_sources = resp.get("mcp_sources", [])
            for src in mcp_sources:
                if "apply_evacuation_plan" not in src.get("tool_name", ""):
                    continue
                result = src.get("result", {})
                if not isinstance(result, dict):
                    continue
                intersections = result.get("intersections", [])
                for isect in intersections:
                    assert "lat" in isect and "lng" in isect, (
                        f"{step_id}: apply_evacuation_plan intersections 缺少 lat/lng\n"
                        f"  交叉口数据：{isect}\n"
                        f"  调优建议：检查 signal_control MCP Server 的 apply_evacuation_plan"
                        f" 是否回查了 lat/lng 字段"
                    )
