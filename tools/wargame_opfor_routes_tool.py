"""
OPFOR 예상 기동 경로 예측 도구 (smolagents Tool)

정찰에 의해 탐지된 OPFOR 부대가 BLUFOR를 공격하기 위해
기동할 가능성이 높은 경로(정면/좌측우회/우측우회)를 지형·고도·엄폐를
반영하여 생성합니다.

반환된 경로는 get_optimal_attack_positions(opfor_routes_json=...) 에
그대로 전달하여 경로 차단 최적 공격 위치 산출에 활용합니다.
"""
import json
import math
import logging
from typing import List, Tuple

from smolagents import tool

logger = logging.getLogger(__name__)

_wargame_engine = None


def register_wargame_engine(engine):
    global _wargame_engine
    _wargame_engine = engine


# ── 상수 ──────────────────────────────────────────────────────────────
_MAP_W, _MAP_H = 30_000, 30_000
_BORDER = 500


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


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


# ── 지형 기반 접근 경로 생성 ────────────────────────────────────────────

def _terrain_approach_route(
    ox: float, oy: float,
    tx: float, ty: float,
    flank_offset_rad: float = 0.0,
    n_mid: int = 4,
) -> List[List[int]]:
    """
    OPFOR (ox,oy) → 목표 (tx,ty) 예상 접근 경로 생성.

    flank_offset_rad > 0 → 우측 우회
    flank_offset_rad < 0 → 좌측 우회
    0 = 정면 접근

    경로 중 각 구간에서 OPFOR 기동 선호 지형(고도+엄폐 최고)을 탐색.
    """
    bearing  = math.atan2(ty - oy, tx - ox)
    perp     = bearing + math.pi / 2
    total_dist = math.hypot(tx - ox, ty - oy)

    # 탐색 반경: 총 거리의 1/6, 최소 1 km / 최대 3 km
    search_r = max(1_000, min(3_000, total_dist / 6))
    step = search_r / 2

    waypoints = []
    for i in range(1, n_mid + 1):
        t = i / (n_mid + 1)
        base_x = ox + (tx - ox) * t
        base_y = oy + (ty - oy) * t

        # 우회 기동: 경로 중간에 최대 측방 이탈 (sin 곡선)
        if flank_offset_rad != 0.0:
            flank_r = (
                math.sin(t * math.pi)         # 중간에 최대, 양끝 0
                * total_dist * 0.28
                * math.sin(abs(flank_offset_rad))  # 오프셋 크기 반영
                * (1 if flank_offset_rad > 0 else -1)
            )
            base_x += math.cos(perp) * flank_r
            base_y += math.sin(perp) * flank_r

        base_x = _clamp(base_x, _BORDER, _MAP_W - _BORDER)
        base_y = _clamp(base_y, _BORDER, _MAP_H - _BORDER)

        # 주변 5×5 후보 중 OPFOR 기동 선호 지점 (고도+엄폐 가중합 최고)
        best_score = -1e9
        best_x, best_y = base_x, base_y
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                cx = _clamp(base_x + dx * step, _BORDER, _MAP_W - _BORDER)
                cy = _clamp(base_y + dy * step, _BORDER, _MAP_H - _BORDER)
                elev  = _elevation(cx, cy)
                cov   = _cover(cx, cy)
                score = elev * 0.6 + cov * 300 * 0.4
                if score > best_score:
                    best_score = score
                    best_x, best_y = cx, cy

        waypoints.append([int(best_x), int(best_y)])

    waypoints.append([int(tx), int(ty)])
    return waypoints


