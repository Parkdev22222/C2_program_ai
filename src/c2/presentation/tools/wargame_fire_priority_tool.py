"""
화력 지원(포병 + 근접 항공) 타격 우선순위 스케줄링 도구 (smolagents Tool)

탐지된 OPFOR를 **부대 병종(type)과 현황(전투력·근접·탐지)**을 함께 반영한 위협 점수로
정렬해, 포병/항공 CAS 타격 우선순위를 산출한다.

위협 점수 = TYPE_THREAT[병종] × cp_factor × prox_factor × det_factor
  - TYPE_THREAT: 병종별 가치(자주포=최고). 만편성·탐지 상태에서 자주포가 항상 1순위가 되도록
    가중치를 잡는다(자주포 최솟값 > 전차 최댓값).
  - cp_factor : 전투력이 높을수록(생존·화력 여력) 위협 큼.
  - prox_factor: 아군에 가까울수록 즉각 위협 큼(영향은 부차적 — 병종이 순위를 지배).
  - det_factor: detected=1.0 / approximate=0.7 (불확실 표적은 감점).
"""
import logging
import math


from c2.domain.wargame.coordinates import xy_to_latlon

logger = logging.getLogger(__name__)

_wargame_engine = None


def register_wargame_engine(engine):
    global _wargame_engine
    _wargame_engine = engine


# ── 병종별 위협 가중치 (0~1, 높을수록 우선 타격) ──────────────────────
# 자주포(SPG): 고가치 원거리 화력 → 대포병 최우선. 만편성·탐지 시 항상 1순위가 되도록
# 자주포 최솟값(=1.0×prox 하한 0.9=0.90) > 전차 최댓값(=0.82) 이 되게 잡는다.
_TYPE_THREAT = {
    "자주포": 1.00,
    "전차": 0.82,
    "대전차": 0.78,
    "기계화보병": 0.60,
    "정찰": 0.40,
}
_TYPE_THREAT_DEFAULT = 0.55

# 병종별 타격 사유(설명용)
_TYPE_REASON = {
    "자주포": "자주포(고가치 원거리 화력) — 대포병 최우선 제거",
    "전차": "전차(기갑 위협) — 대기갑 타격 우선",
    "대전차": "대전차(아군 기갑 위협) — 조기 제압",
    "기계화보병": "기계화보병 — 병력 밀집 시 광역 타격",
    "정찰": "정찰(적 관측자산) — 제거 시 적 표적획득 저하",
}

_PROX_REF_M = 20_000.0        # 근접 위협 정규화 기준 거리(20km)
_CLOSE_CONTACT_RANGE = 1_500.0  # 아군이 이 거리 이내면 근접 교전 → 정밀타격(strike)
_ARTY_TOP_N = 3               # 위협 상위 N 표적에 포병 동시 배정(횟수 제한 없음)


def _threat_score(unit_type, cp, dist_to_friendly, det_status):
    base = _TYPE_THREAT.get(unit_type or "", _TYPE_THREAT_DEFAULT)
    cp_factor = 0.5 + 0.5 * max(0.0, min(100.0, cp or 0.0)) / 100.0     # 0.5~1.0
    prox = max(0.0, min(1.0, 1.0 - (dist_to_friendly / _PROX_REF_M)))
    prox_factor = 0.9 + 0.1 * prox                                     # 0.9~1.0
    det_factor = 1.0 if det_status == "detected" else 0.7
    return base * cp_factor * prox_factor * det_factor


def _air_method_for(unit_type, near_friendly):
    """병종·근접 여부 기반 항공 CAS 수단 선택."""
    if near_friendly:
        return "strike"          # 아군 근접 → 정밀타격(좁은 반경)
    ut = unit_type or ""
    if "자주포" in ut:
        return "strike"          # 점표적·고가치 → 정밀타격
    if "전차" in ut or "장갑" in ut or "기갑" in ut:
        return "helicopter"      # 기갑 목표 → 헬기
    return "cas"                 # 그 외 → 근접항공지원



