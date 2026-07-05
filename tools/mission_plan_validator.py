"""
임무계획 검증기 - Pydantic typed schema + validator + confirmation gate

구조:
- Waypoint, MissionPlanItem, AirSupportItem, MissionPlanRequest: typed schema
- validate_mission_plan(): 검증 로직 (error/warning 분리)
- guard_write_tool(): apply 계열 실행 전 confirmation gate
- pending_plan 세션 상태 관리 (save_pending_plan / get_pending_plan / approve_plan)
"""
import uuid
import logging
from typing import Optional, List, Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 맵 상수
# ─────────────────────────────────────────────
MAP_MAX = 30_000.0
VALID_COMPANY_IDS = {"Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"}
VALID_MISSION_TYPES = {"recon", "attack", "defend", "flank", "withdraw", "hold"}


def update_valid_company_ids(ids) -> None:
    """VALID_COMPANY_IDS를 시나리오에 맞게 동적 갱신."""
    global VALID_COMPANY_IDS
    VALID_COMPANY_IDS = set(ids)
VALID_SUPPORT_TYPES = {"cas", "strike", "artillery", "helicopter"}
WRITE_TOOLS = {"apply_wargame_mission_plan", "apply_wargame_air_support"}

# ─────────────────────────────────────────────
# Pydantic 기반 typed schema (v1/v2 호환)
# ─────────────────────────────────────────────
try:
    from pydantic import BaseModel, Field, validator

    class Waypoint(BaseModel):
        """위경도(WGS84) 또는 내부 미터 좌표를 모두 허용하는 경유지 모델.
        lat/lon 형식: lat=-90~90, lon=-180~180 (소수점 값)
        미터 형식:    x=0~30000, y=0~30000 (정수 또는 소수)
        """
        x: float = Field(ge=-180, le=MAP_MAX)   # lon 또는 x_m 모두 허용
        y: float = Field(ge=-90, le=MAP_MAX)    # lat 또는 y_m 모두 허용

    class MissionPlanItem(BaseModel):
        company_id: str
        mission_type: str
        waypoints: List[Waypoint]
        objective: str

        @validator("company_id")
        def _check_company_id(cls, v):
            if v not in VALID_COMPANY_IDS:
                raise ValueError(f"company_id '{v}'는 허용 부대({VALID_COMPANY_IDS})가 아닙니다.")
            return v

        @validator("mission_type")
        def _check_mission_type(cls, v):
            if v not in VALID_MISSION_TYPES:
                raise ValueError(f"mission_type '{v}'는 허용 값({VALID_MISSION_TYPES})이 아닙니다.")
            return v

        @validator("waypoints", pre=True)
        def _coerce_waypoints(cls, v):
            # [lat/lon 또는 x, y] 리스트 형식을 {"x": x, "y": y} 딕셔너리로 자동 변환
            coerced = []
            for wp in v:
                if isinstance(wp, (list, tuple)) and len(wp) == 2:
                    coerced.append({"x": wp[0], "y": wp[1]})
                else:
                    coerced.append(wp)
            if not coerced:
                raise ValueError("waypoints는 최소 1개 이상이어야 합니다.")
            return coerced

    class AirSupportItem(BaseModel):
        call_sign: str
        support_type: str
        target: List[float]
        radius: float = Field(gt=0, le=10_000)
        delay: float = Field(ge=0, le=3600)

        @validator("support_type")
        def _check_support_type(cls, v):
            if v not in VALID_SUPPORT_TYPES:
                raise ValueError(f"support_type '{v}'는 허용 값({VALID_SUPPORT_TYPES})이 아닙니다.")
            return v

        @validator("target")
        def _check_target(cls, v):
            if len(v) != 2:
                raise ValueError("target은 [lat, lon] 또는 [x, y] 2개 좌표여야 합니다.")
            # lat/lon 또는 미터 좌표 모두 허용 (변환은 apply 단계에서 수행)
            return v

    class MissionPlanRequest(BaseModel):
        plan_id: Optional[str] = None
        mission_plans: List[MissionPlanItem]
        air_support_plans: List[AirSupportItem] = []
        dry_run: bool = True

        @validator("mission_plans")
        def _check_not_empty(cls, v):
            if not v:
                raise ValueError("mission_plans는 비어 있을 수 없습니다.")
            return v

    _PYDANTIC_OK = True
except ImportError:
    _PYDANTIC_OK = False
    logger.warning("pydantic 미설치 — typed schema 검증 비활성화, 기본 dict 검증만 수행합니다.")


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
# validate_mission_plan
# ─────────────────────────────────────────────