def _route_threat_level(
    route_waypoints: List[List[int]],
    ox: float, oy: float,
    tx: float, ty: float,
) -> str:
    """
    경로의 위협 수준 평가.
    경유 지점 고도·엄폐 평균과 접근 각도를 반영.
    """
    if not route_waypoints:
        return "low"

    elev_vals = [_elevation(wp[0], wp[1]) for wp in route_waypoints[:-1]]
    cov_vals  = [_cover(wp[0], wp[1])     for wp in route_waypoints[:-1]]
    avg_elev  = sum(elev_vals) / max(len(elev_vals), 1)
    avg_cov   = sum(cov_vals)  / max(len(cov_vals),  1)

    # 목표 지점 기준 도착 각도 (OPFOR가 방어자 기준 후방/측방에서 오면 위협↑)
    final_wp = route_waypoints[-2] if len(route_waypoints) >= 2 else route_waypoints[-1]
    approach_dist = math.hypot(final_wp[0] - tx, final_wp[1] - ty)

    # 고도+엄폐 높고 근접 가능할수록 위협 높음
    threat_score = avg_elev * 0.005 + avg_cov * 2.0 + max(0, 5000 - approach_dist) / 1000
    if threat_score >= 6.0:
        return "high"
    elif threat_score >= 3.5:
        return "medium"
    return "low"


def _find_key_chokepoints(
    route_waypoints: List[List[int]],
) -> List[List[int]]:
    """
    경로 상 핵심 차단 포인트 추출.
    고도·엄폐가 높은 상위 2개 경유지를 반환 (경로 말단 제외).
    """
    candidates = []
    for wp in route_waypoints[:-1]:
        elev = _elevation(wp[0], wp[1])
        cov  = _cover(wp[0], wp[1])
        candidates.append((elev * 0.7 + cov * 300 * 0.3, wp))
    candidates.sort(reverse=True)
    return [c[1] for c in candidates[:2]]


def _terrain_notes_for_route(waypoints: List[List[int]]) -> str:
    """경로 경유지 고도·엄폐 요약 문자열 반환."""
    parts = []
    for wp in waypoints[:-1]:
        elev = _elevation(wp[0], wp[1])
        cov  = _cover(wp[0], wp[1])
        parts.append(f"({wp[0]},{wp[1]})={elev:.0f}m/엄폐{cov:.2f}")
    return " → ".join(parts)


# ── 메인 툴 ────────────────────────────────────────────────────────────

