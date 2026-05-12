import json
from smolagents import tool


@tool
def validate_mission_plan_tool(plan_json: str) -> dict:
    """
    JSON 임무계획의 유효성을 검증합니다. 실제 적용 없이 오류/경고만 반환합니다.

    Args:
        plan_json: JSON 문자열 형태의 임무계획.

    Returns:
        {"ok": bool, "errors": list, "warnings": list, "summary": str}
    """
    from tools.mission_plan_validator import validate_mission_plan
    try:
        plan = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [f"JSON 파싱 실패: {e}"], "warnings": [], "summary": "파싱 오류"}
    return validate_mission_plan(plan)


@tool
def approve_mission_plan_tool(plan_id: str) -> dict:
    """
    사용자가 특정 plan_id의 임무계획을 승인합니다.

    Args:
        plan_id: 승인할 임무계획 ID.

    Returns:
        {"ok": bool, "plan_id": str, "message": str}
    """
    from tools.mission_plan_validator import approve_plan
    return approve_plan(plan_id)


@tool
def get_pending_plan_tool() -> dict:
    """
    현재 승인 대기 중인 pending 임무계획과 세션 상태를 반환합니다.

    Returns:
        {"has_pending": bool, "plan_id": str|null, "approved": bool, "validation_ok": bool, "plan_summary": str}
    """
    from tools.mission_plan_validator import get_session_state
    state = get_session_state()
    pending = state.get("pending_plan")
    validation = state.get("last_validation") or {}
    if not pending:
        return {"has_pending": False, "plan_id": None, "approved": False, "validation_ok": False, "plan_summary": "대기 중인 임무계획이 없습니다."}
    plan_id = pending.get("plan_id")
    approved = state.get("approved_plan_id") == plan_id
    mission_plans = pending.get("mission_plans", [])
    summary_lines = [f"  • {mp.get('company_id', '?')} → {mp.get('mission_type', '?')}: {mp.get('objective', '')}" for mp in mission_plans]
    return {"has_pending": True, "plan_id": plan_id, "approved": approved, "validation_ok": validation.get("ok", False), "plan_summary": "\n".join(summary_lines) if summary_lines else "(임무 없음)"}
