"""
워게임 최적 공격 위치·수단 추천 도구 (smolagents Tool)

BLUFOR 인텔에서 탐지된 OPFOR 위치(고도·엄폐 포함)를 기반으로,
아군 피해를 최소화하면서 적군에 최대 피해를 줄 수 있는
공격 위치 및 공격 수단을 산출합니다.
"""
import json
import math
import logging
from typing import List, Tuple
from smolagents import tool
from tools.coord_utils import xy_to_latlon

logger = logging.getLogger(__name__)

_wargame_engine = None


def _get_attack_ontology_ctx() -> str:
    """공격 임무 관련 온톨로지 컨텍스트 조회 (실패 시 빈 문자열)."""
    try:
        from tools.graph_rag_tool import get_attack_ontology_context
        return get_attack_ontology_context()
    except Exception as e:
        logger.debug(f"[GraphRAG] 공격 온톨로지 조회 실패 (무시): {e}")
        return ""


def register_wargame_engine(engine):
    global _wargame_engine
    _wargame_engine = engine


# ── 교전 거리 상수 ─────────────────────────────────────────────────
_ENGAGEMENT_RANGE = 2_500.0
_SUPPRESSION_RANGE = 4_000.0
_ARTILLERY_RANGE = 8_000.0   # 자주포 간접사격 사거리
# 이 전투력 이상인 detected 표적은 정밀타격(strike) 대상 — 고가치 점표적 제거
_STRIKE_CP_THRESHOLD = 80.0
# 아군 부대가 표적과 이 거리 이내면 근접 교전으로 보고 정밀타격(좁은 반경)을 권고
_CLOSE_CONTACT_RANGE = 1_500.0


def _engagement_factor(dist: float) -> float:
    if dist <= 1_000:
        return 1.0
    elif dist <= _ENGAGEMENT_RANGE:
        return 1.0 - (dist - 1_000) / (_ENGAGEMENT_RANGE - 1_000) * 0.5
    elif dist <= _SUPPRESSION_RANGE:
        return 0.5 - (dist - _ENGAGEMENT_RANGE) / (_SUPPRESSION_RANGE - _ENGAGEMENT_RANGE) * 0.4
    return 0.0


def _line_of_sight_quality(x1: float, y1: float,
                            x2: float, y2: float) -> float:
    """
    두 좌표 간 시선(LOS) 품질 반환 (1.0 = 완전 개방 / 0.3 = 지형 차폐).
    경로 사이 6개 지점 고도를 샘플링, 중간 지형이 관측자보다 높으면 차폐.
    """
    try:
        from wargame.terrain import terrain as _t
        samples = 6
        atk_elev = _t.elevation(x1, y1)
        def_elev = _t.elevation(x2, y2)
        worst = 0.0
        for i in range(1, samples):
            t = i / samples
            sx = x1 + (x2 - x1) * t
            sy = y1 + (y2 - y1) * t
            mid_elev = _t.elevation(sx, sy)
            # 직선 고도 보간(관측자→목표) 대비 중간 지형 초과량
            interp_elev = atk_elev + (def_elev - atk_elev) * t
            block = max(0.0, (mid_elev - interp_elev) / 80.0)
            worst = max(worst, block)
        return max(0.3, 1.0 - worst)
    except Exception:
        return 1.0


