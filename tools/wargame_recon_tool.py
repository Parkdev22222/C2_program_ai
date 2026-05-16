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
    current_context: dict = None,
) -> List[float]:
    """
    목표 주변 지정 각도·거리에서 고도+엄폐+전술메모리 기준 최적 관측 포인트를 반환.
    standoff±1km 범위에서 3개 후보 중 최적을 선택.
    """
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
        if tm is not None:
            score = tm.apply_penalties(px, py, score, current_context)
        if score > best_score:
            best_score = score
            best_pt = [px, py]
    return best_pt


def _build_current_recon_context() -> dict:
    """현재 엔진 상태에서 정찰 임무용 교전 컨텍스트 빌드."""
    if _wargame_engine is None:
        return {}
    try:
        state = _wargame_engine.get_state()
        units = state.get("units", [])
        opfor = [u for u in units if u.get("side") == "OPFOR" and u.get("status") != "destroyed"]
        blufor = [u for u in units if u.get("side") == "BLUFOR" and u.get("status") != "destroyed"]
        blufor_cp = sum(u.get("combat_power", 100) for u in blufor) / max(len(blufor), 1)
        opfor_cp  = sum(u.get("combat_power", 100) for u in opfor) / max(len(opfor), 1)
        opfor_positions = [
            [float(u.get("x", 0)), float(u.get("y", 0))] for u in opfor
        ]
        # 적 위치 중심점 기준 지형 프로파일 샘플링
        terrain_ctx = {}
        if opfor_positions:
            try:
                from wargame.harness.tactical_memory import sample_terrain_profile
                cx_op = sum(p[0] for p in opfor_positions) / len(opfor_positions)
                cy_op = sum(p[1] for p in opfor_positions) / len(opfor_positions)
                terrain_ctx = sample_terrain_profile(cx_op, cy_op, radius=3000.0)
            except Exception:
                pass
        return {
            "enemy_unit_types": list({u.get("unit_type", "unknown") for u in opfor}),
            "enemy_count": len(opfor),
            "enemy_positions": opfor_positions,
            "friendly_unit_types": list({u.get("unit_type", "unknown") for u in blufor}),
            "force_ratio": blufor_cp / max(opfor_cp, 0.01),
            "terrain": terrain_ctx,
        }
    except Exception:
        return {}


def _nn_order(start_x: float, start_y: float, targets: list) -> list:
    """Nearest-neighbor로 목표 방문 순서 최적화."""
    remaining = list(targets)
    ordered = []
    cx, cy = start_x, start_y
    while remaining:
        nearest = min(remaining, key=lambda t: math.hypot(t["known_x"] - cx, t["known_y"] - cy))
        ordered.append(nearest)
        cx, cy = nearest["known_x"], nearest["known_y"]
        remaining.remove(nearest)
    return ordered


def _obs_point_for_target(
    from_x: float, from_y: float,
    target_x: float, target_y: float,
    ctx: dict,
) -> List[float]:
    """
    직전 위치(from)→목표 방향 기준 최적 관측 포인트 1개 반환.
    - 목표 후방(180°) 또는 측방(±60°) 중 점수 최고 지점 선택
    """
    bearing = math.atan2(target_y - from_y, target_x - from_x)
    best_score, best_pt = -1.0, None
    for angle_offset in (math.pi, math.pi * 2 / 3, -math.pi * 2 / 3):
        obs_angle = bearing + angle_offset
        pt = _best_obs_point(target_x, target_y, obs_angle, _RECON_STANDOFF, ctx)
        elev  = _elevation(pt[0], pt[1])
        cover = _cover(pt[0], pt[1])
        score = elev * 0.6 + cover * 200 * 0.4
        if score > best_score:
            best_score = score
            best_pt = pt
    return best_pt


