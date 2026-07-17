"""Task 27/33: 임무계획 세션/가드/의도분류 (c2.application.planning.mission_session).

- 공개 심볼 import 가능
- mission_session 모듈은 tools/ui/wargame(legacy) import 하지 않음
- functional: classify_intent 실제 동작, save/get pending plan round-trip
- VALID_COMPANY_IDS live-proxy가 domain 갱신을 즉시 반영
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

    original = set(domain.VALID_COMPANY_IDS)
    try:
        ms.update_valid_company_ids({"Zulu"})
        assert domain.VALID_COMPANY_IDS == {"Zulu"}
        # module-level live proxy must also reflect the update immediately
        assert ms.VALID_COMPANY_IDS == {"Zulu"}
    finally:
        ms.update_valid_company_ids(original)
