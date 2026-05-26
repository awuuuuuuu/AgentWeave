"""
Phase 3 — 危化品泄漏场景执行计划合理性测试（weave_integration）

验证 phase_aggregate 针对危化品泄漏的执行计划：
- 步骤数在 3-8 之间
- env_agency / fire_brigade / medical_ems / traffic_control 全部覆盖
- env_agency 步骤含监测/预警/污染相关词
- fire_brigade 步骤含防护/洗消/堵漏等危化品专项词
- 至少 1 个高危步骤
- title 包含具体数量或地点
"""
from __future__ import annotations

import re

import pytest

from tests.weave.evaluators import HAZMAT_DEPTS, check_hazmat_plan_steps

pytestmark = pytest.mark.weave_integration


class TestHazmatPlanStructure:
    def test_plan_not_empty(self, hazmat_weave_plan):
        assert hazmat_weave_plan, (
            "危化品泄漏执行计划为空\n"
            "  调优建议：检查 phase_aggregate 的 LLM 调用是否成功"
        )

    def test_all_rules_pass(self, hazmat_weave_plan):
        errors = check_hazmat_plan_steps(hazmat_weave_plan)
        assert not errors, (
            f"危化品泄漏执行计划校验发现 {len(errors)} 个问题：\n\n"
            + "\n\n".join(f"[{i+1}] {e}" for i, e in enumerate(errors))
        )


class TestHazmatPlanStepDetails:
    def test_each_step_has_task_description(self, hazmat_weave_plan):
        empty_tasks = [
            s["step_id"] for s in hazmat_weave_plan if len(s.get("task", "")) < 10
        ]
        assert not empty_tasks, (
            f"以下步骤的 task 字段过短（< 10 字）: {empty_tasks}\n"
            f"  调优建议：在 phase_aggregate system prompt 中要求「task 字段需包含 2-4 句具体执行指令」"
        )

    def test_step_ids_are_sequential_format(self, hazmat_weave_plan):
        bad_ids = [
            s["step_id"] for s in hazmat_weave_plan
            if not re.match(r"^step-\d{3}$", s.get("step_id", ""))
        ]
        assert not bad_ids, (
            f"以下 step_id 格式不规范（期望 step-XXX）：{bad_ids}\n"
            f"  调优建议：在 phase_aggregate system prompt 中强调「step_id 格式为 step-001, step-002...」"
        )

    def test_not_all_steps_high_risk(self, hazmat_weave_plan):
        if not hazmat_weave_plan:
            return
        high_risk_ratio = sum(1 for s in hazmat_weave_plan if s.get("is_high_risk")) / len(hazmat_weave_plan)
        assert high_risk_ratio < 1.0, (
            f"所有步骤均被标为高危，高危判定标准可能过宽\n"
            f"  调优建议：在 phase_aggregate system prompt 中限制高危标准仅适用于写操作步骤"
        )


