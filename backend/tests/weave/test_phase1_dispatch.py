"""
Phase 1 — 研判指令质量测试（weave_unit）

测试 _build_dept_tasks（LLM 动态分配的 fallback 模板）的规范性：
- 包含事故描述
- 包含该部门的领域关键词（至少一个）
- 所有选定部门都被分配任务

注意：实际生产路径使用 _generate_dept_tasks_llm（LLM 动态生成），
这里测试的是 LLM 失败时的兜底模板质量。
"""
from __future__ import annotations

import pytest

from tests.weave.evaluators import INCIDENT, SELECTED_DEPTS, check_dept_keywords

pytestmark = pytest.mark.weave_unit


@pytest.fixture(scope="module")
def dept_tasks() -> dict[str, str]:
    from agent.graph.weave_supervisor import _build_dept_tasks
    return _build_dept_tasks(INCIDENT, SELECTED_DEPTS)


class TestDispatchTaskCoverage:
    def test_all_selected_depts_assigned(self, dept_tasks):
        missing = [d for d in SELECTED_DEPTS if d not in dept_tasks]
        assert not missing, f"以下部门未被分配任务: {missing}"

    def test_no_extra_depts_assigned(self, dept_tasks):
        extra = [d for d in dept_tasks if d not in SELECTED_DEPTS]
        assert not extra, f"分配了不在选定列表中的部门: {extra}"


class TestDispatchTaskContent:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_task_contains_incident(self, dept_tasks, dept_code):
        task = dept_tasks[dept_code]
        # _build_dept_tasks 直接使用 incident 字符串；phase_dispatch 在其基础上
        # 追加【事故地点】坐标前缀，但本测试调用的是 fallback 模板函数，
        # 不含坐标注入，故只检查 INCIDENT 的前 15 字是否出现在任务中。
        snippet = INCIDENT[:15]
        assert snippet in task, (
            f"[{dept_code}] 任务文本未包含事故描述\n"
            f"  期望包含：{snippet!r}\n"
            f"  实际文本：{task[:100]}"
        )

    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_task_has_dept_specific_keywords(self, dept_tasks, dept_code):
        task = dept_tasks[dept_code]
        ok, msg = check_dept_keywords(dept_code, task)
        assert ok, msg

    def test_task_texts_are_unique(self, dept_tasks):
        texts = list(dept_tasks.values())
        assert len(texts) == len(set(texts)), "不同部门的任务文本完全相同，可能使用了错误的模板"


class TestDispatchCustomDeptCodes:
    def test_partial_dept_selection(self):
        from agent.graph.weave_supervisor import _build_dept_tasks
        partial = ["env_agency", "medical_ems"]
        tasks = _build_dept_tasks(INCIDENT, partial)
        assert set(tasks.keys()) == set(partial)

    def test_unknown_dept_gets_fallback_task(self):
        from agent.graph.weave_supervisor import _build_dept_tasks
        tasks = _build_dept_tasks(INCIDENT, ["unknown_dept"])
        assert "unknown_dept" in tasks
        assert INCIDENT[:15] in tasks["unknown_dept"]


class TestFireBrigadeDispatchTask:
    """验证消防救援部门的调度任务质量。"""

    def test_fire_brigade_task_contains_fire_ops(self, dept_tasks):
        """消防救援任务必须包含消防专用词。"""
        task = dept_tasks["fire_brigade"]
        fire_ops = ["消防", "灭火", "消防车", "消防站", "调派", "火场"]
        assert any(op in task for op in fire_ops), (
            f"fire_brigade 任务缺少消防专用词（期望含：{fire_ops}）\n"
            f"  实际任务：{task[:150]}\n"
            f"  调优建议：检查 _build_dept_tasks 中 fire_brigade 的模板"
        )

    def test_traffic_control_task_mentions_intersection_or_evacuation(self, dept_tasks):
        """交通管控任务应包含路口或疏散相关词。"""
        task = dept_tasks["traffic_control"]
        tc_ops = ["路口", "信号", "疏散", "管控", "通道", "交通"]
        assert any(op in task for op in tc_ops), (
            f"traffic_control 任务缺少交通管控专用词（期望含：{tc_ops}）\n"
            f"  实际任务：{task[:150]}"
        )

    def test_env_agency_task_mentions_env_monitoring(self, dept_tasks):
        """环保局任务应包含环境监测相关词。"""
        task = dept_tasks["env_agency"]
        env_ops = ["传感器", "烟雾", "PM2.5", "空气", "监测", "污染"]
        assert any(op in task for op in env_ops), (
            f"env_agency 任务缺少环境监测专用词（期望含：{env_ops}）\n"
            f"  实际任务：{task[:150]}"
        )

    def test_task_minimum_length(self, dept_tasks):
        """每个部门任务长度不应过短（至少20字）。"""
        too_short = {
            dept: len(task) for dept, task in dept_tasks.items() if len(task) < 20
        }
        assert not too_short, (
            f"以下部门任务过短（<20字），可能是模板占位符未替换：{too_short}\n"
            f"  调优建议：检查 _build_dept_tasks 模板填充逻辑"
        )


class TestDifferentIncidentTypes:
    """验证不同事故类型的调度任务质量。"""

    FLOOD_INCIDENT = "浦东新区张江路积水严重，多辆车辆被困，电力中断，请求紧急救援"
    HAZMAT_INCIDENT = "金桥工业园区化工厂发生泄漏，有毒气体扩散，周边居民需疏散"

    def test_flood_incident_all_depts_assigned(self):
        from agent.graph.weave_supervisor import _build_dept_tasks
        tasks = _build_dept_tasks(self.FLOOD_INCIDENT, SELECTED_DEPTS)
        missing = [d for d in SELECTED_DEPTS if d not in tasks]
        assert not missing, f"洪涝场景未分配部门：{missing}"

    def test_flood_incident_tasks_contain_incident(self):
        from agent.graph.weave_supervisor import _build_dept_tasks
        tasks = _build_dept_tasks(self.FLOOD_INCIDENT, SELECTED_DEPTS)
        snippet = self.FLOOD_INCIDENT[:15]
        missing = [dept for dept, task in tasks.items() if snippet not in task]
        assert not missing, f"洪涝场景以下部门任务未包含事故描述：{missing}"

    def test_hazmat_incident_all_depts_assigned(self):
        from agent.graph.weave_supervisor import _build_dept_tasks
        tasks = _build_dept_tasks(self.HAZMAT_INCIDENT, SELECTED_DEPTS)
        missing = [d for d in SELECTED_DEPTS if d not in tasks]
        assert not missing, f"危化品场景未分配部门：{missing}"

    def test_single_dept_dispatch(self):
        """只派遣单个部门时，应只分配该部门。"""
        from agent.graph.weave_supervisor import _build_dept_tasks
        tasks = _build_dept_tasks(INCIDENT, ["fire_brigade"])
        assert list(tasks.keys()) == ["fire_brigade"], (
            f"单部门调度应只返回该部门，实际：{list(tasks.keys())}"
        )
