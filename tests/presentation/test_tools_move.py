"""Task 28: LLM 툴 어댑터 → c2.presentation.tools + shim 검증.

- 13개 이동 모듈이 c2.presentation.tools.<name> 에서 import 가능
- shim identity: tools.<name>.<symbol> is c2.presentation.tools.<name>.<symbol>
- advisor 배선 경로(wargame.llm_planner)가 여전히 동작
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


def test_shim_identity_for_representative_symbols():
    import tools.wargame_query_tool as old_query
    import c2.presentation.tools.wargame_query_tool as new_query
    assert old_query.get_wargame_situation is new_query.get_wargame_situation

    import tools.wargame_mission_tool as old_mission
    import c2.presentation.tools.wargame_mission_tool as new_mission
    assert old_mission.apply_wargame_mission_plan is new_mission.apply_wargame_mission_plan

    import tools.wargame_recon_tool as old_recon
    import c2.presentation.tools.wargame_recon_tool as new_recon
    assert old_recon.recommend_recon_routes is new_recon.recommend_recon_routes

    import tools.coa_analysis_tool as old_coa
    import c2.presentation.tools.coa_analysis_tool as new_coa
    assert old_coa.analyze_coa_wargame is new_coa.analyze_coa_wargame

    import tools.graph_rag_tool as old_rag
    import c2.presentation.tools.graph_rag_tool as new_rag
    assert old_rag.graph_rag_military_query is new_rag.graph_rag_military_query

    import tools.single_tool_guard as old_guard
    import c2.presentation.tools.single_tool_guard as new_guard
    assert old_guard.guard is new_guard.guard

    import tools.strategy_advisor_tool as old_strat
    import c2.presentation.tools.strategy_advisor_tool as new_strat
    assert old_strat.get_situation_memory is new_strat.get_situation_memory


def test_advisor_wiring_still_resolves():
    # llm_planner shim wires recon/attack/fire advisors from tools.* at import time;
    # verifying the underlying symbols still resolve through the shim proves the
    # wiring path (tools.wargame_recon_tool etc.) keeps working post-move.
    from tools.wargame_recon_tool import recommend_recon_routes
    from tools.wargame_attack_advisor_tool import get_optimal_attack_positions
    from tools.wargame_fire_priority_tool import get_fire_priority_schedule

    assert callable(recommend_recon_routes)
    assert callable(get_optimal_attack_positions)
    assert callable(get_fire_priority_schedule)

    import wargame.llm_planner as llm_planner
    assert callable(llm_planner.build_mission_query)
    assert callable(llm_planner.set_planning_advisors)