@tool
def predict_opfor_routes() -> dict:
    """
    정찰에 의해 탐지된 OPFOR 부대가 아군(BLUFOR)을 공격하기 위해
    기동할 수 있는 예상 경로를 분석합니다.

    각 OPFOR 부대에 대해 지형(고도·엄폐)을 반영한 3가지 접근 경로를 생성합니다:
      - 정면 접근(direct): 직접 접근, 고지대·엄폐 선호
      - 우측 우회(right_flank): 우측으로 우회 후 목표 접근
      - 좌측 우회(left_flank): 좌측으로 우회 후 목표 접근

    반환된 predicted_routes 리스트를 JSON 직렬화하여
    get_optimal_attack_positions(opfor_routes_json=...) 에 전달하면
    경로 차단 위치가 공격 후보지 평가에 반영됩니다.

    Returns:
        {
            "status": "success" | "engine_not_ready" | "no_detected_targets" | "error",
            "game_time": str,
            "predicted_routes": [
                {
                    "opfor_unit_id":   str,
                    "opfor_unit_type": str,
                    "opfor_pos":       [x_m, y_m],
                    "target_blufor_id":  str,
                    "target_blufor_pos": [x_m, y_m],
                    "routes": [
                        {
                            "route_type":    "direct" | "right_flank" | "left_flank",
                            "threat_level":  "high" | "medium" | "low",
                            "waypoints":     [[x_m, y_m], ...],
                            "key_chokepoints": [[x_m, y_m], ...],
                            "terrain_notes": str
                        }, ...
                    ]
                }, ...
            ],
            "interdict_priority": [
                {
                    "chokepoint":          [x_m, y_m],
                    "elevation_m":         float,
                    "cover":               float,
                    "intercepting_routes": int,
                    "opfor_unit_ids":      [str, ...]
                }, ...
            ]
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready",
                "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        state     = _wargame_engine.get_state()
        intel     = state.get("intelligence", {}).get("BLUFOR", [])
        all_units = state.get("units", [])

        # 탐지된 OPFOR (detected / approximate)
        detected_opfor = [e for e in intel if e["status"] in ("detected", "approximate")]
        if not detected_opfor:
            return {
                "status":          "no_detected_targets",
                "game_time":       state.get("game_time_str", ""),
                "message":         "탐지된 OPFOR가 없습니다.",
                "predicted_routes":  [],
                "interdict_priority": [],
            }

        # 활성 BLUFOR
        blufor_active = [
            u for u in all_units
            if u["side"] == "BLUFOR" and u["status"] != "destroyed"
        ]
        if not blufor_active:
            return {
                "status":          "no_detected_targets",
                "game_time":       state.get("game_time_str", ""),
                "message":         "활성 BLUFOR 부대가 없습니다.",
                "predicted_routes":  [],
                "interdict_priority": [],
            }

        # ── 각 OPFOR에 대해 예상 경로 생성 ──────────────────────────
        predicted_routes = []
        all_chokepoints: dict = {}   # (x, y) → {intercepting_routes, opfor_unit_ids}

        for entry in detected_opfor:
            ox = float(entry["known_x"])
            oy = float(entry["known_y"])
            opfor_id   = entry["unit_id"]
            opfor_type = entry.get("unit_type") or "미확인"

            # 가장 가까운 BLUFOR를 주 공격 목표로 설정
            target = min(
                blufor_active,
                key=lambda u: math.hypot(u["x"] - ox, u["y"] - oy),
            )
            tx, ty = float(target["x"]), float(target["y"])

            # 3가지 접근 경로 생성 (정면 / 우측 / 좌측 우회)
            route_configs = [
                ("direct",      0.0),
                ("right_flank", math.pi / 4),     # +45°
                ("left_flank",  -math.pi / 4),    # -45°
            ]

            routes = []
            for route_type, flank_rad in route_configs:
                wps = _terrain_approach_route(ox, oy, tx, ty, flank_rad, n_mid=4)
                threat = _route_threat_level(wps, ox, oy, tx, ty)
                chokepoints = _find_key_chokepoints(wps)
                notes = _terrain_notes_for_route(wps)

                routes.append({
                    "route_type":     route_type,
                    "threat_level":   threat,
                    "waypoints":      wps,
                    "key_chokepoints": chokepoints,
                    "terrain_notes":  notes,
                })

                # 차단 우선순위 집계
                for cp in chokepoints:
                    key = (cp[0], cp[1])
                    if key not in all_chokepoints:
                        all_chokepoints[key] = {
                            "intercepting_routes": 0,
                            "opfor_unit_ids": [],
                        }
                    all_chokepoints[key]["intercepting_routes"] += 1
                    if opfor_id not in all_chokepoints[key]["opfor_unit_ids"]:
                        all_chokepoints[key]["opfor_unit_ids"].append(opfor_id)

            predicted_routes.append({
                "opfor_unit_id":   opfor_id,
                "opfor_unit_type": opfor_type,
                "opfor_pos":       [int(ox), int(oy)],
                "target_blufor_id":  target["id"],
                "target_blufor_pos": [int(tx), int(ty)],
                "routes":          routes,
            })

        # ── 차단 우선순위 정렬 ──────────────────────────────────────
        interdict_priority = []
        for (cx, cy), info in all_chokepoints.items():
            elev = _elevation(cx, cy)
            cov  = _cover(cx, cy)
            interdict_priority.append({
                "chokepoint":          [int(cx), int(cy)],
                "elevation_m":         round(elev, 1),
                "cover":               round(cov, 3),
                "intercepting_routes": info["intercepting_routes"],
                "opfor_unit_ids":      info["opfor_unit_ids"],
            })
        # 많은 경로를 차단하는 지점, 그 중 고도 높은 순
        interdict_priority.sort(
            key=lambda p: (p["intercepting_routes"], p["elevation_m"]),
            reverse=True,
        )

        return {
            "status":             "success",
            "game_time":          state.get("game_time_str", "00:00:00"),
            "total_opfor":        len(predicted_routes),
            "predicted_routes":   predicted_routes,
            "interdict_priority": interdict_priority[:6],   # 상위 6개
        }

    except Exception as e:
        logger.error(f"predict_opfor_routes error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
