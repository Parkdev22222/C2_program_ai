"""
임무계획 검증기 - confirmation gate + pending plan 세션 + intent 분류

구조:
- guard_write_tool(): apply 계열 실행 전 confirmation gate
- pending_plan 세션 상태 관리 (save_pending_plan / get_pending_plan / approve_plan)
- classify_intent(): 사용자 쿼리 의도 분류
- update_valid_company_ids(): 시나리오별 company_id allow-list 동적 갱신

Pydantic typed schema(Waypoint/MissionPlanItem/AirSupportItem/MissionPlanRequest)와
MAP_MAX/validate_mission_plan()은 순수 값 객체 검증 로직으로
c2.domain.planning.mission_plan 으로 이동했다. 아래는 하위호환 shim import.
"""
import uuid
import logging
from typing import Optional

from c2.domain.planning import mission_plan as _mission_plan_domain
from c2.domain.planning.mission_plan import (  # noqa: F401  [shim]
    MAP_MAX,
    VALID_MISSION_TYPES,
    VALID_SUPPORT_TYPES,
    validate_mission_plan,
)
try:
    from c2.domain.planning.mission_plan import (  # noqa: F401  [shim]
        Waypoint,
        MissionPlanItem,
        AirSupportItem,
        MissionPlanRequest,
    )
except ImportError:
    pass

logger = logging.getLogger(__name__)

WRITE_TOOLS = {"apply_wargame_mission_plan", "apply_wargame_air_support"}


def update_valid_company_ids(ids) -> None:
    """VALID_COMPANY_IDS를 시나리오에 맞게 동적 갱신.

    domain 모듈(c2.domain.planning.mission_plan)의 VALID_COMPANY_IDS 전역을
    직접 재할당한다. validate_mission_plan()과 Pydantic validator들이 이
    domain 모듈 전역을 호출 시점에 조회하므로 갱신이 즉시 반영된다.
    """
    _mission_plan_domain.VALID_COMPANY_IDS = set(ids)


# 하위호환: 과거 `tools.mission_plan_validator.VALID_COMPANY_IDS`를 직접
# 참조하던 코드를 위한 property-like 접근. 모듈 속성으로는 갱신 시점의
# 스냅샷이 아니라 항상 domain 모듈의 최신 값을 가리키도록 __getattr__ 사용.
def __getattr__(name):
    if name == "VALID_COMPANY_IDS":
        return _mission_plan_domain.VALID_COMPANY_IDS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ─────────────────────────────────────────────
# Pending Plan 세션 상태
# ─────────────────────────────────────────────
_session_state: dict = {
    "pending_plan": None,
    "approved_plan_id": None,
    "last_validation": None,
    "user_confirmed": False,
}


def save_pending_plan(plan: dict, validation: dict) -> str:
    """임무계획을 pending 상태로 저장하고 plan_id를 반환합니다."""
    plan_id = plan.get("plan_id") or f"plan_{uuid.uuid4().hex[:8]}"
    plan["plan_id"] = plan_id
    _session_state["pending_plan"] = plan
    _session_state["last_validation"] = validation
    _session_state["approved_plan_id"] = None
    _session_state["user_confirmed"] = False
    logger.info(f"Pending plan saved: {plan_id}")
    return plan_id


def get_pending_plan() -> Optional[dict]:
    """현재 pending_plan을 반환합니다."""
    return _session_state.get("pending_plan")


def approve_plan(plan_id: str) -> dict:
    """사용자가 plan_id를 승인합니다."""
    pending = _session_state.get("pending_plan")
    if pending is None:
        return {"ok": False, "message": "승인할 pending_plan이 없습니다."}
    if pending.get("plan_id") != plan_id:
        return {
            "ok": False,
            "message": f"plan_id 불일치: 현재 pending={pending.get('plan_id')}, 요청={plan_id}",
        }
    validation = _session_state.get("last_validation", {})
    if not validation.get("ok", False):
        return {
            "ok": False,
            "message": "검증 실패 계획은 승인할 수 없습니다.",
            "validation": validation,
        }
    _session_state["approved_plan_id"] = plan_id
    _session_state["user_confirmed"] = True
    logger.info(f"Plan approved: {plan_id}")
    return {"ok": True, "plan_id": plan_id, "message": f"{plan_id} 승인 완료"}


