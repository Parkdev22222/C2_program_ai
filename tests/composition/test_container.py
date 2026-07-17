"""Task 32: composition root(`c2.composition.container.build_session`) 검증.

`build_session(agent=None)`이 `WargameSession`의 전체 의존성(EventStore/HarnessStore
기본 팩토리, presentation 8개 툴 엔진등록 훅, 온톨로지 그래프스토어/writer 팩토리,
계획 자문(advisor), replan_hooks, agent)을 한곳에서 wiring함을 검증한다.

- `build_session()` 호출만으로 RuntimeError 없이 세션이 반환되어야 한다(29A 풋건 방지 —
  EventStore 기본 팩토리가 세션 생성 전에 미리 전역 등록되어 있어야 함).
- `session.ensure_engine()` 후 presentation 8개 툴 모듈의 `_wargame_engine` 전역이
  생성된 엔진으로 설정되어 있어야 한다(`register_wargame_engine` 경유 확인).
- `set_planning_advisors()`로 recon/attack/fire 자문이 모두 등록되어 있어야 한다.
- `session.replan_hooks`가 `c2.application.simulation.replan`이 호출하는 8개 메서드를
  모두 제공해야 한다.
- `build_session(agent=X)`는 `session.agent is X`를 만족해야 한다.
"""

from __future__ import annotations

import importlib

import pytest


_TOOL_MODULE_NAMES = [
    "c2.presentation.tools.wargame_query_tool",
    "c2.presentation.tools.wargame_mission_tool",
    "c2.presentation.tools.wargame_strategy_tool",
    "c2.presentation.tools.wargame_attack_advisor_tool",
    "c2.presentation.tools.wargame_fire_priority_tool",
    "c2.presentation.tools.wargame_recon_tool",
    "c2.presentation.tools.wargame_opfor_routes_tool",
    "c2.presentation.tools.coa_analysis_tool",
]

_REPLAN_HOOK_METHODS = [
    "reset_apply_tracker",
    "was_plan_applied_since",
    "get_last_applied_plan",
    "set_resume_on_apply",
    "get_instruction_section",
    "assess_recon_need",
    "recommend_recon_routes",
    "append_learned_rule",
]


def test_build_session_returns_wargame_session_without_runtime_error():
    from c2.application.simulation.session import WargameSession
    from c2.composition.container import build_session

    session = build_session()

    assert isinstance(session, WargameSession)
    # 29A 풋건: EventStore 기본 팩토리가 이미 등록되어 있어야 예외 없이 엔진 생성 가능.
    engine = session.ensure_engine()
    assert engine is not None


def test_build_session_registers_engine_into_all_8_tools():
    from c2.composition.container import build_session

    session = build_session()
    engine = session.ensure_engine()

    for mod_name in _TOOL_MODULE_NAMES:
        mod = importlib.import_module(mod_name)
        assert mod._wargame_engine is engine, f"{mod_name} 에 엔진이 등록되지 않음"


def test_build_session_registers_planning_advisors():
    from c2.application.agent.mission_planner import _planning_advisors
    from c2.composition.container import build_session

    build_session()

    assert _planning_advisors["recon"] is not None
    assert _planning_advisors["attack"] is not None
    assert _planning_advisors["fire"] is not None


def test_build_session_sets_complete_replan_hooks():
    from c2.composition.container import build_session

    session = build_session()

    assert session.replan_hooks is not None
    for method_name in _REPLAN_HOOK_METHODS:
        assert hasattr(session.replan_hooks, method_name), (
            f"replan_hooks 에 {method_name} 메서드가 없음"
        )
        assert callable(getattr(session.replan_hooks, method_name))


def test_build_session_wires_agent():
    from c2.composition.container import build_session

    sentinel_agent = object()
    session = build_session(agent=sentinel_agent)

    assert session.agent is sentinel_agent


def test_build_session_wires_harness_db_factory():
    """HarnessController가 세션당 1회 생성될 수 있도록 HarnessStore 기본 팩토리가
    미리 전역 등록되어 있어야 한다(RuntimeError 없이 init_harness_controller 성공)."""
    from c2.composition.container import build_session

    session = build_session()
    session.ensure_engine()
    ctrl = session.init_harness_controller()

    assert ctrl is not None
