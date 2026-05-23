"""
Phase 3 — 执行计划合理性测试（weave_integration）

验证 phase_aggregate 生成的执行计划：
- 步骤数在 3-8 之间
- step_id 无重复
- 所有 dept_code 合法
- 至少 1 个高危步骤
- title 包含具体数量或地点
- 所有选定部门都被覆盖
"""
from __future__ import annotations

import pytest

from tests.weave.evaluators import SELECTED_DEPTS, check_plan_steps

pytestmark = pytest.mark.weave_integration


class TestPlanStructure:
    def test_plan_not_empty(self, weave_plan):
        assert weave_plan, (
            "执行计划为空\n"
            "  调优建议：检查 phase_aggregate 的 LLM 调用是否成功，"
            "查看是否触发了 _default_plan 兜底"
        )

    def test_all_rules_pass(self, weave_plan):
        errors = check_plan_steps(weave_plan, SELECTED_DEPTS)
        assert not errors, (
            f"执行计划校验发现 {len(errors)} 个问题：\n\n"
            + "\n\n".join(f"[{i+1}] {e}" for i, e in enumerate(errors))
        )


class TestPlanStepDetails:
    def test_each_step_has_task_description(self, weave_plan):
        empty_tasks = [
            s["step_id"] for s in weave_plan if len(s.get("task", "")) < 10
        ]
        assert not empty_tasks, (
            f"以下步骤的 task 字段过短（< 10 字）: {empty_tasks}\n"
            f"  调优建议：在 phase_aggregate system prompt 中要求"
            f"「task 字段需包含 2-4 句具体执行指令」"
        )

    def test_high_risk_steps_have_map_layer_or_reason(self, weave_plan):
        """高危步骤通常应有地图图层，记录缺失供人工审查（不强制 fail）。"""
        high_risk_no_layer = [
            s["step_id"] for s in weave_plan
            if s.get("is_high_risk") and not s.get("map_layer")
        ]
        if high_risk_no_layer:
            print(f"\n[INFO] 高危步骤但无 map_layer，请人工确认是否正常: {high_risk_no_layer}")
