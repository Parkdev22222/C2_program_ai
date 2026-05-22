"""
워게임 시뮬레이터 임무계획 실행 도구 (smolagents Tool)
"""
import json
import logging
import math
from smolagents import tool
from tools.coord_utils import is_latlon_coords, waypoints_latlon_to_xy, latlon_to_xy

logger = logging.getLogger(__name__)

_wargame_engine = None
# UI 버튼이 임무계획 수립을 위해 시뮬레이션을 정지한 경우 True.
# apply_wargame_mission_plan(dry_run=False) 성공 시 자동으로 재개한다.
_resume_on_apply: bool = False


def register_wargame_engine(engine):
    global _wargame_engine
    _wargame_engine = engine


def set_resume_on_apply(flag: bool) -> None:
    """UI 버튼 핸들러가 시뮬레이션을 일시정지했을 때 True로 설정."""
    global _resume_on_apply
    _resume_on_apply = flag


# ── 공중지원 목표 좌표 스냅 헬퍼 ────────────────────────────────────
_SNAP_MAX_DIST = 4_000.0   # 이 거리 이내의 탐지 적군 좌표로 스냅 (m)


def _snap_air_targets_to_opfor(air_plans: list) -> tuple:
    """
    air_support_plans 각 항목의 target을 가장 가까운 탐지 OPFOR 정확 좌표로 스냅.

    Returns:
        (snapped_plans, snap_log, errors)
        snapped_plans: target이 교정된 계획 리스트
        snap_log: 교정 내역 문자열 리스트 (로깅용)
        errors: 스냅 불가(탐지 OPFOR 없거나 너무 멀어서 거부) 항목 설명 리스트
    """
    if _wargame_engine is None:
        return air_plans, [], ["엔진 미초기화 — 스냅 불가"]

    try:
        state = _wargame_engine.get_state()
        intel = state.get("intelligence", {}).get("BLUFOR", [])
        # detected 우선, 없으면 approximate도 허용
        detected = [e for e in intel if e["status"] == "detected"]
        approx   = [e for e in intel if e["status"] == "approximate"]
        opfor_pool = detected if detected else approx
    except Exception as e:
        return air_plans, [], [f"엔진 상태 조회 실패: {e}"]

    if not opfor_pool:
        return air_plans, [], ["탐지된 OPFOR가 없어 공중지원 목표를 지정할 수 없음"]

    snapped = []
    snap_log = []
    errors = []

    for asp in air_plans:
        asp = dict(asp)
        raw_target = asp.get("target", [])
        if len(raw_target) != 2:
            errors.append(f"{asp.get('call_sign','?')}: target 형식 오류 {raw_target}")
            snapped.append(asp)
            continue

        t0, t1 = float(raw_target[0]), float(raw_target[1])
        # lat/lon 형식이면 먼저 미터로 변환
        if -90.0 <= t0 <= 90.0 and t0 != round(t0):
            t0, t1 = latlon_to_xy(t0, t1)
            asp["target"] = [t0, t1]
        tx, ty = t0, t1

        # 가장 가까운 탐지 OPFOR 찾기
        nearest = min(
            opfor_pool,
            key=lambda e: math.hypot(e["known_x"] - tx, e["known_y"] - ty),
        )
        dist = math.hypot(nearest["known_x"] - tx, nearest["known_y"] - ty)

        if dist > _SNAP_MAX_DIST:
            errors.append(
                f"{asp.get('call_sign','?')}: 목표 ({int(tx)},{int(ty)})가 "
                f"가장 가까운 탐지 적군 {nearest['unit_id']} 으로부터 {int(dist)}m 이상 — "
                f"탐지된 적군 좌표를 사용하세요."
            )
            snapped.append(asp)
            continue

        # 정확한 OPFOR 좌표로 교정
        exact_x = int(nearest["known_x"])
        exact_y = int(nearest["known_y"])
        if exact_x != int(tx) or exact_y != int(ty):
            snap_log.append(
                f"{asp.get('call_sign','?')} target ({int(tx)},{int(ty)}) "
                f"→ {nearest['unit_id']} 정확 좌표 ({exact_x},{exact_y}) 으로 교정 "
                f"(오차 {int(dist)}m)"
            )
        asp["target"] = [exact_x, exact_y]
        asp["_snapped_to"] = nearest["unit_id"]
        snapped.append(asp)

    return snapped, snap_log, errors


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

        # ── waypoint 좌표 자동 감지: lat/lon → 미터 변환 ──────────────
        plan = dict(plan)
        converted_mission_plans = []
        for mp in mission_plans:
            mp = dict(mp)
            wps = mp.get("waypoints", [])
            if wps and is_latlon_coords(wps):
                mp["waypoints"] = waypoints_latlon_to_xy(wps)
                logger.info(
                    f"[좌표변환] {mp.get('company_id','?')} waypoints 위경도→미터 변환 완료"
                )
            converted_mission_plans.append(mp)
        plan["mission_plans"] = converted_mission_plans

        # air_support target 좌표도 lat/lon이면 미터로 변환
        if plan.get("air_support_plans"):
            converted_air = []
            for asp in plan["air_support_plans"]:
                asp = dict(asp)
                target = asp.get("target", [])
                if len(target) == 2:
                    t0, t1 = float(target[0]), float(target[1])
                    # lat 범위(소수) 판별
                    if -90.0 <= t0 <= 90.0 and t0 != round(t0):
                        x_m, y_m = latlon_to_xy(t0, t1)
                        asp["target"] = [x_m, y_m]
                        logger.info(
                            f"[좌표변환] {asp.get('call_sign','?')} target 위경도→미터 변환"
                        )
                converted_air.append(asp)
            plan["air_support_plans"] = converted_air

        # Re-read mission_plans after conversion (for skipped check below)
        mission_plans = plan.get("mission_plans", [])

        # ── 공중지원 목표 좌표 강제 교정 (탐지 OPFOR 정확 좌표로 스냅) ──
        air_snap_log, air_snap_errors = [], []
        if plan.get("air_support_plans"):
            snapped_air, air_snap_log, air_snap_errors = _snap_air_targets_to_opfor(
                plan["air_support_plans"]
            )
            plan["air_support_plans"] = snapped_air
            for msg in air_snap_log:
                logger.info(f"[공중지원 좌표교정] {msg}")
            for msg in air_snap_errors:
                logger.warning(f"[공중지원 좌표오류] {msg}")

        _wargame_engine.apply_mission_plan(plan)

        try:
            from tools.mission_plan_validator import clear_pending_plan
            clear_pending_plan()
        except Exception:
            pass

        # 임무계획 버튼이 시뮬레이션을 정지한 경우 여기서 재개
        global _resume_on_apply
        if _resume_on_apply and not _wargame_engine.running:
            _wargame_engine.start()
            _resume_on_apply = False
            logger.info("시뮬레이션 재개 — apply_wargame_mission_plan 적용 완료")

        applied = len(mission_plans) - len(skipped)
        logger.info(f"임무계획 적용: {applied}개 부대, 건너뚁: {skipped}")
        result: dict = {
            "status": "success",
            "applied": applied,
            "skipped": skipped,
            "message": f"{applied}개 부대에 임무계획 적용 완료." + (
                f" (건너뚁: {skipped})" if skipped else ""
            ),
        }
        if air_snap_log:
            result["air_target_corrections"] = air_snap_log
        if air_snap_errors:
            result["air_target_warnings"] = air_snap_errors
        return result
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

    # ── 목표 좌표를 탐지 OPFOR 정확 좌표로 스냅 (dry_run 전에 선행) ──
    snapped_plans, snap_log, snap_errors = _snap_air_targets_to_opfor(support_plans)
    for msg in snap_log:
        logger.info(f"[공중지원 좌표교정] {msg}")
    for msg in snap_errors:
        logger.warning(f"[공중지원 좌표오류] {msg}")
    plan = dict(plan)
    plan["air_support_plans"] = snapped_plans
    support_plans = snapped_plans

    errors = list(snap_errors)   # 스냅 실패도 오류로 취급
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
        "target_corrections": snap_log,
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
                + (f"좌표교정: {len(snap_log)}건\n" if snap_log else "")
                + f"실제 적용하려면 사용자 승인 후 dry_run=False로 실행하세요."
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
        result: dict = {
            "status": "success",
            "registered": len(support_plans),
            "message": f"{len(support_plans)}건의 공중지원 요청이 등록되었습니다.",
        }
        if snap_log:
            result["target_corrections"] = snap_log
        return result
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