def _score_attack_position(
    cx: float, cy: float,
    ox: float, oy: float,
    def_cover: float,
    current_context: dict = None,
) -> Tuple[float, dict]:
    """
    후보 공격 위치 (cx,cy) 에서 적 (ox,oy) 를 공격할 때의 종합 점수를 계산.

    current_context가 제공되면 전술 메모리 패널티를 상황에 맞게 조절합니다.

    Returns:
        (score, detail_dict)
    """
    try:
        from wargame.terrain import terrain as _t
        atk_elev = _t.elevation(cx, cy)
        atk_cover = _t.cover_factor(cx, cy)
        elev_adv = _t.elevation_advantage(cx, cy, ox, oy)
    except Exception:
        atk_elev, atk_cover, elev_adv = 0.0, 0.0, 1.0

    dist = math.hypot(cx - ox, cy - oy)
    ef = _engagement_factor(dist)
    los = _line_of_sight_quality(cx, cy, ox, oy)

    if ef <= 0 and dist > _ARTILLERY_RANGE:
        return -1.0, {}

    # ── 점수 구성 요소 (0~1 범위로 정규화) ───────────────────────
    # 1. 고도 우위: 1.40이면 최고, 0.75이면 최저
    elev_score = (elev_adv - 0.75) / (1.40 - 0.75)        # 0~1

    # 2. 아군 엄폐 (공격 위치에서 아군이 얼마나 보호받는가)
    atk_cover_score = atk_cover / 0.65                      # 0~1

    # 3. 적 노출 (엄폐가 낮을수록 피해 효율 높음)
    target_exposure_score = 1.0 - def_cover / 0.65         # 0~1

    # 4. 교전 효율 (유효 사거리 내 교전 가능성)
    ef_score = ef if ef > 0 else 0.2  # 포병은 ef=0 가능, 부분 점수

    # 5. 시선 품질 (LOS)
    los_score = los

    # 가중 합산 (가중치 합 = 1.0)
    raw_score = (
        elev_score          * 0.30
        + atk_cover_score   * 0.25
        + target_exposure_score * 0.20
        + ef_score          * 0.15
        + los_score         * 0.10
    ) * 100.0  # 0~100 스케일

    # 전술 메모리 패널티/보너스 적용 (현재 교전 상황 컨텍스트 전달)
    try:
        from wargame.harness.tactical_memory import get_tactical_memory
        score = get_tactical_memory().apply_penalties(cx, cy, raw_score, current_context)
    except Exception:
        score = raw_score

    detail = {
        "elevation_m":         round(atk_elev, 1),
        "cover":               round(atk_cover, 3),
        "distance_m":          int(dist),
        "elevation_advantage": round(elev_adv, 2),
        "engagement_factor":   round(ef, 2),
        "los_quality":         round(los, 2),
        "tactical_penalty_applied": score != raw_score,
        "score_breakdown": {
            "elevation":        round(elev_score * 30, 1),
            "atk_cover":        round(atk_cover_score * 25, 1),
            "target_exposure":  round(target_exposure_score * 20, 1),
            "engagement":       round(ef_score * 15, 1),
            "los":              round(los_score * 10, 1),
        },
    }
    return score, detail


