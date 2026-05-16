"""
워게임 최적 공격 위치·수단 추천 도구 (smolagents Tool)

BLUFOR 인텔에서 탐지된 OPFOR 위치(고도·엄폐 포함)를 기반으로,
아군 피해를 최소화하면서 적군에 최대 피해를 줄 수 있는
공격 위치 및 공격 수단을 산출합니다.
"""
import math
import logging
from typing import List, Tuple
from smolagents import tool

logger = logging.getLogger(__name__)

_wargame_engine = None


def register_wargame_engine(engine):
    global _wargame_engine
    _wargame_engine = engine


# ── 교전 거리 상수 ─────────────────────────────────────────────────
_ENGAGEMENT_RANGE = 2_500.0
_SUPPRESSION_RANGE = 4_000.0
_ARTILLERY_RANGE = 8_000.0   # 자주포 간접사격 사거리


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

@tool
def get_optimal_attack_positions(top_n: int = 3) -> dict:
    """
    BLUFOR 인텔에 탐지된 OPFOR 유닛의 위치·고도·엄폐를 분석하여,
    아군 피해를 최소화하면서 적군 피해를 극대화할 수 있는
    최적 공격 위치와 공격 수단을 반환합니다.

    후보 위치 평가 기준:
      - 고도 우위 (공격자 위치가 높을수록 유리, 가중치 30%)
      - 아군 엄폐 (공격 위치의 엄폐 양호할수록 아군 피해 감소, 25%)
      - 적 노출 (목표 위치 엄폐 낮을수록 적 피해 증가, 20%)
      - 교전 효율 (유효 사거리 내 교전 가능성, 15%)
      - 시선(LOS) 품질 (지형 차폐 없을수록 유리, 10%)

    Args:
        top_n: 타겟별 반환할 최적 위치 수 (기본 3)

    Returns:
        {
            "status": "success" | "engine_not_ready" | "no_detected_targets",
            "game_time": str,
            "attack_recommendations": [
                {
                    "target": {
                        "unit_id": str,
                        "unit_type": str,
                        "x_m": int,
                        "y_m": int,
                        "elevation_m": float,
                        "cover": float,
                        "combat_power": float | None,
                        "detection_status": str
                    },
                    "optimal_positions": [
                        {
                            "rank": int,
                            "x_m": int,
                            "y_m": int,
                            "elevation_m": float,
                            "cover": float,
                            "distance_m": int,
                            "elevation_advantage": float,
                            "engagement_factor": float,
                            "los_quality": float,
                            "score": float,          # 0~100
                            "score_breakdown": dict,
                            "attack_methods": [
                                {"method": str, "priority": str, "reason": str}, ...
                            ],
                            "recommended_units": [str, ...]
                        }, ...
                    ],
                    "summary": str   # 한줄 공격 권고 요약
                }, ...
            ]
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

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

        # ── 후보 위치 생성: 16방향 × 4거리 ─────────────────────────
        # 거리: 1.2km / 2.0km / 3.0km / 4.5km (근거리~원거리 포병)
        CANDIDATE_DISTANCES = [1_200, 2_000, 3_000, 4_500]
        CANDIDATE_ANGLES = [i * (360 / 16) for i in range(16)]  # 22.5° 간격

        recommendations = []

        for target_entry in detected_targets:
            ox = target_entry["known_x"]
            oy = target_entry["known_y"]
            target_unit_type = target_entry.get("unit_type") or "미확인"
            target_cover = _t.cover_factor(ox, oy)
            target_elev  = _t.elevation(ox, oy)

            # ── 후보 위치 평가 ───────────────────────────────────
            candidates = []
            for dist_m in CANDIDATE_DISTANCES:
                for angle_deg in CANDIDATE_ANGLES:
                    angle_rad = math.radians(angle_deg)
                    cx = ox + math.cos(angle_rad) * dist_m
                    cy = oy + math.sin(angle_rad) * dist_m
                    cx = max(0.0, min(29_999.0, cx))
                    cy = max(0.0, min(29_999.0, cy))

                    score, detail = _score_attack_position(cx, cy, ox, oy, target_cover, _current_context)
                    if score < 0:
                        continue

                    candidates.append({
                        "x_m":   int(cx),
                        "y_m":   int(cy),
                        "score": round(score, 1),
                        **detail,
                    })

            if not candidates:
                continue

            # 점수 기준 정렬 → 상위 top_n
            candidates.sort(key=lambda c: c["score"], reverse=True)
            top_candidates = candidates[:top_n]

            # ── 각 후보 위치에 공격 수단·권고 부대 추가 ──────────
            optimal_positions = []
            for rank, cand in enumerate(top_candidates, 1):
                methods = _recommend_attack_methods(
                    cand, target_unit_type, target_cover,
                    target_elev, blufor_active
                )
                units = _recommend_units(target_unit_type, blufor_active)
                optimal_positions.append({
                    "rank": rank,
                    **cand,
                    "attack_methods": methods,
                    "recommended_units": units,
                })

            # ── 요약 문자열 ──────────────────────────────────────
            best = optimal_positions[0]
            best_method = best["attack_methods"][0]["method"] if best["attack_methods"] else "직접 공격"
            elev_note = (
                f"고지대 우위 ×{best['elevation_advantage']:.2f}"
                if best["elevation_advantage"] >= 1.10
                else "고도 불리 — 측방/간접화력 권고"
            )
            summary = (
                f"{target_entry['unit_id']}({target_unit_type}) 공략: "
                f"최적 위치 ({best['x_m']}m, {best['y_m']}m) "
                f"고도{best['elevation_m']:.0f}m / {elev_note} / "
                f"권고 수단: {best_method} / 종합점수 {best['score']:.0f}점"
            )

            recommendations.append({
                "target": {
                    "unit_id":         target_entry["unit_id"],
                    "unit_type":       target_unit_type,
                    "x_m":             int(ox),
                    "y_m":             int(oy),
                    "elevation_m":     round(target_elev, 1),
                    "cover":           round(target_cover, 3),
                    "combat_power":    target_entry.get("combat_power"),
                    "detection_status": target_entry["status"],
                },
                "optimal_positions": optimal_positions,
                "summary": summary,
            })

        # 가장 점수가 높은 타겟 우선 정렬
        recommendations.sort(
            key=lambda r: r["optimal_positions"][0]["score"] if r["optimal_positions"] else 0,
            reverse=True,
        )

        return {
            "status": "success",
            "game_time": state.get("game_time_str", "00:00:00"),
            "total_targets": len(recommendations),
            "attack_recommendations": recommendations,
        }

    except Exception as e:
        logger.error(f"get_optimal_attack_positions error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}