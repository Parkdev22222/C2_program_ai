"""
워게임 전술 추천 도구 (smolagents Tool)

각 부대의 병종 상성(相性)을 분석하고, 지형 고도·엄폐를 반영한
부대별 추천 기동 경로(좌표 리스트)를 반환합니다.
"""
import logging
import math
from typing import List, Tuple
from c2.domain.wargame.coordinates import xy_to_latlon, waypoints_xy_to_latlon

logger = logging.getLogger(__name__)

_wargame_engine = None


def register_wargame_engine(engine):
    global _wargame_engine
    _wargame_engine = engine


# ── 병종 상성표 ───────────────────────────────────────────────────
# (공격자 유형, 방어자 유형): (유리도, 화력 보정 배율, 설명)
_MATCHUP_TABLE = {
    ("전차",     "기계화보병"): ("유리",     1.5, "전차 화력·장갑으로 기계화보병 압도"),
    ("전차",     "정찰"):       ("유리",     1.4, "전차는 경장갑 정찰에 압도적"),
    ("전차",     "대전차"):     ("불리",     0.6, "대전차 무기에 장갑 관통 취약"),
    ("전차",     "자주포"):     ("유리",     1.3, "직사 화력으로 포병 제압"),
    ("전차",     "전차"):       ("균등",     1.0, "전차 대 전차 정면 대결"),
    ("기계화보병","기계화보병"): ("균등",     1.0, "동일 병종 교전"),
    ("기계화보병","정찰"):       ("유리",     1.2, "화력 우세로 정찰 제압"),
    ("기계화보병","전차"):       ("불리",     0.5, "전차 화력에 취약"),
    ("기계화보병","대전차"):     ("균등",     0.9, "근접 기동으로 대전차 제압 가능"),
    ("기계화보병","자주포"):     ("유리",     1.3, "근접 기동으로 포병 제압"),
    ("대전차",   "전차"):       ("매우유리", 2.0, "대전차 전문 무기로 장갑 관통"),
    ("대전차",   "기계화보병"): ("균등",     0.9, "대전차 무기로 경장갑 교전"),
    ("대전차",   "정찰"):       ("유리",     1.2, "경장갑 정찰 제압"),
    ("대전차",   "자주포"):     ("유리",     1.3, "포병 진지 직접 타격"),
    ("대전차",   "대전차"):     ("균등",     1.0, "동일 역할 교전"),
    ("정찰",     "자주포"):     ("유리",     1.3, "고속 기동으로 포병 노출·제압"),
    ("정찰",     "정찰"):       ("균등",     1.0, "속도 경쟁"),
    ("정찰",     "기계화보병"): ("불리",     0.6, "화력 열세, 우회 기동 선호"),
    ("정찰",     "전차"):       ("매우불리", 0.3, "전차에 극도로 취약, 회피 필수"),
    ("자주포",   "기계화보병"): ("유리",     1.4, "원거리 면제압"),
    ("자주포",   "정찰"):       ("균등",     0.8, "원거리 면제압, 근접 시 취약"),
    ("자주포",   "전차"):       ("불리",     0.7, "직사 교전 불리, 간접 화력 활용"),
    ("자주포",   "자주포"):     ("균등",     1.0, "포병 대 포병 대결"),
}


def _get_matchup(attacker_type: str, defender_type: str) -> Tuple[str, float, str]:
    key = (attacker_type, defender_type)
    return _MATCHUP_TABLE.get(key, ("균등", 1.0, "상성 정보 없음"))


# ── 지형 기반 경유지 생성 ──────────────────────────────────────────

def _terrain_route(start_x: float, start_y: float,
                   end_x: float, end_y: float,
                   n_mid: int = 3) -> List[List[int]]:
    """
    출발점 → 목적지 사이 n_mid개 중간 경유지를 지형(고도+엄폐) 기반으로 선택.
    각 구간마다 직선 경로 주변 5×5 후보 중 점수 최고 지점 선택.
    """
    try:
        from c2.domain.wargame.terrain import terrain
    except Exception:
        # 지형 모듈 없으면 직선 균등 분할
        pts = []
        for i in range(1, n_mid + 1):
            t = i / (n_mid + 1)
            pts.append([round(start_x + (end_x - start_x) * t),
                        round(start_y + (end_y - start_y) * t)])
        pts.append([round(end_x), round(end_y)])
        return pts

    search_r = 2_500   # 후보 탐색 반경 (m)
    step = search_r // 2

    waypoints = []
    for i in range(1, n_mid + 1):
        t = i / (n_mid + 1)
        base_x = start_x + (end_x - start_x) * t
        base_y = start_y + (end_y - start_y) * t

        best_score = -1e9
        best_x, best_y = base_x, base_y

        for dx in range(-2, 3):
            for dy in range(-2, 3):
                cx = max(0, min(29_999, base_x + dx * step))
                cy = max(0, min(29_999, base_y + dy * step))
                elev  = terrain.elevation(cx, cy)
                cover = terrain.cover_factor(cx, cy)
                # 고도 우선, 엄폐 보조
                score = elev * 0.7 + cover * 300
                if score > best_score:
                    best_score = score
                    best_x, best_y = cx, cy

        waypoints.append([int(best_x), int(best_y)])

    waypoints.append([int(end_x), int(end_y)])
    return waypoints


