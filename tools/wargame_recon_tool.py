"""
워게임 정찰 임무 도구 (smolagents Tool)

두 가지 도구 제공:
  1. assess_recon_need()       — 정찰 필요 여부 및 탐지 현황 평가
  2. recommend_recon_routes()  — 교전 회피 정찰 경로 생성 (apply_wargame_mission_plan 호환 JSON 반환)
"""
import json
import math
import logging
from typing import List

from smolagents import tool

logger = logging.getLogger(__name__)

_wargame_engine = None

# 정찰부대 탐지 범위(m) — 이 거리에서 관측 포인트 배치
_RECON_DETECT_RANGE = 7_000
# 관측 대기 거리(m) — 교전 범위(4 km) 바깥
_RECON_STANDOFF = 5_000
# 지도 경계 여유(m)
_MAP_W, _MAP_H = 30_000, 30_000
_BORDER = 500


def register_wargame_engine(engine):
    """UI에서 WargameEngine 인스턴스를 등록."""
    global _wargame_engine
    _wargame_engine = engine


# ──────────────────────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────────────────────

def _elevation(x: float, y: float) -> float:
    try:
        from wargame.terrain import terrain as _t
        return _t.elevation(x, y)
    except Exception:
        return 100.0


def _cover(x: float, y: float) -> float:
    try:
        from wargame.terrain import terrain as _t
        return _t.cover_factor(x, y)
    except Exception:
        return 0.3


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _best_obs_point(
    target_x: float,
    target_y: float,
    angle: float,
    standoff: float,
) -> List[float]:
    """
    목표 주변 지정 각도·거리에서 고도+엄폐+전술메모리 기준 최적 관측 포인트를 반환.
    standoff±1km 범위에서 3개 후보 중 최적을 선택.
    """
    # 전술 메모리 로드 (실패해도 기본 동작)
    try:
        from wargame.harness.tactical_memory import get_tactical_memory
        tm = get_tactical_memory()
    except Exception:
        tm = None

    best_score, best_pt = -1.0, None
    for r in (standoff - 1_000, standoff, standoff + 1_000):
        px = _clamp(target_x + math.cos(angle) * r, _BORDER, _MAP_W - _BORDER)
        py = _clamp(target_y + math.sin(angle) * r, _BORDER, _MAP_H - _BORDER)
        elev  = _elevation(px, py)
        cover = _cover(px, py)
        score = elev * 0.6 + cover * 200 * 0.4
        # 전술 메모리 패널티/보너스 적용
        if tm is not None:
            score = tm.apply_penalties(px, py, score)
        if score > best_score:
            best_score = score
            best_pt = [px, py]
    return best_pt


def _build_recon_waypoints(
    start_x: float, start_y: float,
    target_x: float, target_y: float,
) -> List[List[float]]:
    """
    교전 회피 정찰 경로 생성.

    전략:
    1. 직선 접근 대신 60도 측방 우회 경유지 삽입
    2. 목표 주변 120도 간격 3개 관측 포인트 (standoff 유지)
    3. 관측 완료 후 출발지 방향 안전 복귀점
    """
    dx = target_x - start_x
    dy = target_y - start_y
    dist = math.hypot(dx, dy) or 1.0
    bearing = math.atan2(dy, dx)  # 진행 방향각

    wps = []

    # ── 0. 전술 메모리 로드 ────────────────────────────────────
    try:
        from wargame.harness.tactical_memory import get_tactical_memory
        _tm = get_tactical_memory()
    except Exception:
        _tm = None

    # ── 1. 측방 우회 경유지 (전술 메모리 기반 최적 측방 선택) ──────
    # 목표 절반 거리 지점에서 60° 또는 -60° 측방 중 패널티가 낮은 방향 선택
    flank_dist = min(dist * 0.45, 5_000)
    best_flank_score = -1.0
    fx, fy = start_x, start_y
    for flank_offset in (math.pi / 3, -math.pi / 3):
        fa = bearing + flank_offset
        _fx = _clamp(start_x + math.cos(fa) * flank_dist, _BORDER, _MAP_W - _BORDER)
        _fy = _clamp(start_y + math.sin(fa) * flank_dist, _BORDER, _MAP_H - _BORDER)
        elev_f  = _elevation(_fx, _fy)
        cover_f = _cover(_fx, _fy)
        fscore = elev_f * 0.5 + cover_f * 200 * 0.5
        if _tm is not None:
            fscore = _tm.apply_penalties(_fx, _fy, fscore)
        if fscore > best_flank_score:
            best_flank_score = fscore
            fx, fy = _fx, _fy
    wps.append([round(fx), round(fy)])

    # ── 2. 3개 관측 포인트 (목표 후방 → 측방 → 전방 순) ────────
    # 시작 각도: 목표 후방(아군 방향)부터 반시계 순 배치
    start_obs_angle = bearing + math.pi  # 목표 기준 아군 쪽
    for i in range(3):
        obs_angle = start_obs_angle + i * (2 * math.pi / 3)
        pt = _best_obs_point(target_x, target_y, obs_angle, _RECON_STANDOFF)
        wps.append([round(pt[0]), round(pt[1])])

    # ── 3. 안전 복귀점 ─────────────────────────────────────────
    # 출발지와 목표 사이 20% 지점으로 후퇴
    ret_x = _clamp(start_x + dx * 0.2, _BORDER, _MAP_W - _BORDER)
    ret_y = _clamp(start_y + dy * 0.2, _BORDER, _MAP_H - _BORDER)
    wps.append([round(ret_x), round(ret_y)])

    return wps


