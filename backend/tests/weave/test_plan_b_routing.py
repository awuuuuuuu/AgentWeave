"""
Plan B — 路由逻辑与指令分类单元测试（weave_unit）

涵盖：
  _route_after_classify   — 纯路由函数（无 LLM，可直接单元测试）
  _route_after_location   — 纯路由函数
  classify_intent         — 异步节点（Turn 0 无 LLM，可在 CI 运行；Turn ≥1 调 LLM）
  create_single_step_plan — 异步节点（调 LLM，需 API Key）

意图枚举（Plan B）：
  incident_response | direct_command | escalation | follow_up

注意：Plan B 尚未实现。函数未定义时测试自动 skip，实现后无需修改即可运行。
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.weave_unit


def _get(func_name: str):
    """安全导入；函数不存在时 skip。"""
    try:
        import importlib
        mod = importlib.import_module("agent.graph.weave_supervisor")
        fn = getattr(mod, func_name, None)
        if fn is None:
            pytest.skip(f"{func_name} 尚未实现（Plan B 待开发）")
        return fn
    except ImportError as e:
        pytest.skip(f"weave_supervisor 导入失败：{e}")


# ── _route_after_classify ────────────────────────────────────────────────────

class TestRouteAfterClassify:
    """纯同步路由函数——无外部依赖，CI 默认运行。"""

    @pytest.fixture(autouse=True)
    def fn(self):
        self._fn = _get("_route_after_classify")

    def test_turn0_no_location_goes_to_disambig(self):
        state = {"intent": "incident_response", "incident_location": None,
                 "event_scope": "localized", "conversation_turn": 1}
        assert self._fn(state) == "location_disambig"

    def test_turn0_has_location_goes_to_dispatch(self):
        state = {"intent": "incident_response",
                 "incident_location": {"lat": 31.23, "lng": 121.50},
                 "event_scope": "localized", "conversation_turn": 2}
        assert self._fn(state) == "phase_dispatch"

    def test_direct_command_no_location_goes_to_disambig(self):
        state = {"intent": "direct_command", "incident_location": None,
                 "event_scope": "localized", "conversation_turn": 2}
        assert self._fn(state) == "location_disambig"

    def test_direct_command_has_location_goes_to_single_step(self):
        state = {"intent": "direct_command",
                 "incident_location": {"lat": 31.23, "lng": 121.50},
                 "event_scope": "localized", "conversation_turn": 2}
        assert self._fn(state) == "create_single_step_plan"

    def test_escalation_goes_to_dispatch(self):
        """事故升级直接重新全部门研判，跳过地点消歧。"""
        state = {"intent": "escalation",
                 "incident_location": {"lat": 31.23, "lng": 121.50},
                 "event_scope": "localized", "conversation_turn": 3}
        assert self._fn(state) == "phase_dispatch"

    def test_citywide_no_location_skips_disambig(self):
        """台风/全区预警（event_scope=citywide）跳过地点消歧，直接研判。"""
        state = {"intent": "incident_response", "incident_location": None,
                 "event_scope": "citywide", "conversation_turn": 1}
        assert self._fn(state) == "phase_dispatch"


# ── _route_after_location ────────────────────────────────────────────────────

class TestRouteAfterLocation:
    """地点消歧节点后的路由——纯同步函数。"""

    @pytest.fixture(autouse=True)
    def fn(self):
        self._fn = _get("_route_after_location")

    def test_incident_response_goes_to_dispatch(self):
        state = {"intent": "incident_response",
                 "incident_location": {"lat": 31.23, "lng": 121.50}}
        assert self._fn(state) == "phase_dispatch"

    def test_direct_command_goes_to_single_step(self):
        state = {"intent": "direct_command",
                 "incident_location": {"lat": 31.23, "lng": 121.50}}
        assert self._fn(state) == "create_single_step_plan"

    def test_follow_up_goes_to_dispatch(self):
        state = {"intent": "follow_up",
                 "incident_location": {"lat": 31.23, "lng": 121.50}}
        assert self._fn(state) == "phase_dispatch"

    def test_escalation_goes_to_dispatch(self):
        state = {"intent": "escalation",
                 "incident_location": {"lat": 31.23, "lng": 121.50}}
        assert self._fn(state) == "phase_dispatch"


# ── classify_intent ───────────────────────────────────────────────────────────

class TestClassifyIntent:
    """Turn 0 不调 LLM（可在 CI 运行）；Turn ≥1 需 LLM（skip when no key）。"""

    @pytest.fixture(autouse=True)
    def fn(self):
        self._fn = _get("classify_intent")

    @pytest.mark.parametrize("incident,expected_scope", [
        ("台风橙色预警，浦东全区进入一级应急响应", "citywide"),
        ("全市暴雨黄色预警", "citywide"),
        ("大风橙色预警，全区停课", "citywide"),
        ("世纪大道×陆家嘴建筑起火，2人受伤", "localized"),
        ("浦东南路多车追尾，3人受伤", "localized"),
        ("张江某化工仓库危化品泄漏", "localized"),
    ])
    def test_turn0_scope_detection(self, incident, expected_scope):
        """Turn 0 直接检测 citywide 关键词，不调 LLM。"""
        state = {"incident": incident, "conversation_turn": 0}
        result = asyncio.run(self._fn(state, {"configurable": {}}))
        assert result.get("intent") == "incident_response", (
            f"Turn 0 应始终返回 incident_response，实际：{result.get('intent')}"
        )
        assert result.get("event_scope") == expected_scope, (
            f"事件范围判断错误\n  输入：{incident}\n"
            f"  期望：{expected_scope}，实际：{result.get('event_scope')}\n"
            f"  调优建议：检查 classify_intent 的 _CITYWIDE_KEYWORDS 列表"
        )

    def test_turn0_increments_conversation_turn(self):
        state = {"incident": "世纪大道×陆家嘴建筑起火", "conversation_turn": 0}
        result = asyncio.run(self._fn(state, {"configurable": {}}))
        assert result.get("conversation_turn") == 1, (
            f"Turn 0 后应将 conversation_turn 设为 1，实际：{result.get('conversation_turn')}"
        )

    def test_turn0_none_treated_as_zero(self):
        """conversation_turn=None 应与 0 等价（首次调用）。"""
        state = {"incident": "世纪大道×陆家嘴建筑起火", "conversation_turn": None}
        result = asyncio.run(self._fn(state, {"configurable": {}}))
        assert result.get("intent") == "incident_response"


# ── create_single_step_plan ───────────────────────────────────────────────────

class TestCreateSingleStepPlan:
    """调 LLM——需 API Key，否则 skip。"""

    @pytest.fixture(autouse=True)
    def fn(self):
        import os
        if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
            pytest.skip("无 LLM API Key，跳过 create_single_step_plan 集成测试")
        self._fn = _get("create_single_step_plan")

    def _state(self, incident, depts, location=None):
        return {
            "incident": incident,
            "incident_location": location or {"name": "张江高科技园", "lat": 31.20, "lng": 121.60},
            "selected_dept_codes": depts,
            "a2a_urls": {},
        }

    def test_elevator_generates_one_to_three_steps(self):
        result = asyncio.run(self._fn(
            self._state("浦东新区某写字楼电梯故障，2人被困", ["fire_brigade"]),
            {"configurable": {}},
        ))
        plan = result.get("dispatch_plan", [])
        assert 1 <= len(plan) <= 3, f"单步计划应生成 1-3 步，实际 {len(plan)} 步"

    def test_step_has_required_fields(self):
        result = asyncio.run(self._fn(
            self._state("浦东新区某写字楼电梯故障，2人被困", ["fire_brigade"]),
            {"configurable": {}},
        ))
        plan = result.get("dispatch_plan", [])
        if not plan:
            pytest.skip("计划为空，由 test_elevator_generates_one_to_three_steps 覆盖")
        for step in plan:
            missing = {"step_id", "dept_code", "title", "task"} - set(step.keys())
            assert not missing, f"步骤 {step.get('step_id', '?')} 缺少字段：{missing}"

    def test_dept_code_in_selected(self):
        result = asyncio.run(self._fn(
            self._state("浦东南路多车追尾，3人受伤", ["traffic_control", "medical_ems"]),
            {"configurable": {}},
        ))
        plan = result.get("dispatch_plan", [])
        depts = ["traffic_control", "medical_ems"]
        invalid = [s["dept_code"] for s in plan if s.get("dept_code") not in depts]
        assert not invalid, f"步骤中出现未选定部门：{invalid}"
