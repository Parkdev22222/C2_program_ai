"""
워게임 시뮬레이터 임무계획 실행 도구 (smolagents Tool)
"""
import json
import logging
from smolagents import tool

logger = logging.getLogger(__name__)

_wargame_engine = None


def register_wargame_engine(engine):
    global _wargame_engine
    _wargame_engine = engine


@tool
def apply_wargame_mission_plan(plan_json: str, dry_run: bool = True) -> dict:
    """
    JSON 형태의 BLUFOR 임무계획을 워게임 시뮬레이터에 적용합니다.
    기본값 dry_run=True: 실제 적용 없이 검증 결과만 반환합니다.

    Args:
        plan_json: JSON 문자열.
            {
              "plan_id": "plan_abc123",
              "mission_plans": [
                {
                  "company_id": "Alpha",
                  "mission_type": "attack",
                  "waypoints": [[x, y], ...],
                  "objective": "Red1 격멸"
                }
              ]
            }
        dry_run: True이면 검증만 수행 (기본값). False이면 실제 적용.

    Returns:
        dry_run=True 시: {"status": "dry_run", "valid": bool, "plan_id": str, ...}
        dry_run=False 시: {"status": "success"|"blocked"|"error", ...}
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        plan = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"JSON 파싱 실패: {e}"}

    try:
        from tools.mission_plan_validator import (
            validate_mission_plan, save_pending_plan, guard_write_tool,
        )
        validation = validate_mission_plan(plan)
    except ImportError:
        validation = {"ok": True, "errors": [], "warnings": [], "summary": "validator 미로드"}

    if dry_run:
        plan_id = save_pending_plan(plan, validation)
        mission_plans = plan.get("mission_plans", [])
        summary_lines = [
            f"  • {mp.get('company_id', '?')} → {mp.get('mission_type', '?')}: {mp.get('objective', '')}"
            for mp in mission_plans
        ]
        return {
            "status": "dry_run",
            "valid": validation["ok"],
            "validation": validation,
            "plan_id": plan_id,
            "would_apply": plan,
            "plan_summary": "\n".join(summary_lines),
            "message": (
                f"[DRY RUN] 검증만 수행했습니다. 실제 워게임 상태는 변경되지 않았습니다.\n"
                f"검증 결과: {validation['summary']}\n"
                f"실제 적용하려면 사용자가 plan_id='{plan_id}'를 승인한 후 실행하세요."
            ),
        }

    try:
        gate = guard_write_tool("apply_wargame_mission_plan", {"plan_json": plan_json})
    except Exception:
        gate = {"allowed": True}

    if not gate.get("allowed", True):
        return {
            "status": "blocked",
            "reason": gate.get("reason"),
            "message": gate.get("message", "실행이 차단되었습니다."),
        }

    if not validation.get("ok", False):
        return {
            "status": "blocked",
            "reason": "validation_failed",
            "validation": validation,
            "message": f"검증 실패 — 실행 불가: {validation.get('summary')}",
        }

    try:
        mission_plans = plan.get("mission_plans", [])
        if not mission_plans:
            return {"status": "error", "message": "mission_plans 필드가 없거나 비어 있습니다."}

        state = _wargame_engine.get_state()
        valid_ids = {u["id"] for u in state.get("units", []) if u["side"] == "BLUFOR"}
        skipped = [mp["company_id"] for mp in mission_plans
                   if mp.get("company_id", "") not in valid_ids]

        _wargame_engine.apply_mission_plan(plan)

        try:
            from tools.mission_plan_validator import clear_pending_plan
            clear_pending_plan()
        except Exception:
            pass

        applied = len(mission_plans) - len(skipped)
        logger.info(f"임무계획 적용: {applied}개 부대, 건너뚁: {skipped}")
        return {
            "status": "success",
            "applied": applied,
            "skipped": skipped,
            "message": f"{applied}개 부대에 임무계획 적용 완료." + (
                f" (건너뚁: {skipped})" if skipped else ""
            ),
        }
    except Exception as e:
        logger.error(f"apply_wargame_mission_plan error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@tool
def apply_wargame_air_support(support_json: str, dry_run: bool = True) -> dict:
    """
    JSON 형태의 공중지원 계획을 워게임 시뮬레이터에 적용합니다.

    Args:
        support_json: JSON 문자열.
            {"air_support_plans": [{"call_sign": "VIPER-1", "support_type": "cas", "target": [x, y], "radius": 1500, "delay": 60}]}
        dry_run: True이면 검증만 (기본값). False이면 실제 적용.

    Returns:
        dry_run=True 시: {"status": "dry_run", "valid": bool, ...}
        dry_run=False 시: {"status": "success"|"blocked"|"error", ...}
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        plan = json.loads(support_json) if isinstance(support_json, str) else support_json
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"JSON 파싱 실패: {e}"}

    support_plans = plan.get("air_support_plans", [])
    errors = []
    warnings = []
    valid_types = {"cas", "strike", "artillery", "helicopter"}
    for asp in support_plans:
        if asp.get("support_type") not in valid_types:
            errors.append(f"허용되지 않은 support_type: {asp.get('support_type')}")
        target = asp.get("target", [])
        if len(target) != 2:
            errors.append(f"target 형식 오류: {target}")
        radius = asp.get("radius", 0)
        if radius <= 0 or radius > 10_000:
            warnings.append(f"radius 비정상: {radius}m")
    validation = {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "summary": "통과" if not errors else f"오류 {len(errors)}건",
    }

    if dry_run:
        return {
            "status": "dry_run",
            "valid": validation["ok"],
            "validation": validation,
            "would_apply": plan,
            "message": (
                f"[DRY RUN] 공중지원 검증만 수행했습니다.\n"
                f"검증 결과: {validation['summary']}\n"
                f"실제 적용하려면 사용자 승인 후 dry_run=False로 실행하세요."
            ),
        }

    try:
        from tools.mission_plan_validator import guard_write_tool
        gate = guard_write_tool("apply_wargame_air_support", {"support_json": support_json})
    except Exception:
        gate = {"allowed": True}

    if not gate.get("allowed", True):
        return {
            "status": "blocked",
            "reason": gate.get("reason"),
            "message": gate.get("message", "실행이 차단되었습니다."),
        }

    if not validation["ok"]:
        return {
            "status": "blocked",
            "reason": "validation_failed",
            "validation": validation,
            "message": f"검증 실패 — 실행 불가: {validation['summary']}",
        }

    try:
        if not support_plans:
            return {"status": "error", "message": "air_support_plans 필드가 없거나 비어 있습니다."}
        _wargame_engine.apply_air_support_plan(plan)
        logger.info(f"공중지원 등록: {len(support_plans)}건")
        return {
            "status": "success",
            "registered": len(support_plans),
            "message": f"{len(support_plans)}건의 공중지원 요청이 등록되었습니다.",
        }
    except Exception as e:
        logger.error(f"apply_wargame_air_support error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@tool
def get_wargame_engine_status() -> dict:
    """
    워게임 시뮬레이터 엔진 상태를 반환합니다.

    Returns:
        {"status": str, "running": bool, "game_time": str, "tick": int, "time_scale": int, "winner": str|null, "air_supports_active": int}
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        state = _wargame_engine.get_state()
        active_air = sum(1 for a in state.get("air_supports", []) if a["status"] == "active")
        return {
            "status": "success",
            "running": _wargame_engine.running,
            "game_time": state.get("game_time_str", "00:00:00"),
            "tick": state.get("tick", 0),
            "time_scale": int(_wargame_engine.time_scale),
            "winner": state.get("winner"),
            "air_supports_active": active_air,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