def get_fire_priority_schedule() -> dict:
    """포병·근접 항공(CAS) 화력지원을 위한 타격 우선순위 스케줄을 산출합니다.

    탐지된 OPFOR를 **병종(자주포/전차/대전차/기계화보병/정찰)과 현황(전투력·아군 근접도·
    탐지 상태)**을 함께 반영한 위협 점수로 정렬해, 포병/항공 CAS 타격 순서를 제시합니다.
    자주포처럼 고가치 원거리 화력 자산이 상위로 오도록 병종 가중치를 반영합니다.

    Returns:
        {
          "status": "success" | "engine_not_ready" | "no_detected_targets" | "error",
          "air_remaining": int,   # 항공 CAS 잔여 횟수(5회 제한)
          "priorities": [
             {"rank": int, "target_unit_id": str, "target_type": str, "threat_score": float,
              "combat_power": float, "detect_status": str, "distance_km": float,
              "target": [lat, lon], "air_method": "cas|strike|helicopter",
              "artillery": bool, "reason": str}, ...
          ],
          "air_cas_schedule": [   # 우선순위대로 항공 CAS 배정(잔여 횟수 이내)
             {"priority": int, "target_unit_id": str, "target_type": str,
              "target": [lat, lon], "method": str, "reason": str}, ...
          ],
          "artillery_schedule": [ # 위협 상위 표적에 포병(횟수 무제한, 항공과 동시 투사)
             {"priority": int, "target_unit_id": str, "target_type": str,
              "target": [lat, lon], "method": "artillery", "reason": str}, ...
          ]
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}
    try:
        state = _wargame_engine.get_state()
        intel = state.get("intelligence", {}).get("BLUFOR", [])
        targets = [e for e in intel if e.get("status") in ("detected", "approximate")]
        if not targets:
            return {"status": "no_detected_targets", "message": "탐지된 OPFOR가 없습니다.", "priorities": []}

        blufor = [
            u for u in state.get("units", [])
            if u.get("side") == "BLUFOR" and u.get("status") != "destroyed"
        ]
        air_use = state.get("air_use_count", {})
        air_limit = state.get("air_use_limit", 5)
        air_remaining = max(0, air_limit - air_use.get("BLUFOR", 0))

        scored = []
        for e in targets:
            tx, ty = e.get("known_x", 0.0), e.get("known_y", 0.0)
            dist_to_friendly = min(
                (math.hypot(u.get("x", 0) - tx, u.get("y", 0) - ty) for u in blufor),
                default=_PROX_REF_M,
            )
            near_friendly = dist_to_friendly <= _CLOSE_CONTACT_RANGE
            cp = e.get("combat_power")
            cp = cp if isinstance(cp, (int, float)) else 100.0
            utype = e.get("unit_type") or "미상"
            score = _threat_score(utype, cp, dist_to_friendly, e.get("status"))
            lat, lon = xy_to_latlon(tx, ty)
            reason = _TYPE_REASON.get(utype, f"{utype} — 위협 평가")
            if near_friendly:
                reason += " / 아군 근접 → 정밀타격"
            scored.append({
                "target_unit_id": e.get("unit_id"),
                "target_type": utype,
                "threat_score": round(score, 4),
                "combat_power": round(cp, 1),
                "detect_status": e.get("status"),
                "distance_km": round(dist_to_friendly / 1000.0, 2),
                "target": [lat, lon],
                "air_method": _air_method_for(utype, near_friendly),
                "_near": near_friendly,
                "reason": reason,
            })

        scored.sort(key=lambda s: s["threat_score"], reverse=True)
        for i, s in enumerate(scored, 1):
            s["rank"] = i

        # 항공 CAS 스케줄 — 우선순위대로 잔여 횟수만큼
        air_cas_schedule = []
        for s in scored[:air_remaining]:
            air_cas_schedule.append({
                "priority": s["rank"],
                "target_unit_id": s["target_unit_id"],
                "target_type": s["target_type"],
                "target": s["target"],
                "method": s["air_method"],
                "reason": s["reason"],
            })

        # 포병 스케줄 — 위협 상위 N (항공 CAS와 동시 투사, 횟수 제한 없음)
        artillery_schedule = []
        for s in scored[:_ARTY_TOP_N]:
            artillery_schedule.append({
                "priority": s["rank"],
                "target_unit_id": s["target_unit_id"],
                "target_type": s["target_type"],
                "target": s["target"],
                "method": "artillery",
                "concurrent_with_air": True,
                "reason": s["reason"] + " (포병 동시 투사)",
            })

        # priorities 에 artillery 플래그 부여
        arty_ids = {a["target_unit_id"] for a in artillery_schedule}
        for s in scored:
            s["artillery"] = s["target_unit_id"] in arty_ids
            s.pop("_near", None)

        return {
            "status": "success",
            "air_remaining": air_remaining,
            "priorities": scored,
            "air_cas_schedule": air_cas_schedule,
            "artillery_schedule": artillery_schedule,
        }
    except Exception as e:
        logger.error(f"get_fire_priority_schedule error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
