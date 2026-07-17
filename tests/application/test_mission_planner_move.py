"""Task 22/33: mission_planner (c2.application.agent.mission_planner) + advisor 주입 검증.

- 이동한 모듈에서 공개 심볼 import 가능
- 이동 모듈은 tools/ui/wargame(legacy) 를 import 하지 않음
- advisor 주입(DI)이 프롬프트에 반영됨
- advisor 미등록 시 graceful fallback (문자열 반환)
"""
import inspect

import c2.application.agent.mission_planner as mp


def _minimal_state():
    return {
        "tick": 0,
        "units": [
            {"id": "Alpha", "side": "BLUFOR", "unit_type": "기계화보병",
             "status": "active", "combat_power": 90, "x": 5000, "y": 5000},
            {"id": "Red1", "side": "OPFOR", "unit_type": "전차",
             "status": "active", "combat_power": 80, "x": 20000, "y": 20000},
        ],
        "intelligence": {"BLUFOR": [
            {"unit_id": "Red1", "unit_type": "전차", "combat_power": 80,
             "status": "detected", "known_x": 20000, "known_y": 20000},
        ]},
        "air_use_count": {"BLUFOR": 0},
        "air_use_limit": 5,
        "air_reset_at": 0,
    }


def test_public_symbols_importable():
    assert callable(mp.build_mission_query)
    assert callable(mp.set_planning_advisors)
    assert inspect.isclass(mp.MissionPlanner)


def test_module_has_no_outward_imports():
    src = inspect.getsource(mp)
    assert "from tools" not in src
    assert "import tools" not in src
    assert "wargame_recon_tool" not in src
    assert "wargame_attack_advisor_tool" not in src
    assert "wargame_fire_priority_tool" not in src
    # legacy wargame package must not be imported (domain is allowed)
    assert "from wargame" not in src
    assert "from ui" not in src


def test_advisor_injection_feeds_prompt():
    mp.set_planning_advisors(
        recon=lambda: {"status": "ok", "routes": ["X"]},
        attack=lambda: {"status": "ok", "unit_key_highground": []},
        fire=lambda: {"status": "ok", "priorities": []},
    )
    try:
        prompt = mp.build_mission_query(_minimal_state())
        assert isinstance(prompt, str)
        # injected recon JSON must appear in the prompt (proves DI wiring)
        assert '"routes": ["X"]' in prompt
        assert '"priorities": []' in prompt
    finally:
        mp.set_planning_advisors(recon=None, attack=None, fire=None)
        mp._planning_advisors["recon"] = None
        mp._planning_advisors["attack"] = None
        mp._planning_advisors["fire"] = None


def test_graceful_fallback_without_advisors():
    # reset registry to None explicitly
    mp._planning_advisors["recon"] = None
    mp._planning_advisors["attack"] = None
    mp._planning_advisors["fire"] = None
    prompt = mp.build_mission_query(_minimal_state())
    assert isinstance(prompt, str)
    # fallback recon dict shape must appear
    assert "no_recon_units" in prompt
    assert "advisor not configured" in prompt