class TestHazmatPlanContent:
    """验证危化品泄漏执行计划的内容合理性。"""

    def test_env_agency_step_content(self, hazmat_weave_plan):
        """env_agency 步骤应含污染监测/预警/传感器相关内容。"""
        env_steps = [s for s in hazmat_weave_plan if s.get("dept_code") == "env_agency"]
        if not env_steps:
            pytest.skip("env_agency 无步骤，由 check_hazmat_plan_steps 覆盖")
        env_keywords = ["监测", "预警", "污染", "浓度", "传感器", "扩散", "隔离", "警戒"]
        has_env_kw = any(
            any(kw in s.get("task", "") + s.get("title", "") for kw in env_keywords)
            for s in env_steps
        )
        assert has_env_kw, (
            f"env_agency 步骤缺少污染监测/预警词\n"
            f"  步骤：{[s.get('title', '') for s in env_steps]}\n"
            f"  调优建议：在 phase_aggregate prompt 中加入「危化品场景 env_agency 应布置传感器监测并发布预警」示例"
        )

    def test_fire_brigade_step_content(self, hazmat_weave_plan):
        """fire_brigade 步骤应含危化品专项处置词（而非普通灭火）。"""
        ff_steps = [s for s in hazmat_weave_plan if s.get("dept_code") == "fire_brigade"]
        if not ff_steps:
            pytest.skip("fire_brigade 无步骤，由 check_hazmat_plan_steps 覆盖")
        hazmat_keywords = ["防护", "洗消", "堵漏", "隔离区", "危化", "化学", "处置", "SCBA", "呼吸器", "防化"]
        has_hazmat_kw = any(
            any(kw in s.get("task", "") + s.get("title", "") for kw in hazmat_keywords)
            for s in ff_steps
        )
        assert has_hazmat_kw, (
            f"fire_brigade 危化品步骤缺少专项处置词\n"
            f"  步骤：{[s.get('title', '') for s in ff_steps]}\n"
            f"  调优建议：在 phase_aggregate prompt 中说明「危化品场景消防步骤应含防护装备和处置指令，而非普通灭火」"
        )

    def test_traffic_control_evacuation_step(self, hazmat_weave_plan):
        """traffic_control 步骤应含疏散/封路/禁止进入相关词（危化品区域管控）。"""
        tc_steps = [s for s in hazmat_weave_plan if s.get("dept_code") == "traffic_control"]
        if not tc_steps:
            pytest.skip("traffic_control 无步骤，由 check_hazmat_plan_steps 覆盖")
        evacuation_keywords = ["疏散", "封路", "禁止", "管控", "绕行", "警戒", "隔离", "通道", "限行"]
        has_evac = any(
            any(kw in s.get("task", "") + s.get("title", "") for kw in evacuation_keywords)
            for s in tc_steps
        )
        assert has_evac, (
            f"traffic_control 危化品步骤缺少疏散/封路词\n"
            f"  步骤：{[s.get('title', '') for s in tc_steps]}\n"
            f"  调优建议：在 phase_aggregate prompt 中说明「危化品泄漏交通管控应包含周边疏散和进入禁止」"
        )

    def test_medical_ems_evacuation_support(self, hazmat_weave_plan):
        """medical_ems 步骤应含急救或疏散人员医疗保障词。"""
        me_steps = [s for s in hazmat_weave_plan if s.get("dept_code") == "medical_ems"]
        if not me_steps:
            pytest.skip("medical_ems 无步骤，由 check_hazmat_plan_steps 覆盖")
        ems_keywords = ["救护", "急救", "医疗", "中毒", "洗眼", "伤者", "救治", "转运", "医院"]
        has_ems = any(
            any(kw in s.get("task", "") + s.get("title", "") for kw in ems_keywords)
            for s in me_steps
        )
        assert has_ems, (
            f"medical_ems 危化品步骤缺少急救/中毒救治词\n"
            f"  步骤：{[s.get('title', '') for s in me_steps]}\n"
            f"  调优建议：在 phase_aggregate prompt 中说明「危化品中毒伤者需专业解毒/洗消处置」"
        )

    def test_fire_brigade_is_high_risk(self, hazmat_weave_plan):
        """fire_brigade 危化品处置步骤应标记为高危（涉及写操作和人身安全）。"""
        ff_steps = [s for s in hazmat_weave_plan if s.get("dept_code") == "fire_brigade"]
        if not ff_steps:
            pytest.skip("fire_brigade 无步骤，由 check_hazmat_plan_steps 覆盖")
        has_high_risk = any(s.get("is_high_risk") for s in ff_steps)
        assert has_high_risk, (
            f"fire_brigade 步骤中无高危步骤，危化品处置应标记为高危\n"
            f"  步骤列表：{[s.get('title', '') for s in ff_steps]}\n"
            f"  调优建议：在 phase_aggregate prompt 中将「危化品现场处置/调派防化车辆」标为 is_high_risk=true"
        )

    def test_high_risk_steps_have_map_layer_or_logged(self, hazmat_weave_plan):
        """高危步骤通常应有地图图层，仅记录供人工审查（不强制 fail）。"""
        high_risk_no_layer = [
            s["step_id"] for s in hazmat_weave_plan
            if s.get("is_high_risk") and not s.get("map_layer")
        ]
        if high_risk_no_layer:
            print(f"\n[INFO] 高危步骤但无 map_layer，请人工确认是否正常: {high_risk_no_layer}")


class TestHazmatPlanDeptCoverage:
    """验证危化品场景所有参与部门均被分配步骤。"""

    @pytest.mark.parametrize("dept_code", HAZMAT_DEPTS)
    def test_each_dept_has_steps(self, hazmat_weave_plan, dept_code):
        dept_steps = [s for s in hazmat_weave_plan if s.get("dept_code") == dept_code]
        assert dept_steps, (
            f"危化品计划中 [{dept_code}] 未被分配任何步骤\n"
            f"  调优建议：在 phase_aggregate system prompt 中要求「每个参与部门至少一步」"
        )
