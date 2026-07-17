"""Task 27: 임무계획 세션/가드/의도분류 → c2.application.planning.mission_session + shim 검증.

- 이동한 모듈에서 세션/가드/의도분류 공개 심볼 import 가능
- shim identity (tools.mission_plan_validator.X is mission_session.X) — 세션 함수
- shim은 domain 스키마 재수출(MAP_MAX, validate_mission_plan 등)도 유지
- mission_session 모듈은 tools/ui/wargame(legacy) import 하지 않음
- functional: classify_intent 실제 동작, save/get pending plan round-trip
- VALID_COMPANY_IDS live-proxy가 domain 갱신을 즉시 반영 (shim 레벨에서도)
"""
import inspect

import c2.application.planning.mission_session as ms


def test_public_symbols_importable():
    assert callable(ms.update_valid_company_ids)
    assert callable(ms.save_pending_plan)
    assert callable(ms.get_pending_plan)
    assert callable(ms.approve_plan)
    assert callable(ms.clear_pending_plan)
    assert callable(ms.get_session_state)
    assert callable(ms.guard_write_tool)
    assert callable(ms.classify_intent)


def test_shim_identity_session_functions():
    import tools.mission_plan_validator as shim
    assert shim.update_valid_company_ids is ms.update_valid_company_ids
    assert shim.save_pending_plan is ms.save_pending_plan
    assert shim.get_pending_plan is ms.get_pending_plan
    assert shim.approve_plan is ms.approve_plan
    assert shim.clear_pending_plan is ms.clear_pending_plan
    assert shim.get_session_state is ms.get_session_state
    assert shim.guard_write_tool is ms.guard_write_tool
    assert shim.classify_intent is ms.classify_intent


def test_shim_schema_reexports_still_work():
    import tools.mission_plan_validator as shim
    from c2.domain.planning import mission_plan as domain

    assert shim.MAP_MAX == domain.MAP_MAX
    assert shim.validate_mission_plan is domain.validate_mission_plan
    assert shim.VALID_MISSION_TYPES == domain.VALID_MISSION_TYPES
    assert shim.VALID_SUPPORT_TYPES == domain.VALID_SUPPORT_TYPES
    assert shim.Waypoint is domain.Waypoint
    assert shim.MissionPlanItem is domain.MissionPlanItem
    assert shim.AirSupportItem is domain.AirSupportItem
    assert shim.MissionPlanRequest is domain.MissionPlanRequest


def test_module_has_no_outward_imports():
    src = inspect.getsource(ms)
    assert "from tools" not in src
    assert "import tools" not in src
    assert "from ui" not in src
    assert "import ui" not in src
    assert "from wargame" not in src
    assert "import wargame" not in src
    assert "from agent" not in src


def test_classify_intent_real_behavior():
    result = ms.classify_intent("적 기갑 공격")
    assert isinstance(result, dict)
    assert result["intent"] == "attack_planning"
    assert "preferred_tools" in result


def test_pending_plan_round_trip():
    ms.clear_pending_plan()
    plan = {"mission_plans": []}
    validation = {"ok": True}
    plan_id = ms.save_pending_plan(plan, validation)
    assert isinstance(plan_id, str)
    got = ms.get_pending_plan()
    assert got is not None
    assert got.get("plan_id") == plan_id
    ms.clear_pending_plan()
    assert ms.get_pending_plan() is None


def test_valid_company_ids_live_proxy():
    from c2.domain.planning import mission_plan as domain
    import tools.mission_plan_validator as shim

    original = set(domain.VALID_COMPANY_IDS)
    try:
        ms.update_valid_company_ids({"Zulu"})
        assert domain.VALID_COMPANY_IDS == {"Zulu"}
        # shim-level live proxy must also reflect the update immediately
        assert shim.VALID_COMPANY_IDS == {"Zulu"}
    finally:
        ms.update_valid_company_ids(original)