def _recommend_attack_methods(
    best_pos: dict,
    target_unit_type: str,
    target_cover: float,
    target_elev: float,
    available_blufor: List[dict],
) -> List[dict]:
    """
    최적 공격 위치·적 상태를 바탕으로 공격 수단 우선순위를 결정.
    """
    methods = []
    elev_adv = best_pos.get("elevation_advantage", 1.0)
    ef       = best_pos.get("engagement_factor", 0.0)
    atk_cov  = best_pos.get("cover", 0.0)
    dist_m   = best_pos.get("distance_m", 5000)

    # ── 직접 지상 공격 ────────────────────────────────────────────
    if ef >= 0.4:
        priority = "1순위" if elev_adv >= 1.10 and atk_cov >= 0.20 else "2순위"
        reason_parts = []
        if elev_adv >= 1.25:
            reason_parts.append(f"고지대 우위(×{elev_adv:.2f})")
        if target_cover <= 0.20:
            reason_parts.append("적 노출 지형")
        if atk_cov >= 0.30:
            reason_parts.append(f"아군 엄폐 양호({atk_cov:.2f})")
        methods.append({
            "method": "직접 지상 화력",
            "priority": priority,
            "reason": " / ".join(reason_parts) if reason_parts else "교전 거리 내",
        })

    # ── 측방 포위 기동 ────────────────────────────────────────────
    if elev_adv < 1.00 or target_cover >= 0.35:
        methods.append({
            "method": "측방 포위 기동",
            "priority": "1순위" if elev_adv < 0.93 else "2순위",
            "reason": (
                "정면 고도 불리 — 측방에서 취약 지점 공략 권고"
                if elev_adv < 0.93
                else "적 엄폐 양호 — 측면 기동으로 엄폐 우회"
            ),
        })

    # ── 포병/자주포 간접 사격 ─────────────────────────────────────
    has_artillery = any(u.get("unit_type") == "자주포" for u in available_blufor)
    if target_cover <= 0.30 or (ef < 0.3 and dist_m <= _ARTILLERY_RANGE):
        arty_priority = "1순위" if target_cover <= 0.15 and not has_artillery else "2순위"
        methods.append({
            "method": "포병 간접 사격",
            "priority": arty_priority,
            "reason": (
                f"적 엄폐 낮음({target_cover:.2f}) — 면제압 효과 극대화"
                if target_cover <= 0.25
                else f"직사 불리 거리({dist_m:.0f}m) — 간접화력으로 제압 후 기동"
            ),
        })

    # ── 공중지원 (CAS/타격) ───────────────────────────────────────
    if target_cover <= 0.35 or target_unit_type in ("전차", "자주포"):
        cas_reason_parts = []
        if target_unit_type in ("전차", "자주포"):
            cas_reason_parts.append(f"{target_unit_type} 대응 공중 정밀 타격 유효")
        if target_cover <= 0.20:
            cas_reason_parts.append("노출 지형 — CAS 피해 최대화")
        methods.append({
            "method": "공중 지원 (CAS/타격)",
            "priority": "1순위" if target_unit_type == "전차" and target_cover <= 0.20 else "2순위",
            "reason": " / ".join(cas_reason_parts) if cas_reason_parts else "보조 화력 지원",
        })

    # ── 정찰 후 확인사격 ─────────────────────────────────────────
    if best_pos.get("los_quality", 1.0) < 0.5:
        methods.append({
            "method": "정찰 탐지 후 확인사격",
            "priority": "사전 조치",
            "reason": f"LOS 품질 낮음({best_pos.get('los_quality',0):.2f}) — 정찰부대 선도 후 사격 조정 필요",
        })

    # 우선순위 정렬 (1순위 → 2순위 → 사전조치 순)
    order = {"1순위": 0, "2순위": 1, "사전 조치": 2}
    methods.sort(key=lambda m: order.get(m["priority"], 3))
    return methods


def _recommend_units(
    target_unit_type: str,
    available_blufor: List[dict],
) -> List[str]:
    """상성 기반 권고 아군 부대 목록."""
    try:
        from tools.wargame_strategy_tool import _get_matchup
    except Exception:
        return [u["id"] for u in available_blufor[:3]]

    scored = []
    for u in available_blufor:
        _, mult, _ = _get_matchup(u.get("unit_type", ""), target_unit_type)
        scored.append((mult, u["id"], u.get("unit_type", "")))
    scored.sort(reverse=True)
    return [f"{uid}({utype})" for _, uid, utype in scored[:3]]


# ── 메인 툴 ─────────────────────────────────────────────────────────

