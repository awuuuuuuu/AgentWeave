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


class TestFireScenarioPlanContent:
    """验证火灾场景执行计划的内容合理性。"""

    def test_fire_brigade_has_at_least_one_step(self, weave_plan):
        """执行计划中 fire_brigade 至少应有 1 个步骤。"""
        ff_steps = [s for s in weave_plan if s.get("dept_code") == "fire_brigade"]
        assert ff_steps, (
            "执行计划中缺少 fire_brigade 步骤\n"
            "  调优建议：在 phase_aggregate system prompt 中明确「火灾场景必须包含消防调派步骤」"
        )

    def test_fire_brigade_has_high_risk_step(self, weave_plan):
        """fire_brigade 至少有 1 个高危步骤（调派消防车是高危操作）。"""
        ff_steps = [s for s in weave_plan if s.get("dept_code") == "fire_brigade"]
        if not ff_steps:
            pytest.skip("fire_brigade 无步骤，由 test_fire_brigade_has_at_least_one_step 覆盖")
        has_high_risk = any(s.get("is_high_risk") for s in ff_steps)
        assert has_high_risk, (
            "fire_brigade 步骤中无高危步骤，消防调派应标记为高危\n"
            "  步骤列表：" + str([s.get("title", "") for s in ff_steps]) + "\n"
            "  调优建议：在 phase_aggregate system prompt 示例中将「消防车调派」明确标为 is_high_risk=true"
        )

    def test_traffic_control_has_at_least_one_step(self, weave_plan):
        """执行计划中 traffic_control 至少应有 1 个步骤（火灾需疏散通道管控）。"""
        tc_steps = [s for s in weave_plan if s.get("dept_code") == "traffic_control"]
        assert tc_steps, (
            "执行计划中缺少 traffic_control 步骤\n"
            "  调优建议：在 phase_aggregate system prompt 中说明「火灾场景需包含交通管控步骤」"
        )

    def test_env_agency_has_at_least_one_step(self, weave_plan):
        """执行计划中 env_agency 至少应有 1 个步骤（火灾浓烟需环境监测）。"""
        env_steps = [s for s in weave_plan if s.get("dept_code") == "env_agency"]
        assert env_steps, (
            "执行计划中缺少 env_agency 步骤\n"
            "  调优建议：在 phase_aggregate system prompt 中说明「火灾场景需包含环境监测步骤」"
        )


class TestPlanStepOrdering:
    """验证执行计划步骤的逻辑顺序合理性。"""

    def test_step_ids_are_sequential_format(self, weave_plan):
        """step_id 应符合格式 step-XXX（三位数字）。"""
        import re
        bad_ids = [
            s["step_id"] for s in weave_plan
            if not re.match(r"^step-\d{3}$", s.get("step_id", ""))
        ]
        assert not bad_ids, (
            f"以下 step_id 格式不规范（期望 step-XXX）：{bad_ids}\n"
            f"  调优建议：在 phase_aggregate system prompt 中强调「step_id 格式为 step-001, step-002...」"
        )

    def test_not_all_steps_high_risk(self, weave_plan):
        """不应所有步骤都被标为高危（过度标记会降低 HITL 有效性）。"""
        if not weave_plan:
            return
        high_risk_ratio = sum(1 for s in weave_plan if s.get("is_high_risk")) / len(weave_plan)
        assert high_risk_ratio < 1.0, (
            f"所有步骤（{len(weave_plan)}个）均被标为高危，高危判定标准可能过宽\n"
            f"  调优建议：在 phase_aggregate system prompt 中限制高危标准仅适用于写操作步骤"
        )
