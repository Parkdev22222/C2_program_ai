"""
Tool Trace Eval — C2 에이전트 도구 호출 추적 평가

각 도구의 기능, intent 라우팅, confirmation gate, COA 분석을 단위 테스트합니다.
실제 모델 없이 mock 엔진으로 독립 실행 가능합니다.

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
from typing import Callable, Optional

# ─────────────────────────────────────────────────────────────
# Mock Wargame Engine
# ─────────────────────────────────────────────────────────────

class MockWargameEngine:
    """실제 엔진 없이 테스트할 수 있는 최소 Mock."""

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


# ─────────────────────────────────────────────────────────────
# Test Runner
# ─────────────────────────────────────────────────────────────

class EvalResult:
    def __init__(self, name: str, passed: bool, detail: str = "", elapsed: float = 0.0):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.elapsed = elapsed

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name} ({self.elapsed*1000:.1f}ms){': ' + self.detail if self.detail else ''}"


def run_case(name: str, fn: Callable, verbose: bool = False) -> EvalResult:
    start = time.time()
    try:
        detail = fn()
        elapsed = time.time() - start
        passed = True
        if verbose:
            print(f"  detail: {detail}")
        return EvalResult(name, passed, str(detail) if detail else "", elapsed)
    except AssertionError as e:
        elapsed = time.time() - start
        return EvalResult(name, False, str(e), elapsed)
    except Exception as e:
        elapsed = time.time() - start
        return EvalResult(name, False, f"Exception: {e}\n{traceback.format_exc()}", elapsed)


# ─────────────────────────────────────────────────────────────
# Test Cases
# ─────────────────────────────────────────────────────────────

def setup_engine():
    """엔진 등록 (각 테스트 시작 전 호출)."""
    engine = MockWargameEngine()
    from tools import wargame_mission_tool, coa_analysis_tool
    wargame_mission_tool.register_wargame_engine(engine)
    coa_analysis_tool.register_wargame_engine(engine)
    return engine


# ── validate_mission_plan ──────────────────────────────

def test_validate_valid_plan():
    from tools.mission_plan_validator import validate_mission_plan
    plan = {
        "mission_plans": [
            {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000], [20000, 20000]], "objective": "Red1 격멸"},
        ]
    }
    result = validate_mission_plan(plan)
    assert result["ok"] is True, f"유효한 계획이 통과해야 함: {result}"
    assert result["errors"] == [], f"오류가 없어야 함: {result['errors']}"
    return f"errors={result['errors']}, warnings={result['warnings']}"


def test_validate_invalid_company():
    from tools.mission_plan_validator import validate_mission_plan
    plan = {
        "mission_plans": [
            {"company_id": "Zulu", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"},
        ]
    }
    result = validate_mission_plan(plan)
    assert result["ok"] is False, "허용되지 않은 부대는 실패해야 함"
    assert any("Zulu" in e for e in result["errors"]), f"Zulu 오류 메시지 없음: {result['errors']}"
    return f"errors={result['errors'][:1]}"


def test_validate_invalid_mission_type():
    from tools.mission_plan_validator import validate_mission_plan
    plan = {
        "mission_plans": [
            {"company_id": "Alpha", "mission_type": "blitz", "waypoints": [[5000, 5000]], "objective": "test"},
        ]
    }
    result = validate_mission_plan(plan)
    assert result["ok"] is False, "허용되지 않은 임무 유형은 실패해야 함"
    return f"errors={result['errors'][:1]}"


def test_validate_out_of_bounds_waypoint():
    from tools.mission_plan_validator import validate_mission_plan
    plan = {
        "mission_plans": [
            {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[50000, 50000]], "objective": "test"},
        ]
    }
    result = validate_mission_plan(plan)
    assert result["ok"] is False, "범위 초과 waypoint는 실패해야 함"
    return f"errors={result['errors'][:1]}"


def test_validate_recon_attack_warning():
    from tools.mission_plan_validator import validate_mission_plan
    plan = {
        "mission_plans": [
            {"company_id": "Delta", "mission_type": "recon", "waypoints": [[5000, 5000]], "objective": "정찰"},
            {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[20000, 20000]], "objective": "공격"},
        ]
    }
    result = validate_mission_plan(plan)
    assert result["ok"] is True, "경고만 있으면 ok=True여야 함"
    assert any("정찰" in w and "공격" in w for w in result["warnings"]), f"경고 없음: {result['warnings']}"
    return f"warnings={result['warnings'][:1]}"


def test_validate_empty_mission_plans():
    from tools.mission_plan_validator import validate_mission_plan
    plan = {"mission_plans": []}
    result = validate_mission_plan(plan)
    assert result["ok"] is False, "빈 mission_plans는 실패해야 함"
    return f"errors={result['errors']}"


# ── pending_plan / confirmation gate ────────────────────

def test_pending_plan_save_and_retrieve():
    from tools.mission_plan_validator import (
        validate_mission_plan, save_pending_plan, get_pending_plan, clear_pending_plan
    )
    clear_pending_plan()
    plan = {"mission_plans": [
        {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}
    ]}
    validation = validate_mission_plan(plan)
    plan_id = save_pending_plan(plan, validation)
    assert plan_id.startswith("plan_"), f"plan_id 형식 오류: {plan_id}"
    pending = get_pending_plan()
    assert pending is not None, "pending_plan이 저장되어야 함"
    assert pending["plan_id"] == plan_id, "plan_id 불일치"
    clear_pending_plan()
    return f"plan_id={plan_id}"


def test_approve_plan_success():
    from tools.mission_plan_validator import (
        validate_mission_plan, save_pending_plan, approve_plan, clear_pending_plan
    )
    clear_pending_plan()
    plan = {"mission_plans": [
        {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}
    ]}
    validation = validate_mission_plan(plan)
    assert validation["ok"], f"검증 실패: {validation}"
    plan_id = save_pending_plan(plan, validation)
    result = approve_plan(plan_id)
    assert result["ok"] is True, f"승인 실패: {result}"
    clear_pending_plan()
    return f"approved plan_id={plan_id}"


def test_approve_wrong_plan_id():
    from tools.mission_plan_validator import (
        validate_mission_plan, save_pending_plan, approve_plan, clear_pending_plan
    )
    clear_pending_plan()
    plan = {"mission_plans": [
        {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}
    ]}
    validation = validate_mission_plan(plan)
    save_pending_plan(plan, validation)
    result = approve_plan("plan_wrongid")
    assert result["ok"] is False, "잘못된 plan_id는 실패해야 함"
    clear_pending_plan()
    return f"message={result['message']}"


def test_guard_write_tool_no_pending():
    from tools.mission_plan_validator import guard_write_tool, clear_pending_plan
    clear_pending_plan()
    result = guard_write_tool("apply_wargame_mission_plan", {})
    assert result["allowed"] is False, "pending 없으면 차단되어야 함"
    assert result["reason"] == "no_pending_plan"
    return f"reason={result['reason']}"


def test_guard_write_tool_not_approved():
    from tools.mission_plan_validator import (
        validate_mission_plan, save_pending_plan, guard_write_tool, clear_pending_plan
    )
    clear_pending_plan()
    plan = {"mission_plans": [
        {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}
    ]}
    validation = validate_mission_plan(plan)
    save_pending_plan(plan, validation)
    result = guard_write_tool("apply_wargame_mission_plan", {})
    assert result["allowed"] is False, "미승인 상태는 차단되어야 함"
    assert result["reason"] == "confirmation_required"
    clear_pending_plan()
    return f"reason={result['reason']}"


def test_guard_write_tool_approved_passes():
    from tools.mission_plan_validator import (
        validate_mission_plan, save_pending_plan, approve_plan, guard_write_tool, clear_pending_plan
    )
    clear_pending_plan()
    plan = {"mission_plans": [
        {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}
    ]}
    validation = validate_mission_plan(plan)
    plan_id = save_pending_plan(plan, validation)
    approve_plan(plan_id)
    result = guard_write_tool("apply_wargame_mission_plan", {})
    assert result["allowed"] is True, f"승인 후 통과되어야 함: {result}"
    clear_pending_plan()
    return "guard passed after approval"


# ── apply_wargame_mission_plan dry_run ────────────────────

def test_apply_dry_run_default():
    setup_engine()
    from tools.mission_plan_validator import clear_pending_plan
    clear_pending_plan()
    from tools.wargame_mission_tool import apply_wargame_mission_plan
    plan = json.dumps({"mission_plans": [
        {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}
    ]})
    result = apply_wargame_mission_plan(plan)
    assert result["status"] == "dry_run", f"기본값은 dry_run이어야 함: {result}"
    assert "plan_id" in result, "plan_id가 반환되어야 함"
    return f"plan_id={result['plan_id']}, valid={result['valid']}"


def test_apply_dry_run_blocked_without_approval():
    setup_engine()
    from tools.mission_plan_validator import clear_pending_plan
    clear_pending_plan()
    from tools.wargame_mission_tool import apply_wargame_mission_plan
    plan = json.dumps({"mission_plans": [
        {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}
    ]})
    # dry_run=True로 pending 저장
    apply_wargame_mission_plan(plan, dry_run=True)
    # 승인 없이 dry_run=False 시도
    result = apply_wargame_mission_plan(plan, dry_run=False)
    assert result["status"] == "blocked", f"미승인 상태는 blocked여야 함: {result}"
    return f"reason={result.get('reason')}"


def test_apply_success_after_approval():
    engine = setup_engine()
    from tools.mission_plan_validator import clear_pending_plan, approve_plan
    clear_pending_plan()
    from tools.wargame_mission_tool import apply_wargame_mission_plan
    plan_data = {"mission_plans": [
        {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[5000, 5000]], "objective": "test"}
    ]}
    plan_json = json.dumps(plan_data)
    dry = apply_wargame_mission_plan(plan_json, dry_run=True)
    plan_id = dry["plan_id"]
    approve_result = approve_plan(plan_id)
    assert approve_result["ok"], f"승인 실패: {approve_result}"
    result = apply_wargame_mission_tool_fn = apply_wargame_mission_plan(plan_json, dry_run=False)
    assert result["status"] == "success", f"실제 적용 실패: {result}"
    assert result["applied"] >= 1, f"적용된 부대가 없음: {result}"
    return f"applied={result['applied']}"


# ── apply_wargame_air_support ───────────────────────────

def test_air_support_dry_run():
    setup_engine()
    from tools.wargame_mission_tool import apply_wargame_air_support
    plan = json.dumps({"air_support_plans": [
        {"call_sign": "VIPER-1", "support_type": "cas", "target": [15000, 15000], "radius": 1500, "delay": 60}
    ]})
    result = apply_wargame_air_support(plan)
    assert result["status"] == "dry_run", f"기본값은 dry_run이어야 함: {result}"
    assert result["valid"] is True, f"유효한 계획이어야 함: {result}"
    return f"valid={result['valid']}"


def test_air_support_invalid_type():
    setup_engine()
    from tools.wargame_mission_tool import apply_wargame_air_support
    plan = json.dumps({"air_support_plans": [
        {"call_sign": "X-1", "support_type": "nuke", "target": [15000, 15000], "radius": 1000, "delay": 0}
    ]})
    result = apply_wargame_air_support(plan)
    assert result["valid"] is False, "허용되지 않은 support_type은 실패해야 함"
    return f"valid={result['valid']}"


# ── classify_intent ────────────────────────────────────

def test_intent_execution_request():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("Alpha 부대에 공격 임무를 적용해줘")
    assert result["intent"] == "execution_request", f"의도 불일치: {result}"
    assert result["requires_confirmation"] is True
    return f"intent={result['intent']}"


def test_intent_recon_planning():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("현재 적 위치 정찰이 필요한지 판단해줘")
    assert result["intent"] == "recon_planning", f"의도 불일치: {result}"
    return f"intent={result['intent']}, tools={result['preferred_tools'][:2]}"


def test_intent_attack_planning():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("Red1 전차부대를 격멸하는 최적 공격 계획을 수립해줘")
    assert result["intent"] == "attack_planning", f"의도 불일치: {result}"
    return f"intent={result['intent']}"


def test_intent_video_query():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("드론 영상에서 탐지된 객체를 분석해줘")
    assert result["intent"] == "video_query", f"의도 불일치: {result}"
    return f"intent={result['intent']}"


def test_intent_situation_query():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("현재 전투 현황을 알려줘")
    assert result["intent"] == "situation_query", f"의도 불일치: {result}"
    return f"intent={result['intent']}"


def test_intent_no_confirmation_for_recon():
    from tools.mission_plan_validator import classify_intent
    result = classify_intent("정찰 경로를 추천해줘")
    assert result["requires_confirmation"] is False, "정찰 계획 수립은 승인 불필요"
    return f"requires_confirmation={result['requires_confirmation']}"


# ── COA 분석 ────────────────────────────────────────────

def test_coa_analysis_basic():
    setup_engine()
    from tools.coa_analysis_tool import analyze_coa_wargame
    coa_list = [
        {
            "coa_id": "COA-1",
            "name": "정면 공격",
            "mission_plans": [
                {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[20000, 20000]], "objective": "Red1 격멸"},
                {"company_id": "Bravo", "mission_type": "attack", "waypoints": [[22000, 18000]], "objective": "Red2 격멸"},
            ],
        },
        {
            "coa_id": "COA-2",
            "name": "정찰 후 공격",
            "mission_plans": [
                {"company_id": "Delta", "mission_type": "recon", "waypoints": [[15000, 15000]], "objective": "Red3 정찰"},
                {"company_id": "Alpha", "mission_type": "flank", "waypoints": [[18000, 22000]], "objective": "Red 측방 공격"},
            ],
        },
    ]
    result = analyze_coa_wargame(coa_list, objective="OPFOR 격멸")
    assert result["status"] == "success", f"분석 실패: {result}"
    assert len(result["evaluated"]) == 2, "2개 COA가 평가되어야 함"
    assert result["recommended_coa"] in ("COA-1", "COA-2"), "권장 COA가 지정되어야 함"
    return f"recommended={result['recommended_coa']}, scores={[(e['coa_id'], e['score']) for e in result['evaluated']]}"


def test_coa_analysis_empty():
    setup_engine()
    from tools.coa_analysis_tool import analyze_coa_wargame
    result = analyze_coa_wargame([], objective="test")
    assert result["status"] == "error", "빈 coa_list는 오류여야 함"
    return f"message={result['message']}"


def test_coa_analysis_recon_bonus():
    setup_engine()
    from tools.coa_analysis_tool import analyze_coa_wargame
    coa_with_recon = [{
        "coa_id": "R1",
        "name": "정찰 포함",
        "mission_plans": [
            {"company_id": "Delta", "mission_type": "recon", "waypoints": [[10000, 10000]], "objective": "정찰"},
        ],
    }]
    coa_no_recon = [{
        "coa_id": "A1",
        "name": "정찰 없음",
        "mission_plans": [
            {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[20000, 20000]], "objective": "공격"},
        ],
    }]
    r1 = analyze_coa_wargame(coa_with_recon)
    a1 = analyze_coa_wargame(coa_no_recon)
    r_score = r1["evaluated"][0]["score"]
    a_score = a1["evaluated"][0]["score"]
    # 미탐지 OPFOR(Red2, Red3)가 있으므로 정찰이 있는 COA가 더 높아야 함
    assert r_score > a_score, f"정찰 포함 COA 점수({r_score})가 미포함({a_score})보다 높아야 함"
    return f"recon_score={r_score}, attack_score={a_score}"


# ── get_wargame_engine_status ───────────────────────────

def test_engine_status():
    setup_engine()
    from tools.wargame_mission_tool import get_wargame_engine_status
    result = get_wargame_engine_status()
    assert result["status"] == "success", f"엔진 상태 조회 실패: {result}"
    assert "running" in result
    assert "tick" in result
    return f"running={result['running']}, tick={result['tick']}"


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

ALL_CASES = [
    # validate_mission_plan
    ("validate/valid_plan", test_validate_valid_plan),
    ("validate/invalid_company", test_validate_invalid_company),
    ("validate/invalid_mission_type", test_validate_invalid_mission_type),
    ("validate/out_of_bounds_waypoint", test_validate_out_of_bounds_waypoint),
    ("validate/recon_attack_warning", test_validate_recon_attack_warning),
    ("validate/empty_mission_plans", test_validate_empty_mission_plans),
    # pending_plan / confirmation gate
    ("gate/pending_save_retrieve", test_pending_plan_save_and_retrieve),
    ("gate/approve_success", test_approve_plan_success),
    ("gate/approve_wrong_id", test_approve_wrong_plan_id),
    ("gate/guard_no_pending", test_guard_write_tool_no_pending),
    ("gate/guard_not_approved", test_guard_write_tool_not_approved),
    ("gate/guard_approved_passes", test_guard_write_tool_approved_passes),
    # apply_wargame_mission_plan
    ("apply/dry_run_default", test_apply_dry_run_default),
    ("apply/blocked_without_approval", test_apply_dry_run_blocked_without_approval),
    ("apply/success_after_approval", test_apply_success_after_approval),
    # apply_wargame_air_support
    ("air/dry_run", test_air_support_dry_run),
    ("air/invalid_type", test_air_support_invalid_type),
    # classify_intent
    ("intent/execution_request", test_intent_execution_request),
    ("intent/recon_planning", test_intent_recon_planning),
    ("intent/attack_planning", test_intent_attack_planning),
    ("intent/video_query", test_intent_video_query),
    ("intent/situation_query", test_intent_situation_query),
    ("intent/no_confirmation_for_recon", test_intent_no_confirmation_for_recon),
    # COA 분석
    ("coa/basic", test_coa_analysis_basic),
    ("coa/empty", test_coa_analysis_empty),
    ("coa/recon_bonus", test_coa_analysis_recon_bonus),
    # 엔진 상태
    ("engine/status", test_engine_status),
]


def main():
    parser = argparse.ArgumentParser(description="C2 Tool Trace Eval")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-k", "--filter", default="", help="케이스 이름 필터")
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
    # 프로젝트 루트를 sys.path에 추가
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, project_root)

    # smolagents가 없는 환경에서도 실행 가능하도록 stub 등록
    try:
        import smolagents  # noqa: F401
    except ModuleNotFoundError:
        from unittest.mock import MagicMock

        def _noop_tool(fn):
            return fn

        smolagents_stub = MagicMock()
        smolagents_stub.tool = _noop_tool
        sys.modules["smolagents"] = smolagents_stub

    # tools 패키지 __init__.py 실행 방지: 직접 모듈 파일을 임포트하도록
    # tools/__init__.py 가 smolagents를 임포트하므로, 패키지 경로만 등록
    import importlib
    import types

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = [os.path.join(project_root, "tools")]
    tools_pkg.__package__ = "tools"
    sys.modules["tools"] = tools_pkg

    main()