def _route_interdict_bonus(
    cx: float, cy: float,
    predicted_routes: list,
) -> float:
    """
    후보 공격 위치 (cx, cy) 가 예측된 OPFOR 경로를 얼마나 차단할 수 있는지 보너스 반환.

    평가 기준:
    - 예측 경로 경유지와의 LOS + 사거리 내 여부
    - 차단 가능한 경유지·경로 수에 비례한 보너스 (+5 ~ +25)
    """
    if not predicted_routes:
        return 0.0

    INTERDICT_RANGE = 8_000.0   # 포병 사거리 내 경로 차단 가능
    bonus = 0.0

    for route_entry in predicted_routes:
        for route in route_entry.get("routes", []):
            threat = route.get("threat_level", "low")
            threat_mult = {"high": 1.5, "medium": 1.0, "low": 0.5}.get(threat, 1.0)
            # waypoints_m 우선 사용 (미터 좌표), 없으면 waypoints fallback
            wps = route.get("waypoints_m", route.get("waypoints", []))
            for wp in wps[:-1]:  # 마지막(목표) 제외
                dist = math.hypot(cx - wp[0], cy - wp[1])
                if dist <= INTERDICT_RANGE:
                    # LOS 품질 반영
                    los = _line_of_sight_quality(cx, cy, wp[0], wp[1])
                    if los >= 0.4:
                        bonus += 3.0 * threat_mult * los

            # 핵심 차단 포인트 추가 보너스 (key_chokepoints_m 우선 사용)
            cps = route.get("key_chokepoints_m", route.get("key_chokepoints", []))
            for cp in cps:
                dist = math.hypot(cx - cp[0], cy - cp[1])
                if dist <= INTERDICT_RANGE:
                    los = _line_of_sight_quality(cx, cy, cp[0], cp[1])
                    if los >= 0.4:
                        bonus += 5.0 * threat_mult * los

    return min(bonus, 25.0)   # 최대 보너스 캡