def _elevation_info(x: float, y: float) -> str:
    try:
        from c2.domain.wargame.terrain import terrain
        elev  = terrain.elevation(x, y)
        cover = terrain.cover_factor(x, y)
        return f"{elev:.0f}m/엄폐{cover:.2f}"
    except Exception:
        return "N/A"


# ── 메인 툴 ────────────────────────────────────────────────────────


def get_wargame_tactical_recommendation() -> dict:
    """
    워게임 시뮬레이터의 현재 전장 상황을 분석하여 다음 두 가지를 반환합니다.

    1) 병종 상성(相性) 기반 교전 매칭 — BLUFOR 각 부대에 대해 최적 OPFOR 타겟 추천
    2) 지형 고도·엄폐를 반영한 부대별 추천 기동 경로 (좌표 리스트, 단위 m)

    Returns:
        {
            "status": "success" | "engine_not_ready" | "error",
            "game_time": str,
            "matchup_recommendations": [
                {
                    "blufor_unit":    str,   # "Alpha"
                    "blufor_type":    str,   # "기계화보병"
                    "recommended_target": str,  # "Red1"
                    "target_type":    str,
                    "advantage":      str,   # "유리" | "불리" | "균등" | "매우유리" | "매우불리"
                    "firepower_multiplier": float,
                    "reason":         str
                }, ...
            ],
            "movement_routes": [
                {
                    "unit_id":   str,
                    "unit_type": str,
                    "from":      [x_m, y_m],
                    "to_target": str,
                    "waypoints": [[x1,y1], [x2,y2], ...],  # 경유지 포함 최종 목적지
                    "terrain_notes": str   # 경로 지형 요약
                }, ...
            ]
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        state = _wargame_engine.get_state()
        units = state.get("units", [])

        blufor = [u for u in units if u["side"] == "BLUFOR" and u["status"] != "destroyed"]
        opfor  = [u for u in units if u["side"] == "OPFOR"  and u["status"] != "destroyed"]

        if not blufor or not opfor:
            return {
                "status": "success",
                "game_time": state.get("game_time_str", ""),
                "matchup_recommendations": [],
                "movement_routes": [],
            }

        # ── 1. 상성 기반 교전 매칭 ──────────────────────────────
        matchups = []
        for bu in blufor:
            best_target = None
            best_score = -1e9

            for ou in opfor:
                adv, mult, _ = _get_matchup(bu.get("unit_type", ""), ou.get("unit_type", ""))
                dist = math.hypot(bu["x"] - ou["x"], bu["y"] - ou["y"])
                # 화력배율 높고, 거리 가까울수록 선호
                score = mult * 100 - dist / 1000
                if score > best_score:
                    best_score = score
                    best_target = ou

            if best_target:
                adv, mult, reason = _get_matchup(
                    bu.get("unit_type", ""), best_target.get("unit_type", "")
                )
                bu_lat, bu_lon = xy_to_latlon(bu["x"], bu["y"])
                tgt_lat, tgt_lon = xy_to_latlon(best_target["x"], best_target["y"])
                matchups.append({
                    "blufor_unit":           bu["id"],
                    "blufor_type":           bu.get("unit_type", ""),
                    "blufor_cp":             round(bu["combat_power"], 1),
                    "blufor_lat":            bu_lat,
                    "blufor_lon":            bu_lon,
                    "recommended_target":    best_target["id"],
                    "target_type":           best_target.get("unit_type", ""),
                    "target_cp":             round(best_target["combat_power"], 1),
                    "target_lat":            tgt_lat,
                    "target_lon":            tgt_lon,
                    "advantage":             adv,
                    "firepower_multiplier":  mult,
                    "distance_m":            int(math.hypot(
                        bu["x"] - best_target["x"], bu["y"] - best_target["y"]
                    )),
                    "reason": reason,
                })

        # ── 2. 지형 기반 기동 경로 ───────────────────────────────
        routes = []
        for bu in blufor:
            # 매칭된 타겟 찾기
            matched = next(
                (m for m in matchups if m["blufor_unit"] == bu["id"]), None
            )
            if matched is None:
                continue

            target_id = matched["recommended_target"]
            target = next((u for u in opfor if u["id"] == target_id), None)
            if target is None:
                continue

            # 경로 생성 (중간 경유지 3개)
            waypoints_m = _terrain_route(
                bu["x"], bu["y"], target["x"], target["y"], n_mid=3
            )
            waypoints_latlon = waypoints_xy_to_latlon(waypoints_m)

            # 경유지 지형 요약
            terrain_notes = "경유지 고도: " + " → ".join(
                _elevation_info(wp[0], wp[1]) for wp in waypoints_m[:-1]
            )

            from_lat, from_lon = xy_to_latlon(bu["x"], bu["y"])
            routes.append({
                "unit_id":      bu["id"],
                "unit_type":    bu.get("unit_type", ""),
                "from_lat":     from_lat,
                "from_lon":     from_lon,
                "from":         [int(bu["x"]), int(bu["y"])],   # 내부 미터 (하위호환)
                "to_target":    target_id,
                "waypoints":    waypoints_latlon,
                "terrain_notes": terrain_notes,
            })

        return {
            "status": "success",
            "game_time": state.get("game_time_str", "00:00:00"),
            "matchup_recommendations": matchups,
            "movement_routes": routes,
        }

    except Exception as e:
        logger.error(f"get_wargame_tactical_recommendation error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
