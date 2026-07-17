"""Task 28/33: LLM 툴 어댑터 (c2.presentation.tools).

- 13개 이동 모듈이 c2.presentation.tools.<name> 에서 import 가능
- advisor 배선 경로(c2.composition.container._wire_planning_advisors 가 사용하는
  recon/attack/fire 함수들)가 여전히 동작
"""
import importlib

MOVED_MODULES = [
    "wargame_query_tool",
    "wargame_mission_tool",
    "wargame_recon_tool",
    "wargame_attack_advisor_tool",
    "wargame_fire_priority_tool",
    "wargame_opfor_routes_tool",
    "wargame_strategy_tool",
    "coa_analysis_tool",
    "ontology_query_tool",
    "strategy_advisor_tool",
    "single_tool_guard",
    "mission_plan_validator_tool",
    "graph_rag_tool",
]


def test_all_13_modules_importable_from_c2_presentation_tools():
    for name in MOVED_MODULES:
        mod = importlib.import_module(f"c2.presentation.tools.{name}")
        assert mod is not None


def test_advisor_wiring_still_resolves():
    # c2.composition.container._wire_planning_advisors() 가 이 3개 함수를
    # c2.application.agent.mission_planner.set_planning_advisors() 에 주입한다;
    # 이동 후에도 c2.presentation.tools 경로에서 정상 resolve되는지 확인한다
    # (레거시 wargame.llm_planner shim import 없이).
    from c2.presentation.tools.wargame_recon_tool import recommend_recon_routes
    from c2.presentation.tools.wargame_attack_advisor_tool import get_optimal_attack_positions
    from c2.presentation.tools.wargame_fire_priority_tool import get_fire_priority_schedule

    assert callable(recommend_recon_routes)
    assert callable(get_optimal_attack_positions)
    assert callable(get_fire_priority_schedule)

    import c2.application.agent.mission_planner as mission_planner
    assert callable(mission_planner.build_mission_query)
    assert callable(mission_planner.set_planning_advisors)
