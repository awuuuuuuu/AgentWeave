"""
Phase 3 — 交通事故场景执行计划合理性测试（weave_integration）

验证 phase_aggregate 针对交通事故的执行计划：
- 步骤数在 2-5 之间（小规模事故）
- traffic_control 和 medical_ems 均被覆盖
- 至少 1 个高危步骤
- title 包含具体数量或地点
- step_id 无重复且格式规范
"""
from __future__ import annotations

import re

import pytest

from tests.weave.evaluators import check_traffic_plan_steps

pytestmark = pytest.mark.weave_integration


class TestTrafficPlanStructure:
    def test_plan_not_empty(self, traffic_weave_plan):
        assert traffic_weave_plan, (
            "交通事故执行计划为空\n"
            "  调优建议：检查 phase_aggregate 的 LLM 调用是否成功"
        )

    def test_all_rules_pass(self, traffic_weave_plan):
        errors = check_traffic_plan_steps(traffic_weave_plan)
        assert not errors, (
            f"交通事故执行计划校验发现 {len(errors)} 个问题：\n\n"
            + "\n\n".join(f"[{i+1}] {e}" for i, e in enumerate(errors))
        )


class TestTrafficPlanStepDetails:
    def test_each_step_has_task_description(self, traffic_weave_plan):
        empty_tasks = [
            s["step_id"] for s in traffic_weave_plan if len(s.get("task", "")) < 10
        ]
        assert not empty_tasks, (
            f"以下步骤的 task 字段过短（< 10 字）: {empty_tasks}\n"
            f"  调优建议：在 phase_aggregate system prompt 中要求「task 字段需包含 2-4 句具体执行指令」"
        )

    def test_step_ids_are_sequential_format(self, traffic_weave_plan):
        bad_ids = [
            s["step_id"] for s in traffic_weave_plan
            if not re.match(r"^step-\d{3}$", s.get("step_id", ""))
        ]
        assert not bad_ids, (
            f"以下 step_id 格式不规范（期望 step-XXX）：{bad_ids}\n"
            f"  调优建议：在 phase_aggregate system prompt 中强调「step_id 格式为 step-001, step-002...」"
        )

    def test_not_all_steps_high_risk(self, traffic_weave_plan):
        if not traffic_weave_plan:
            return
        high_risk_ratio = sum(1 for s in traffic_weave_plan if s.get("is_high_risk")) / len(traffic_weave_plan)
        assert high_risk_ratio < 1.0, (
            f"所有步骤均被标为高危，高危判定标准可能过宽\n"
            f"  调优建议：在 phase_aggregate system prompt 中限制高危标准仅适用于写操作步骤"
        )


class TestTrafficPlanContent:
    """验证交通事故执行计划内容合理性。"""

    def test_traffic_step_mentions_road_management(self, traffic_weave_plan):
        """traffic_control 步骤应含路口管控或清障相关词。"""
        tc_steps = [s for s in traffic_weave_plan if s.get("dept_code") == "traffic_control"]
        if not tc_steps:
            pytest.skip("traffic_control 无步骤，由 check_traffic_plan_steps 覆盖")
        road_keywords = ["管控", "封路", "清障", "疏散", "绕行", "信号", "路口", "限行", "通道"]
        has_road = any(
            any(kw in s.get("task", "") + s.get("title", "") for kw in road_keywords)
            for s in tc_steps
        )
        assert has_road, (
            f"traffic_control 步骤缺少路口管控相关词\n"
            f"  步骤：{[s.get('title', '') for s in tc_steps]}\n"
            f"  调优建议：在 phase_aggregate prompt 中说明「交通事故 traffic_control 步骤应包含路口管控或清障指令」"
        )

    def test_medical_step_mentions_ambulance_dispatch(self, traffic_weave_plan):
        """medical_ems 步骤应含救护车派遣相关词。"""
        me_steps = [s for s in traffic_weave_plan if s.get("dept_code") == "medical_ems"]
        if not me_steps:
            pytest.skip("medical_ems 无步骤，由 check_traffic_plan_steps 覆盖")
        ems_keywords = ["救护车", "急救", "派遣", "出动", "送医", "检伤", "转运", "医院"]
        has_ems = any(
            any(kw in s.get("task", "") + s.get("title", "") for kw in ems_keywords)
            for s in me_steps
        )
        assert has_ems, (
            f"medical_ems 步骤缺少救护车派遣相关词\n"
            f"  步骤：{[s.get('title', '') for s in me_steps]}\n"
            f"  调优建议：在 phase_aggregate prompt 中说明「有伤者的事故必须包含救护车派遣步骤」"
        )

    def test_ambulance_dispatch_is_high_risk(self, traffic_weave_plan):
        """medical_ems 的救护车派遣步骤应标记为高危。"""
        me_steps = [s for s in traffic_weave_plan if s.get("dept_code") == "medical_ems"]
        if not me_steps:
            pytest.skip("medical_ems 无步骤，由 check_traffic_plan_steps 覆盖")
        dispatch_steps = [
            s for s in me_steps
            if any(kw in s.get("task", "") + s.get("title", "") for kw in ["派遣", "出动", "救护车", "dispatch"])
        ]
        if not dispatch_steps:
            pytest.skip("未找到明确的救护车派遣步骤")
        has_high_risk = any(s.get("is_high_risk") for s in dispatch_steps)
        assert has_high_risk, (
            f"救护车派遣步骤未标记为高危\n"
            f"  步骤：{[s.get('title', '') for s in dispatch_steps]}\n"
            f"  调优建议：在 phase_aggregate prompt 中将「救护车/消防车调派」明确标为 is_high_risk=true"
        )

    def test_high_risk_steps_have_map_layer_or_logged(self, traffic_weave_plan):
        """高危步骤通常应有地图图层，仅记录供人工审查（不强制 fail）。"""
        high_risk_no_layer = [
            s["step_id"] for s in traffic_weave_plan
            if s.get("is_high_risk") and not s.get("map_layer")
        ]
        if high_risk_no_layer:
            print(f"\n[INFO] 高危步骤但无 map_layer，请人工确认是否正常: {high_risk_no_layer}")
