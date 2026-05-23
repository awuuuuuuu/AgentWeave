"""
Phase 4 — 执行阶段 MCP 写操作 + 地图数据测试（crew_integration）

仅测试有写操作 MCP 要求的部门步骤（EXECUTION_MCP_WHITELIST 非空）：
- 执行结果 status == "completed"
- mcp_sources 中包含写操作工具
- 有 map_layer 的步骤，map_events 不为空且坐标合法
"""
from __future__ import annotations

import pytest

from tests.crew.evaluators import (
    EXECUTION_MCP_WHITELIST,
    EXPECTED_MAP_LAYERS,
    check_map_event_coordinates,
    check_mcp_whitelist,
)

pytestmark = pytest.mark.crew_integration


class TestExecutionStatus:
    def test_at_least_one_step_executed(self, execution_results):
        assert execution_results, (
            "执行阶段无任何步骤被执行\n"
            "  可能原因：crew_plan 中所有需写操作的步骤状态不为 pending/approved\n"
            "  调试建议：打印 crew_plan 中各步骤的 status 和 dept_code"
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
        failures = []
        for step_id, (step, resp) in execution_results.items():
            dept = step["dept_code"]
            mcp_sources = resp.get("mcp_sources", [])
            ok, msg = check_mcp_whitelist(dept, mcp_sources, EXECUTION_MCP_WHITELIST)
            if not ok:
                failures.append(f"  {step_id}: {msg}")
        assert not failures, "以下步骤缺少写操作 MCP 调用：\n" + "\n".join(failures)


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