def validate_mission_plan(plan: Any) -> dict:
    """
    임무계획을 검증합니다.

    Returns:
        {
          "ok": bool,
          "errors": list[str],
          "warnings": list[str],
          "summary": str
        }
    """
    errors: List[str] = []
    warnings: List[str] = []

    if isinstance(plan, str):
        import json as _json
        try:
            plan = _json.loads(plan)
        except Exception as e:
            return {"ok": False, "errors": [f"JSON 파싱 실패: {e}"], "warnings": [], "summary": "파싱 오류"}

    mission_plans = plan.get("mission_plans", [])
    air_support_plans = plan.get("air_support_plans", [])

    if not mission_plans:
        errors.append("mission_plans가 비어 있습니다.")

    if _PYDANTIC_OK and mission_plans:
        try:
            MissionPlanRequest(
                plan_id=plan.get("plan_id"),
                mission_plans=mission_plans,
                air_support_plans=air_support_plans,
            )
        except Exception as e:
            errors.append(f"Schema 검증 실패: {e}")

    seen_companies: set = set()
    has_recon = False
    has_attack = False

    for mp in mission_plans:
        cid = mp.get("company_id", "")
        mtype = mp.get("mission_type", "")
        wps = mp.get("waypoints", [])

        if cid not in VALID_COMPANY_IDS:
            errors.append(f"허용되지 않은 company_id: '{cid}'")

        if mtype not in VALID_MISSION_TYPES:
            errors.append(f"허용되지 않은 mission_type: '{mtype}' (부대: {cid})")

        if cid in seen_companies:
            warnings.append(f"동일 부대({cid})에 중복 임무가 있습니다.")
        seen_companies.add(cid)

        for i, wp in enumerate(wps):
            if isinstance(wp, (list, tuple)) and len(wp) == 2:
                x, y = wp
            elif isinstance(wp, dict):
                x, y = wp.get("x", wp.get("lon", 0)), wp.get("y", wp.get("lat", 0))
            else:
                errors.append(f"{cid} waypoint[{i}] 형식 오류: {wp}")
                continue
            fx, fy = float(x), float(y)
            # 위경도 형식 (lat: -90~90 소수, lon: -180~180 소수) 또는 미터 형식 (0~30000) 허용
            is_latlon = (-90.0 <= fy <= 90.0 and -180.0 <= fx <= 180.0
                         and (fy != round(fy) or fx != round(fx)))
            if not is_latlon and not (0 <= fx <= MAP_MAX and 0 <= fy <= MAP_MAX):
                errors.append(f"{cid} waypoint[{i}] 좌표 범위 초과: ({x}, {y})")

        if mtype == "recon":
            has_recon = True
        if mtype in ("attack", "flank"):
            has_attack = True

    if has_recon and has_attack:
        warnings.append("정찰 임무와 공격 임무가 혼재합니다. 정찰 완료 후 공격 권장.")

    for asp in air_support_plans:
        stype = asp.get("support_type", "")
        target = asp.get("target", [])
        radius = asp.get("radius", 0)
        delay = asp.get("delay", 0)

        if stype not in VALID_SUPPORT_TYPES:
            errors.append(f"허용되지 않은 support_type: '{stype}'")

        if len(target) != 2:
            errors.append(f"공중지원 target 형식 오류: {target}")
        else:
            tx, ty = target
            ftx, fty = float(tx), float(ty)
            # 위경도 형식 또는 미터 형식 모두 허용 (변환은 apply 단계에서 수행)
            is_latlon_tgt = (-90.0 <= fty <= 90.0 and -180.0 <= ftx <= 180.0
                             and (fty != round(fty) or ftx != round(ftx)))
            if not is_latlon_tgt and not (0 <= ftx <= MAP_MAX and 0 <= fty <= MAP_MAX):
                errors.append(f"공중지원 target 좌표 범위 초과: {target}")

        if radius <= 0 or radius > 10_000:
            warnings.append(f"공중지원 radius 비정상: {radius}m")

        if delay < 0 or delay > 3600:
            warnings.append(f"공중지원 delay 비정상: {delay}s")

    ok = len(errors) == 0
    summary_parts = []
    if errors:
        summary_parts.append(f"오류 {len(errors)}건")
    if warnings:
        summary_parts.append(f"경고 {len(warnings)}건")
    summary = "검증 통과" if ok and not warnings else (", ".join(summary_parts) if summary_parts else "통과")

    return {"ok": ok, "errors": errors, "warnings": warnings, "summary": summary}


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
