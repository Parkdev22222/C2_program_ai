"""
워게임 시뮬레이터 임무계획 실행 도구 (smolagents Tool)
"""
import json
import logging
import math
from c2.domain.wargame.coordinates import is_latlon_coords, waypoints_latlon_to_xy, latlon_to_xy

logger = logging.getLogger(__name__)


_wargame_engine = None
_resume_on_apply: bool = False
_last_apply_time: float = 0.0  # apply_wargame_mission_plan 마지막 호출 시각
_last_applied_plan: dict = {}  # 마지막으로 실제 적용된 계획(좌표 변환·스냅 반영본)


def register_wargame_engine(engine):
    global _wargame_engine
    _wargame_engine = engine


def set_resume_on_apply(flag: bool) -> None:
    global _resume_on_apply
    _resume_on_apply = flag


def reset_apply_tracker() -> None:
    """자동 재계획 세션 시작 시 호출 — 이전 apply 기록 초기화."""
    global _last_apply_time, _last_applied_plan
    _last_apply_time = 0.0
    _last_applied_plan = {}


def was_plan_applied_since(since: float) -> bool:
    """since 이후 apply_wargame_mission_plan이 실제로 호출됐는지 확인."""
    return _last_apply_time > since


def get_last_applied_plan() -> dict:
    """에이전트가 apply 툴로 직접 적용한 마지막 계획을 반환(표시용). 없으면 빈 dict."""
    return dict(_last_applied_plan) if _last_applied_plan else {}


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
                  "company_id": "보병1중대",
                  "mission_type": "attack",
                  "waypoints": [[x, y], ...],
                  "objective": "적보병1중대 격멸"
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
        from c2.domain.planning.mission_plan import validate_mission_plan
        from c2.application.planning.mission_session import (
            save_pending_plan, guard_write_tool,
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
            "message": (
                f"검증 실패 — 실행 불가: {validation.get('summary')}. "
                f"오류: {validation.get('errors')}. "
                "위 오류를 수정한 mission_plans 를 다시 생성해 "
                "apply_wargame_mission_plan(dry_run=false) 로 재호출하세요."
            ),
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

        # 공중지원 계획도 함께 엔진에 등록
        if plan.get("air_support_plans"):
            _wargame_engine.apply_air_support_plan(plan)
            logger.info(f"[공중지원] {len(plan['air_support_plans'])}회 엔진 등록 완료")

        try:
            from c2.application.planning.mission_session import clear_pending_plan
            clear_pending_plan()
        except Exception:
            pass

        # 적용 시각 기록 (자동 재계획에서 폴백 여부 판단에 사용)
        import time as _t_apply
        global _last_apply_time, _last_applied_plan
        _last_apply_time = _t_apply.time()
        # 적용된 계획(좌표 변환·스냅 반영본) 보관 → UI 표시용
        _last_applied_plan = plan

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

        # 성공 결과를 그대로 반환한다.
        # (과거 _raise_final_answer(result)로 smolagents CodeAgent를 조기 종료시켰으나,
        #  smolagents 버전에 따라 FinalAnswerException이 에이전트 루프에 안 잡히고 밖으로
        #  새어나와(BaseException 계열) 트레이스백/중단을 유발 → 제거. 두 백엔드 모두
        #  반환 dict를 정상 처리한다.)
        return result
    except Exception as e:
        logger.error(f"apply_wargame_mission_plan error: {e}", exc_info=True)
        return {
            "status": "error",
            "message": (
                f"임무계획 적용 중 오류: {e}. 오류 원인을 수정한 mission_plans 를 "
                "다시 생성해 apply_wargame_mission_plan(dry_run=false) 로 재호출하세요. "
                "(좌표는 미터 정수 0~30000, company_id 는 실제 BLUFOR 부대 ID)"
            ),
        }



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

    # 승인 게이트(guard_write_tool)를 두지 않는다 — apply_wargame_mission_plan 과 동일하게
    # dry_run=False 시 즉시 적용한다. (기존 게이트는 pending_plan 승인을 요구해 공중지원이
    # 항상 'pending_plan 없음'으로 차단 → LLM 이 '시스템 문제로 적용 실패'로 보고하던 버그)
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