def _build_combined_recon_waypoints(
    ru_x: float, ru_y: float,
    targets: list,
) -> List[List[float]]:
    """
    모든 목표를 하나의 경로로 순회하는 통합 정찰 경로 생성.

    전략:
    1. Nearest-neighbor로 목표 방문 순서 최적화
    2. 첫 목표 진입 전 60도 측방 우회 경유지 1개
    3. 각 목표마다 최적 관측 포인트 1개 (후방/측방 중 best)
    4. 모든 목표 순회 후 출발지 방향 안전 복귀점
    """
    try:
        from wargame.harness.tactical_memory import get_tactical_memory
        _tm = get_tactical_memory()
    except Exception:
        _tm = None

    _ctx = _build_current_recon_context()

    ordered = _nn_order(ru_x, ru_y, targets)
    wps = []

    # ── 1. 첫 목표 방향 측방 우회 경유지 ─────────────────────────
    first_tx, first_ty = ordered[0]["known_x"], ordered[0]["known_y"]
    bearing0 = math.atan2(first_ty - ru_y, first_tx - ru_x)
    flank_dist = min(math.hypot(first_tx - ru_x, first_ty - ru_y) * 0.4, 5_000)
    best_flank_score, fx, fy = -1.0, ru_x, ru_y
    for offset in (math.pi / 3, -math.pi / 3):
        fa = bearing0 + offset
        _fx = _clamp(ru_x + math.cos(fa) * flank_dist, _BORDER, _MAP_W - _BORDER)
        _fy = _clamp(ru_y + math.sin(fa) * flank_dist, _BORDER, _MAP_H - _BORDER)
        score = _elevation(_fx, _fy) * 0.5 + _cover(_fx, _fy) * 200 * 0.5
        if _tm:
            score = _tm.apply_penalties(_fx, _fy, score, _ctx)
        if score > best_flank_score:
            best_flank_score = score
            fx, fy = _fx, _fy
    wps.append([round(fx), round(fy)])

    # ── 2. 각 목표별 관측 포인트 1개씩 ──────────────────────────
    prev_x, prev_y = ru_x, ru_y
    for t in ordered:
        pt = _obs_point_for_target(prev_x, prev_y, t["known_x"], t["known_y"], _ctx)
        wps.append([round(pt[0]), round(pt[1])])
        prev_x, prev_y = t["known_x"], t["known_y"]

    # ── 3. 안전 복귀점 (출발지 방향 20% 지점) ────────────────────
    ret_x = _clamp(ru_x + (first_tx - ru_x) * 0.2, _BORDER, _MAP_W - _BORDER)
    ret_y = _clamp(ru_y + (first_ty - ru_y) * 0.2, _BORDER, _MAP_H - _BORDER)
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
                {"unit_id", "status", "known_x_m", "known_y_m"}, ...
            ],
            "available_recon_units": [
                {"unit_id", "x_m", "y_m", "combat_power_pct"}, ...
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

        # degraded(전투력 저하)도 정찰 임무 수행 가능 — suppressed·destroyed만 제외
        recon_units = [
            u for u in all_units
            if u["side"] == "BLUFOR"
            and u.get("unit_type") == "정찰"
            and u["status"] not in ("suppressed", "destroyed")
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
                    "known_x_m": int(e["known_x"]),
                    "known_y_m": int(e["known_y"]),
                }
                for e in approx + lost_list
            ],
            "available_recon_units": [
                {
                    "unit_id":          u["id"],
                    "status":           u["status"],
                    "x_m":              int(u["x"]),
                    "y_m":              int(u["y"]),
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

        # degraded(전투력 저하)도 정찰 임무 수행 가능 — suppressed·destroyed만 제외
        recon_units = [
            u for u in all_units
            if u["side"] == "BLUFOR"
            and u.get("unit_type") == "정찰"
            and u["status"] not in ("suppressed", "destroyed")
        ]
        if not recon_units:
            return {
                "status":  "no_recon_units",
                "message": "사용 가능한 BLUFOR 정찰부대가 없습니다. (전멸 또는 전원 제압 상태)",
                "mission_plans": [],
                "apply_json": json.dumps({"mission_plans": []}, ensure_ascii=False),
                "summary": "사용 가능한 BLUFOR 정찰부대가 없습니다.",
            }

        # ── 정찰부대별 통합 경로 생성 ─────────────────────────────
        # 정찰부대 1개당 모든 미탐지 목표를 순회하는 경로 1개 생성.
        # 정찰부대가 여러 개일 경우 목표를 분배 후 각각 통합 경로 생성.
        assignments = []

        # 정찰부대별 목표 분배 (부대 수만큼 균등 분할)
        n_ru = len(recon_units)
        chunks: List[list] = [[] for _ in range(n_ru)]
        for i, t in enumerate(targets):
            chunks[i % n_ru].append(t)

        for ru, chunk in zip(recon_units, chunks):
            if not chunk:
                continue
            wps = _build_combined_recon_waypoints(ru["x"], ru["y"], chunk)
            target_ids = ", ".join(t["unit_id"] for t in chunk)
            assignments.append({
                "company_id":     ru["id"],
                "mission_type":   "recon",
                "waypoints":      wps,
                "objective":      f"OPFOR 위치 정밀확인: {target_ids}",
                "target_unit_ids": [t["unit_id"] for t in chunk],
            })

        # apply_wargame_mission_plan 호환 JSON (내부 메타 필드 제외)
        _exclude = {"target_unit_id", "target_unit_ids"}
        apply_payload = {
            "mission_plans": [
                {k: v for k, v in a.items() if k not in _exclude}
                for a in assignments
            ]
        }
        apply_json = json.dumps(apply_payload, ensure_ascii=False)

        ru_status = {ru["id"]: ru["status"] for ru in recon_units}

        summary_lines = [
            f"정찰 임무계획: {len(assignments)}개 부대, "
            f"목표 {len(targets)}개 통합 경로"
        ]
        for a in assignments:
            st = ru_status.get(a["company_id"], "active")
            st_note = " [전투력 저하]" if st == "degraded" else ""
            tids = ", ".join(a.get("target_unit_ids", []))
            summary_lines.append(
                f"  {a['company_id']}{st_note} → {tids} "
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