@tool
def get_optimal_attack_positions(
    top_n: int = 3,
    opfor_routes_json: str = "",
) -> dict:
    """
    detected OPFOR를 기준으로 두 가지만 계산해 간결하게 반환한다(컨텍스트 절약):
      1) 공중지원 우선순위 스케줄 — 잔여 공중지원 횟수 내에서 목표 우선순위
      2) 각 BLUFOR 부대별 주요 고지 — 담당(최근접) 타겟 방향의 최적 고지대 사격 위치

    후보 위치 다수·점수 세부내역·온톨로지 컨텍스트는 반환하지 않는다.

    Args:
        top_n: (미사용, 하위호환) 이전 버전에서 타겟별 반환하던 위치 수. 현재 무시된다.
        opfor_routes_json: (미사용, 하위호환) predict_opfor_routes() 결과 JSON 문자열. 현재 무시된다.

    Returns:
        {
            "status": "success" | "engine_not_ready" | "no_detected_targets" | "error",
            "game_time": str,
            "air_remaining": int,
            "air_support_schedule": [
                {"priority": int, "target_unit_id": str, "target_type": str,
                 "target": [lat, lon], "method": "cas|strike|helicopter", "reason": str}, ...
            ],
            "artillery_support_schedule": [   # 위협도 상위 표적 — 공중지원과 같은 좌표 동시 포병(횟수 무제한)
                {"priority": int, "target_unit_id": str, "target_type": str,
                 "target": [lat, lon], "method": "artillery", "concurrent_with_air": True,
                 "reason": str}, ...
            ],
            "unit_key_highground": [
                {"unit_id": str, "unit_type": str, "target_unit_id": str,
                 "position": [lat, lon], "x_m": int, "y_m": int,
                 "elevation_m": float, "elevation_advantage": float}, ...
            ]
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    # opfor_routes_json 파싱
    predicted_routes: list = []
    if opfor_routes_json:
        try:
            predicted_routes = json.loads(opfor_routes_json)
            if not isinstance(predicted_routes, list):
                predicted_routes = []
        except Exception:
            predicted_routes = []

    try:
        from wargame.terrain import terrain as _t
    except Exception as e:
        return {"status": "error", "message": f"지형 모듈 로드 실패: {e}"}

    try:
        state = _wargame_engine.get_state()
        intel = state.get("intelligence", {}).get("BLUFOR", [])
        all_units = state.get("units", [])

        # 탐지된 OPFOR 유닛만 사용 (approximate 포함, lost 제외)
        detected_targets = [
            e for e in intel
            if e["status"] in ("detected", "approximate")
        ]
        if not detected_targets:
            return {
                "status": "no_detected_targets",
                "game_time": state.get("game_time_str", ""),
                "message": "현재 탐지된 OPFOR가 없습니다. 정찰부대를 파견하여 적 위치를 파악하세요.",
                "attack_recommendations": [],
            }

        # 활성 BLUFOR 부대
        blufor_active = [
            u for u in all_units
            if u["side"] == "BLUFOR" and u["status"] != "destroyed"
        ]

        # 공중지원 잔여 횟수
        air_use     = state.get("air_use_count", {})
        air_limit   = state.get("air_use_limit", 5)
        air_remaining = max(0, air_limit - air_use.get("BLUFOR", 0))

        # 현재 교전 상황 컨텍스트 빌드 (전술 메모리 패널티 유사도 계산에 사용)
        opfor_units = [u for u in all_units if u["side"] == "OPFOR" and u["status"] != "destroyed"]
        blufor_cp = sum(u.get("combat_power", 100) for u in blufor_active) / max(len(blufor_active), 1)
        opfor_cp  = sum(u.get("combat_power", 100) for u in opfor_units) / max(len(opfor_units), 1)
        opfor_positions = [
            [float(u.get("x", 0)), float(u.get("y", 0))] for u in opfor_units
        ]
        # 적 위치 중심점 기준 지형 프로파일 샘플링
        _terrain_ctx = {}
        if opfor_positions:
            try:
                from wargame.harness.tactical_memory import sample_terrain_profile
                cx_op = sum(p[0] for p in opfor_positions) / len(opfor_positions)
                cy_op = sum(p[1] for p in opfor_positions) / len(opfor_positions)
                _terrain_ctx = sample_terrain_profile(cx_op, cy_op, radius=3000.0)
            except Exception:
                pass
        _current_context = {
            "enemy_unit_types": list({u.get("unit_type", "unknown") for u in opfor_units}),
            "enemy_count": len(opfor_units),
            "enemy_positions": opfor_positions,
            "friendly_unit_types": list({u.get("unit_type", "unknown") for u in blufor_active}),
            "force_ratio": blufor_cp / max(opfor_cp, 0.01),
            "terrain": _terrain_ctx,
        }

        # ── 후보 위치 생성: 16방향 × 4거리 (고지 계산용) ────────────
        CANDIDATE_DISTANCES = [1_200, 2_000, 3_000, 4_500]
        CANDIDATE_ANGLES = [i * (360 / 16) for i in range(16)]  # 22.5° 간격

        def _air_method_for(unit_type, combat_power=None):
            ut = unit_type or ""
            # 고가치·점표적(전투력 높음) 또는 포병 → 정밀타격(strike) 적극 활용
            if (combat_power is not None and combat_power >= _STRIKE_CP_THRESHOLD) \
                    or "자주포" in ut or "포병" in ut:
                return "strike"       # 좁은 반경 고위력 한 방 — 핵심 표적 제거
            if "전차" in ut or "장갑" in ut or "기갑" in ut:
                return "helicopter"   # 기갑 목표 → 헬기
            return "cas"              # 그 외 → 근접항공지원

        def _best_highground(ox, oy):
            """타겟(ox,oy) 주변 후보 중 고지대 우위가 가장 큰 사격 위치."""
            tcover = _t.cover_factor(ox, oy)
            best = None
            for dist_m in CANDIDATE_DISTANCES:
                for angle_deg in CANDIDATE_ANGLES:
                    ar = math.radians(angle_deg)
                    cx = max(0.0, min(29_999.0, ox + math.cos(ar) * dist_m))
                    cy = max(0.0, min(29_999.0, oy + math.sin(ar) * dist_m))
                    score, detail = _score_attack_position(cx, cy, ox, oy, tcover, _current_context)
                    if score < 0:
                        continue
                    key = (detail.get("elevation_advantage", 0.0), score)
                    if best is None or key > best[0]:
                        best = (key, cx, cy, detail)
            return best

        # ① 공중지원 우선순위 스케줄 — detected 타겟을 전투력 높은 순으로 잔여 횟수만큼
        detected_only = [e for e in detected_targets if e["status"] == "detected"]
        detected_only.sort(key=lambda e: (e.get("combat_power") or 0.0), reverse=True)
        air_support_schedule = []
        for i, tgt in enumerate(detected_only[:air_remaining], 1):
            t_lat, t_lon = xy_to_latlon(tgt["known_x"], tgt["known_y"])
            tx, ty = tgt["known_x"], tgt["known_y"]
            # 아군 부대가 이 표적과 인접(근접 교전)이면 광역 CAS 대신 정밀타격(strike, 좁은 반경)
            near_friendly = min(
                (math.hypot(u.get("x", 0) - tx, u.get("y", 0) - ty) for u in blufor_active),
                default=1e9,
            )
            if near_friendly <= _CLOSE_CONTACT_RANGE:
                method = "strike"
                reason = (f"아군 근접 {near_friendly:.0f}m — 정밀타격(좁은 반경) 권고 "
                          f"/ 전투력 {tgt.get('combat_power')}")
            else:
                method = _air_method_for(tgt.get("unit_type"), tgt.get("combat_power"))
                reason = f"전투력 {tgt.get('combat_power')} — 우선순위 {i}/{air_remaining}"
            air_support_schedule.append({
                "priority": i,
                "target_unit_id": tgt["unit_id"],
                "target_type": tgt.get("unit_type") or "미확인",
                "target": [t_lat, t_lon],
                "method": method,
                "reason": reason,
            })

        # ①-b 포병 동시지원 스케줄 — 위협도 상위 표적에는 공중지원과 '같은 좌표'로 포병(artillery)
        #      병행 투사(화력 집중). artillery 는 항공 CAS 5회 제한과 무관(횟수 제한 없음)하며,
        #      공중지원과 동시에 투사된다.
        _ARTY_TOP_N = 2  # 위협도 상위 N개 표적에 공중+포병 동시지원 권고
        artillery_support_schedule = []
        for i, tgt in enumerate(detected_only[:_ARTY_TOP_N], 1):
            t_lat, t_lon = xy_to_latlon(tgt["known_x"], tgt["known_y"])
            artillery_support_schedule.append({
                "priority": i,
                "target_unit_id": tgt["unit_id"],
                "target_type": tgt.get("unit_type") or "미확인",
                "target": [t_lat, t_lon],
                "method": "artillery",
                "concurrent_with_air": True,
                "reason": (f"위협도 상위 {i} — 공중지원과 동일 좌표 동시 포병 투사(화력 집중), "
                           f"전투력 {tgt.get('combat_power')}"),
            })

        # ② 각 BLUFOR 부대별 주요 고지 — 담당(최근접) 타겟 방향의 최적 고지대 사격 위치
        unit_key_highground = []
        for u in blufor_active:
            if u.get("unit_type") == "정찰":
                continue
            ux, uy = float(u.get("x", 0)), float(u.get("y", 0))
            tgt = min(
                detected_targets,
                key=lambda e: (e["known_x"] - ux) ** 2 + (e["known_y"] - uy) ** 2,
            )
            best = _best_highground(tgt["known_x"], tgt["known_y"])
            if best is None:
                continue
            _key, cx, cy, detail = best
            p_lat, p_lon = xy_to_latlon(cx, cy)
            unit_key_highground.append({
                "unit_id": u["id"],
                "unit_type": u.get("unit_type", ""),
                "target_unit_id": tgt["unit_id"],
                "position": [p_lat, p_lon],
                "x_m": int(cx),
                "y_m": int(cy),
                "elevation_m": detail.get("elevation_m"),
                "elevation_advantage": detail.get("elevation_advantage"),
            })

        return {
            "status": "success",
            "game_time": state.get("game_time_str", "00:00:00"),
            "air_remaining": air_remaining,
            "air_support_schedule": air_support_schedule,
            "artillery_support_schedule": artillery_support_schedule,
            "unit_key_highground": unit_key_highground,
        }

    except Exception as e:
        logger.error(f"get_optimal_attack_positions error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}