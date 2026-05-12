"""
Tool Trace Eval — C2 에이전트 도구 호출 추적 평가

사용법:
    python tests/tool_trace_eval.py          # 전체 실행
    python tests/tool_trace_eval.py -v       # 상세 출력
    python tests/tool_trace_eval.py -k recon # 특정 케이스만
"""
import sys
import json
import time
import argparse
import traceback
from typing import Callable


class MockWargameEngine:
    def __init__(self):
        self.running = True
        self.time_scale = 1
        self._applied_plans = []
        self._applied_air = []

    def get_state(self):
        return {
            "units": [
                {"id": "Alpha", "side": "BLUFOR", "type": "infantry", "position": [5000, 5000], "hp": 100, "intel_status": "detected"},
                {"id": "Bravo", "side": "BLUFOR", "type": "tank", "position": [6000, 5000], "hp": 100, "intel_status": "detected"},
                {"id": "Delta", "side": "BLUFOR", "type": "recon", "position": [4000, 4000], "hp": 100, "intel_status": "detected"},
                {"id": "Red1", "side": "OPFOR", "type": "tank", "position": [20000, 20000], "hp": 100, "intel_status": "detected"},
                {"id": "Red2", "side": "OPFOR", "type": "infantry", "position": [22000, 18000], "hp": 100, "intel_status": "approximate"},
                {"id": "Red3", "side": "OPFOR", "type": "artillery", "position": [25000, 25000], "hp": 100, "intel_status": "lost"},
            ],
            "air_supports": [],
            "game_time_str": "01:00:00",
            "tick": 3600,
            "winner": None,
        }

    def apply_mission_plan(self, plan):
        self._applied_plans.append(plan)

    def apply_air_support_plan(self, plan):
        self._applied_air.append(plan)


class EvalResult:
    def __init__(self, name, passed, detail="", elapsed=0.0):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.elapsed = elapsed

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name} ({self.elapsed*1000:.1f}ms){': ' + self.detail if self.detail else ''}"


def run_case(name, fn, verbose=False):
    start = time.time()
    try:
        detail = fn()
        elapsed = time.time() - start
        if verbose:
            print(f"  detail: {detail}")
        return EvalResult(name, True, str(detail) if detail else "", elapsed)
    except AssertionError as e:
        return EvalResult(name, False, str(e), time.time() - start)
    except Exception as e:
        return EvalResult(name, False, f"Exception: {e}\n{traceback.format_exc()}", time.time() - start)


def setup_engine():
    engine = MockWargameEngine()
    from tools import wargame_mission_tool, coa_analysis_tool
    wargame_mission_tool.register_wargame_engine(engine)
    coa_analysis_tool.register_wargame_engine(engine)
    return engine