def clear_pending_plan():
    """pending plan 상태를 초기화합니다."""
    _session_state["pending_plan"] = None
    _session_state["approved_plan_id"] = None
    _session_state["last_validation"] = None
    _session_state["user_confirmed"] = False


def get_session_state() -> dict:
    return dict(_session_state)


# ─────────────────────────────────────────────
# guard_write_tool — apply 실행 전 gate
# ─────────────────────────────────────────────

def guard_write_tool(tool_name: str, args: dict) -> dict:
    """
    apply 계열 실행 도구 호출 직전에 통과해야 하는 gate.

    Returns:
        {"allowed": True} or {"allowed": False, "reason": str, "message": str}
    """
    if tool_name not in WRITE_TOOLS:
        return {"allowed": True}

    pending_plan = _session_state.get("pending_plan")
    approved_plan_id = _session_state.get("approved_plan_id")

    if not pending_plan:
        return {
            "allowed": False,
            "reason": "no_pending_plan",
            "message": (
                "실행 가능한 pending_plan이 없습니다. "
                "먼저 임무계획을 생성하고 사용자 승인을 받으세요."
            ),
        }

    if approved_plan_id != pending_plan.get("plan_id"):
        return {
            "allowed": False,
            "reason": "confirmation_required",
            "message": (
                f"사용자 승인이 필요합니다. "
                f"plan_id '{pending_plan.get('plan_id')}'를 승인한 후 실행하세요."
            ),
        }

    validation = _session_state.get("last_validation") or {}
    if not validation.get("ok", False):
        return {
            "allowed": False,
            "reason": "validation_failed",
            "message": "검증을 통과하지 못한 계획은 실행할 수 없습니다.",
            "validation": validation,
        }

    return {"allowed": True}


# ─────────────────────────────────────────────
# classify_intent — intent router
# ─────────────────────────────────────────────

def classify_intent(query: str) -> dict:
    """
    사용자 쿼리의 의도를 분류합니다.

    Returns:
        {
          "intent": str,
          "requires_confirmation": bool,
          "preferred_tools": list[str]
        }
    """
    q = query.lower()

    if any(k in q for k in ["적용", "실행", "반영", "apply", "확정"]):
        return {
            "intent": "execution_request",
            "requires_confirmation": True,
            "preferred_tools": ["apply_wargame_mission_plan"],
        }

    if any(k in q for k in ["정찰", "탐지", "recon", "reconnaissance", "위치 확인", "감시"]):
        return {
            "intent": "recon_planning",
            "requires_confirmation": False,
            "preferred_tools": [
                "get_wargame_situation",
                "assess_recon_need",
                "recommend_recon_routes",
            ],
        }

    if any(k in q for k in ["공격", "타격", "격멸", "attack", "assault", "strike"]):
        return {
            "intent": "attack_planning",
            "requires_confirmation": False,
            "preferred_tools": [
                "get_wargame_situation",
                "assess_recon_need",
                "get_optimal_attack_positions",
            ],
        }

    if any(k in q for k in ["현황", "상태", "상황", "부대", "전력", "situation", "status"]):
        return {
            "intent": "situation_query",
            "requires_confirmation": False,
            "preferred_tools": [
                "get_wargame_situation",
                "get_wargame_unit_detail",
                "get_intelligence_report",
            ],
        }

    if any(k in q for k in [
        "전략", "전술", "작전", "기동", "화력", "포위", "기습", "매복", "침투",
        "방어", "돌격", "strategy", "tactics", "maneuver", "coa", "course of action",
    ]):
        return {
            "intent": "general_strategy_advice",
            "requires_confirmation": False,
            "preferred_tools": [
                "get_wargame_situation",
                "get_wargame_tactical_recommendation",
            ],
        }

    if any(k in q for k in ["계획", "추천", "제안", "검토", "plan", "recommend", "suggest", "review"]):
        return {
            "intent": "planning_request",
            "requires_confirmation": False,
            "preferred_tools": ["get_wargame_tactical_recommendation", "analyze_coa_wargame"],
        }

    return {
        "intent": "general",
        "requires_confirmation": False,
        "preferred_tools": [],
    }