# ──────────────────────────────────────────────────────────────
# 공개 도구
# ──────────────────────────────────────────────────────────────

@tool
def assess_recon_need() -> dict:
    """
    현재 BLUFOR 인텔 상태를 분석하여 정찰 임무의 필요성을 평가합니다.

    OPFOR 탐지 상태 기준:
      - "detected"   : 정확한 위치 파악됨 → 공격 가능
      - "approximate": 개략 위치만 파악   → 정찰 필요
      - "lost"       : 탐지 상실           → 정찰 필요

    Returns:
        {
            "status": "success" | "engine_not_ready" | "error",
            "recon_needed": bool,
            "recommendation": "정찰 우선 실시" | "공격 즉시 가능" | "적 없음",
            "reason": str,
            "opfor_summary": {
                "detected": int,
                "approximate": int,
                "lost": int
            },
            "undetected_targets": [
                {"unit_id", "status", "known_x_km", "known_y_km"}, ...
            ],
            "available_recon_units": [
                {"unit_id", "x_km", "y_km", "combat_power_pct"}, ...
            ]
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        state       = _wargame_engine.get_state()
        intel       = state.get("intelligence", {}).get("BLUFOR", [])
        all_units   = state.get("units", [])

        detected  = [e for e in intel if e["status"] == "detected"]
        approx    = [e for e in intel if e["status"] == "approximate"]
        lost_list = [e for e in intel if e["status"] == "lost"]

        recon_units = [
            u for u in all_units
            if u["side"] == "BLUFOR"
            and u.get("unit_type") == "정찰"
            and u["status"] == "active"
        ]

        recon_needed = bool(approx or lost_list)

        if not intel:
            recommendation = "적 없음"
            reason = "탐지된 적군이 없습니다."
        elif recon_needed:
            recommendation = "정찰 우선 실시"
            reason = (
                f"적 {len(approx) + len(lost_list)}개 부대의 정확한 위치가 미확인입니다. "
                f"(개략위치: {len(approx)}개, 탐지상실: {len(lost_list)}개) "
                "공격 전 정찰부대를 통한 위치 확인이 필요합니다."
            )
        else:
            recommendation = "공격 즉시 가능"
            reason = (
                f"모든 OPFOR {len(detected)}개 부대의 정확한 위치가 확인되었습니다. "
                "공격 임무계획 수립이 가능합니다."
            )

        return {
            "status":         "success",
            "recon_needed":   recon_needed,
            "recommendation": recommendation,
            "reason":         reason,
            "opfor_summary": {
                "detected":    len(detected),
                "approximate": len(approx),
                "lost":        len(lost_list),
            },
            "undetected_targets": [
                {
                    "unit_id":    e["unit_id"],
                    "status":     e["status"],
                    "known_x_km": round(e["known_x"] / 1000, 2),
                    "known_y_km": round(e["known_y"] / 1000, 2),
                }
                for e in approx + lost_list
            ],
            "available_recon_units": [
                {
                    "unit_id":          u["id"],
                    "x_km":             round(u["x"] / 1000, 2),
                    "y_km":             round(u["y"] / 1000, 2),
                    "combat_power_pct": round(u["combat_power"], 1),
                }
                for u in recon_units
            ],
        }

    except Exception as e:
        logger.error(f"assess_recon_need error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@tool
def recommend_recon_routes() -> dict:
    """
    탐지되지 않은 OPFOR 목표에 대해 정찰부대의 교전 회피 정찰 경로를 생성합니다.

    경로 설계 원칙:
      - 직선 접근 금지 → 60도 측방 우회 경유지 삽입
      - 정찰부대 탐지 반경(7 km) 내, 교전 범위(4 km) 바깥 관측 포인트 3개 배치
      - 관측 완료 후 안전 복귀점으로 이동
      - 고도·엄폐율 기준으로 최적 관측 위치 선택

    Returns:
        {
            "status": "success" | "no_recon_units" | "no_targets" | "engine_not_ready" | "error",
            "mission_plans": [
                {
                    "company_id":    str,   // 정찰부대 ID
                    "mission_type":  "recon",
                    "waypoints":     [[x, y], ...],  // 좌표 단위: m
                    "objective":     str,
                    "target_unit_id": str
                }, ...
            ],
            "apply_json": str,   // apply_wargame_mission_plan()에 바로 전달 가능한 JSON
            "summary":   str
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        state     = _wargame_engine.get_state()
        intel     = state.get("intelligence", {}).get("BLUFOR", [])
        all_units = state.get("units", [])

        targets = [e for e in intel if e["status"] in ("approximate", "lost")]
        if not targets:
            return {
                "status":  "no_targets",
                "message": "모든 OPFOR 위치가 탐지됨. 정찰 불필요.",
                "mission_plans": [],
                "apply_json": json.dumps({"mission_plans": []}, ensure_ascii=False),
                "summary": "모든 OPFOR 위치가 탐지됨. 정찰 불필요.",
            }

        recon_units = [
            u for u in all_units
            if u["side"] == "BLUFOR"
            and u.get("unit_type") == "정찰"
            and u["status"] == "active"
        ]
        if not recon_units:
            return {
                "status":  "no_recon_units",
                "message": "사용 가능한 BLUFOR 정찰부대가 없습니다.",
                "mission_plans": [],
                "apply_json": json.dumps({"mission_plans": []}, ensure_ascii=False),
                "summary": "사용 가능한 BLUFOR 정찰부대가 없습니다.",
            }

        # ── 정찰부대-목표 매칭 (거리 최소 우선) ──────────────────
        assignments = []
        used_recon  = set()

        for target in targets:
            tx, ty = target["known_x"], target["known_y"]

            # 가장 가까운 미사용 정찰부대 선택
            best_ru   = None
            best_dist = float("inf")
            for ru in recon_units:
                if ru["id"] in used_recon:
                    continue
                d = math.hypot(ru["x"] - tx, ru["y"] - ty)
                if d < best_dist:
                    best_dist = d
                    best_ru   = ru

            # 정찰부대 부족 시 전투력 최고 부대 재사용
            if best_ru is None:
                best_ru = max(recon_units, key=lambda u: u["combat_power"])
            else:
                used_recon.add(best_ru["id"])

            wps = _build_recon_waypoints(best_ru["x"], best_ru["y"], tx, ty)

            status_ko = {"approximate": "개략위치", "lost": "탐지상실"}.get(
                target["status"], target["status"]
            )
            assignments.append({
                "company_id":     best_ru["id"],
                "mission_type":   "recon",
                "waypoints":      wps,
                "objective":      f"{target['unit_id']} 위치 정밀확인 ({status_ko})",
                "target_unit_id": target["unit_id"],
            })

        # apply_wargame_mission_plan 호환 JSON (target_unit_id 제외)
        apply_payload = {
            "mission_plans": [
                {k: v for k, v in a.items() if k != "target_unit_id"}
                for a in assignments
            ]
        }
        apply_json = json.dumps(apply_payload, ensure_ascii=False)

        summary_lines = [f"정찰 임무계획: {len(assignments)}개 부대 파견"]
        for a in assignments:
            summary_lines.append(
                f"  {a['company_id']} → {a['objective']} "
                f"({len(a['waypoints'])}개 경유지)"
            )

        return {
            "status":        "success",
            "mission_plans": assignments,
            "apply_json":    apply_json,
            "summary":       "\n".join(summary_lines),
        }

    except Exception as e:
        logger.error(f"recommend_recon_routes error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