def test_validate_valid_plan():
    from tools.mission_plan_validator import validate_mission_plan
    result = validate_mission_plan({"mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000], [20000, 20000]], "objective": "Red1 격멸"}]})
    assert result["ok"] is True, f"유효한 계획이 통과해야 함: {result}"
    return f"errors={result['errors']}, warnings={result['warnings']}"


def test_validate_invalid_company():
    from tools.mission_plan_validator import validate_mission_plan
    result = validate_mission_plan({"mission_plans": [{"company_id": "Zulu", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}]})
    assert result["ok"] is False
    assert any("Zulu" in e for e in result["errors"])
    return f"errors={result['errors'][:1]}"


def test_validate_invalid_mission_type():
    from tools.mission_plan_validator import validate_mission_plan
    result = validate_mission_plan({"mission_plans": [{"company_id": "Alpha", "mission_type": "blitz", "waypoints": [[5000, 5000]], "objective": "test"}]})
    assert result["ok"] is False
    return f"errors={result['errors'][:1]}"


def test_validate_out_of_bounds_waypoint():
    from tools.mission_plan_validator import validate_mission_plan
    result = validate_mission_plan({"mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[50000, 50000]], "objective": "test"}]})
    assert result["ok"] is False
    return f"errors={result['errors'][:1]}"


def test_validate_recon_attack_warning():
    from tools.mission_plan_validator import validate_mission_plan
    result = validate_mission_plan({"mission_plans": [
        {"company_id": "Delta", "mission_type": "recon", "waypoints": [[5000, 5000]], "objective": "정찰"},
        {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[20000, 20000]], "objective": "공격"},
    ]})
    assert result["ok"] is True
    assert any("정찰" in w and "공격" in w for w in result["warnings"])
    return f"warnings={result['warnings'][:1]}"


def test_validate_empty_mission_plans():
    from tools.mission_plan_validator import validate_mission_plan
    result = validate_mission_plan({"mission_plans": []})
    assert result["ok"] is False
    return f"errors={result['errors']}"


def test_pending_plan_save_and_retrieve():
    from tools.mission_plan_validator import validate_mission_plan, save_pending_plan, get_pending_plan, clear_pending_plan
    clear_pending_plan()
    plan = {"mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}]}
    plan_id = save_pending_plan(plan, validate_mission_plan(plan))
    assert plan_id.startswith("plan_")
    pending = get_pending_plan()
    assert pending is not None and pending["plan_id"] == plan_id
    clear_pending_plan()
    return f"plan_id={plan_id}"


def test_approve_plan_success():
    from tools.mission_plan_validator import validate_mission_plan, save_pending_plan, approve_plan, clear_pending_plan
    clear_pending_plan()
    plan = {"mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}]}
    validation = validate_mission_plan(plan)
    assert validation["ok"]
    plan_id = save_pending_plan(plan, validation)
    result = approve_plan(plan_id)
    assert result["ok"] is True
    clear_pending_plan()
    return f"approved plan_id={plan_id}"


def test_approve_wrong_plan_id():
    from tools.mission_plan_validator import validate_mission_plan, save_pending_plan, approve_plan, clear_pending_plan
    clear_pending_plan()
    plan = {"mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}]}
    save_pending_plan(plan, validate_mission_plan(plan))
    result = approve_plan("plan_wrongid")
    assert result["ok"] is False
    clear_pending_plan()
    return f"message={result['message']}"


def test_guard_write_tool_no_pending():
    from tools.mission_plan_validator import guard_write_tool, clear_pending_plan
    clear_pending_plan()
    result = guard_write_tool("apply_wargame_mission_plan", {})
    assert result["allowed"] is False and result["reason"] == "no_pending_plan"
    return f"reason={result['reason']}"


def test_guard_write_tool_not_approved():
    from tools.mission_plan_validator import validate_mission_plan, save_pending_plan, guard_write_tool, clear_pending_plan
    clear_pending_plan()
    plan = {"mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}]}
    save_pending_plan(plan, validate_mission_plan(plan))
    result = guard_write_tool("apply_wargame_mission_plan", {})
    assert result["allowed"] is False and result["reason"] == "confirmation_required"
    clear_pending_plan()
    return f"reason={result['reason']}"


def test_guard_write_tool_approved_passes():
    from tools.mission_plan_validator import validate_mission_plan, save_pending_plan, approve_plan, guard_write_tool, clear_pending_plan
    clear_pending_plan()
    plan = {"mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}]}
    plan_id = save_pending_plan(plan, validate_mission_plan(plan))
    approve_plan(plan_id)
    result = guard_write_tool("apply_wargame_mission_plan", {})
    assert result["allowed"] is True
    clear_pending_plan()
    return "guard passed after approval"


def test_apply_dry_run_default():
    setup_engine()
    from tools.mission_plan_validator import clear_pending_plan
    clear_pending_plan()
    from tools.wargame_mission_tool import apply_wargame_mission_plan
    result = apply_wargame_mission_plan(json.dumps({"mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}]}))
    assert result["status"] == "dry_run" and "plan_id" in result
    return f"plan_id={result['plan_id']}, valid={result['valid']}"


def test_apply_dry_run_blocked_without_approval():
    setup_engine()
    from tools.mission_plan_validator import clear_pending_plan
    clear_pending_plan()
    from tools.wargame_mission_tool import apply_wargame_mission_plan
    plan = json.dumps({"mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}]})
    apply_wargame_mission_plan(plan, dry_run=True)
    result = apply_wargame_mission_plan(plan, dry_run=False)
    assert result["status"] == "blocked"
    return f"reason={result.get('reason')}"


def test_apply_success_after_approval():
    engine = setup_engine()
    from tools.mission_plan_validator import clear_pending_plan, approve_plan
    clear_pending_plan()
    from tools.wargame_mission_tool import apply_wargame_mission_plan
    plan_json = json.dumps({"mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}]})
    dry = apply_wargame_mission_plan(plan_json, dry_run=True)
    approve_result = approve_plan(dry["plan_id"])
    assert approve_result["ok"]
    result = apply_wargame_mission_plan(plan_json, dry_run=False)
    assert result["status"] == "success" and result["applied"] >= 1
    return f"applied={result['applied']}"


def test_air_support_dry_run():
    setup_engine()
    from tools.wargame_mission_tool import apply_wargame_air_support
    result = apply_wargame_air_support(json.dumps({"air_support_plans": [{"call_sign": "VIPER-1", "support_type": "cas", "target": [15000, 15000], "radius": 1500, "delay": 60}]}))
    assert result["status"] == "dry_run" and result["valid"] is True
    return f"valid={result['valid']}"


def test_air_support_invalid_type():
    setup_engine()
    from tools.wargame_mission_tool import apply_wargame_air_support
    result = apply_wargame_air_support(json.dumps({"air_support_plans": [{"call_sign": "X-1", "support_type": "nuke", "target": [15000, 15000], "radius": 1000, "delay": 0}]}))
    assert result["valid"] is False
    return f"valid={result['valid']}"


def test_intent_execution_request():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("Alpha 부대에 공격 임무를 적용해줘")
    assert result["intent"] == "execution_request" and result["requires_confirmation"] is True
    return f"intent={result['intent']}"


def test_intent_recon_planning():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("현재 적 위치 정찰이 필요한지 판단해줘")
    assert result["intent"] == "recon_planning"
    return f"intent={result['intent']}, tools={result['preferred_tools'][:2]}"


def test_intent_attack_planning():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("Red1 전차부대를 격멸하는 최적 공격 계획을 수립해줘")
    assert result["intent"] == "attack_planning"
    return f"intent={result['intent']}"


def test_intent_video_query():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("드론 영상에서 탐지된 객체를 분석해줘")
    assert result["intent"] == "video_query"
    return f"intent={result['intent']}"


def test_intent_situation_query():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("현재 전투 현황을 알려줘")
    assert result["intent"] == "situation_query"
    return f"intent={result['intent']}"


def test_intent_no_confirmation_for_recon():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("정찰 경로를 추천해줘")
    assert result["requires_confirmation"] is False
    return f"requires_confirmation={result['requires_confirmation']}"


def test_coa_analysis_basic():
    setup_engine()
    from tools.coa_analysis_tool import analyze_coa_wargame
    result = analyze_coa_wargame([
        {"coa_id": "COA-1", "name": "정면 공격", "mission_plans": [
            {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[20000, 20000]], "objective": "Red1 격멸"},
            {"company_id": "Bravo", "mission_type": "attack", "waypoints": [[22000, 18000]], "objective": "Red2 격멸"},
        ]},
        {"coa_id": "COA-2", "name": "정찰 후 공격", "mission_plans": [
            {"company_id": "Delta", "mission_type": "recon", "waypoints": [[15000, 15000]], "objective": "Red3 정찰"},
            {"company_id": "Alpha", "mission_type": "flank", "waypoints": [[18000, 22000]], "objective": "Red 측방 공격"},
        ]},
    ], objective="OPFOR 격멸")
    assert result["status"] == "success" and len(result["evaluated"]) == 2
    assert result["recommended_coa"] in ("COA-1", "COA-2")
    return f"recommended={result['recommended_coa']}, scores={[(e['coa_id'], e['score']) for e in result['evaluated']]}"


def test_coa_analysis_empty():
    setup_engine()
    from tools.coa_analysis_tool import analyze_coa_wargame
    result = analyze_coa_wargame([], objective="test")
    assert result["status"] == "error"
    return f"message={result['message']}"


def test_coa_analysis_recon_bonus():
    setup_engine()
    from tools.coa_analysis_tool import analyze_coa_wargame
    r1 = analyze_coa_wargame([{"coa_id": "R1", "name": "정찰 포함", "mission_plans": [{"company_id": "Delta", "mission_type": "recon", "waypoints": [[10000, 10000]], "objective": "정찰"}]}])
    a1 = analyze_coa_wargame([{"coa_id": "A1", "name": "정찰 없음", "mission_plans": [{"company_id": "Alpha", "mission_type": "attack", "waypoints": [[20000, 20000]], "objective": "공격"}]}])
    r_score = r1["evaluated"][0]["score"]
    a_score = a1["evaluated"][0]["score"]
    assert r_score > a_score, f"정찰 포함 COA 점수({r_score})가 미포함({a_score})보다 높아야 함"
    return f"recon_score={r_score}, attack_score={a_score}"


def test_engine_status():
    setup_engine()
    from tools.wargame_mission_tool import get_wargame_engine_status
    result = get_wargame_engine_status()
    assert result["status"] == "success" and "running" in result and "tick" in result
    return f"running={result['running']}, tick={result['tick']}"


ALL_CASES = [
    ("validate/valid_plan", test_validate_valid_plan),
    ("validate/invalid_company", test_validate_invalid_company),
    ("validate/invalid_mission_type", test_validate_invalid_mission_type),
    ("validate/out_of_bounds_waypoint", test_validate_out_of_bounds_waypoint),
    ("validate/recon_attack_warning", test_validate_recon_attack_warning),
    ("validate/empty_mission_plans", test_validate_empty_mission_plans),
    ("gate/pending_save_retrieve", test_pending_plan_save_and_retrieve),
    ("gate/approve_success", test_approve_plan_success),
    ("gate/approve_wrong_id", test_approve_wrong_plan_id),
    ("gate/guard_no_pending", test_guard_write_tool_no_pending),
    ("gate/guard_not_approved", test_guard_write_tool_not_approved),
    ("gate/guard_approved_passes", test_guard_write_tool_approved_passes),
    ("apply/dry_run_default", test_apply_dry_run_default),
    ("apply/blocked_without_approval", test_apply_dry_run_blocked_without_approval),
    ("apply/success_after_approval", test_apply_success_after_approval),
    ("air/dry_run", test_air_support_dry_run),
    ("air/invalid_type", test_air_support_invalid_type),
    ("intent/execution_request", test_intent_execution_request),
    ("intent/recon_planning", test_intent_recon_planning),
    ("intent/attack_planning", test_intent_attack_planning),
    ("intent/video_query", test_intent_video_query),
    ("intent/situation_query", test_intent_situation_query),
    ("intent/no_confirmation_for_recon", test_intent_no_confirmation_for_recon),
    ("coa/basic", test_coa_analysis_basic),
    ("coa/empty", test_coa_analysis_empty),
    ("coa/recon_bonus", test_coa_analysis_recon_bonus),
    ("engine/status", test_engine_status),
]


def main():
    parser = argparse.ArgumentParser(description="C2 Tool Trace Eval")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-k", "--filter", default="")
    args = parser.parse_args()

    cases = [(n, fn) for n, fn in ALL_CASES if args.filter.lower() in n.lower()]
    if not cases:
        print(f"필터 '{args.filter}'에 해당하는 케이스 없음")
        sys.exit(1)

    results = []
    for name, fn in cases:
        r = run_case(name, fn, verbose=args.verbose)
        results.append(r)
        print(r)

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    total_ms = sum(r.elapsed for r in results) * 1000
    print(f"\n{'='*60}")
    print(f"결과: {passed}/{total} 통과  ({total_ms:.1f}ms 총 소요)")
    if passed < total:
        print("\n실패 케이스:")
        for r in results:
            if not r.passed:
                print(f"  {r}")
        sys.exit(1)
    else:
        print("모든 테스트 통과 ✓")


if __name__ == "__main__":
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, project_root)

    try:
        import smolagents  # noqa: F401
    except ModuleNotFoundError:
        from unittest.mock import MagicMock
        smolagents_stub = MagicMock()
        smolagents_stub.tool = lambda fn: fn
        sys.modules["smolagents"] = smolagents_stub

    import importlib
    import types
    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = [os.path.join(project_root, "tools")]
    tools_pkg.__package__ = "tools"
    sys.modules["tools"] = tools_pkg

    main()
