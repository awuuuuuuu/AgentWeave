"""
Phase 1 — 研判指令质量测试（crew_unit）

测试 _build_dept_tasks（LLM 动态分配的 fallback 模板）的规范性：
- 包含事故描述
- 包含该部门的领域关键词（至少一个）
- 所有选定部门都被分配任务

注意：实际生产路径使用 _generate_dept_tasks_llm（LLM 动态生成），
这里测试的是 LLM 失败时的兜底模板质量。
"""
from __future__ import annotations

import pytest

from tests.crew.evaluators import INCIDENT, SELECTED_DEPTS, check_dept_keywords

pytestmark = pytest.mark.crew_unit


@pytest.fixture(scope="module")
def dept_tasks() -> dict[str, str]:
    from agent.graph.crew_supervisor import _build_dept_tasks
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
        from agent.graph.crew_supervisor import _build_dept_tasks
        partial = ["env_agency", "medical_ems"]
        tasks = _build_dept_tasks(INCIDENT, partial)
        assert set(tasks.keys()) == set(partial)

    def test_unknown_dept_gets_fallback_task(self):
        from agent.graph.crew_supervisor import _build_dept_tasks
        tasks = _build_dept_tasks(INCIDENT, ["unknown_dept"])
        assert "unknown_dept" in tasks
        assert INCIDENT[:15] in tasks["unknown_dept"]
