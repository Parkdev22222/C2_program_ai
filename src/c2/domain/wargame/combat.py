"""
순수 전투/탐지 계산 (도메인 계층).

wargame/engine.py 의 God-object 에서 상태(self)·난수·I/O 에 의존하지 않는
순수 군사 계산(교전 사거리 계수 / 병종 상성 / 상태 배율 / LOS 품질)만 분리했다.

순수성 규약:
  - stdlib(typing) 와 c2.domain.wargame.terrain 만 import 한다.
  - 난수 발생기를 절대 참조하지 않는다 (난수 시퀀스는 엔진이 소유 → 골든 결정성 보존).
  - 엔진/tools/agent/ui/ontology 를 import 하지 않는다.
  - 로깅·파일·네트워크 등 부수효과 없음.

원본 위치(wargame/engine.py)는 이 모듈의 이름들을 다시 import 하여 사용한다.
"""

from typing import Dict, Tuple

from c2.domain.wargame.terrain import terrain

# ── 병종별 교전 사거리 ────────────────────────────────────────────────
_DIRECT_RANGE: Dict[str, float] = {
    "전차":      3_000.0,
    "기계화보병": 1_500.0,
    "대전차":    4_000.0,
    "자주포":        0.0,   # 직사 교전 불가, 별도 간접사격
    "정찰":        800.0,
}
_SUPPRESS_RANGE: Dict[str, float] = {
    "전차":      5_000.0,
    "기계화보병": 2_500.0,
    "대전차":    4_500.0,
    "자주포":        0.0,
    "정찰":      1_200.0,
}

# ── 병종 상성 계수 ────────────────────────────────────────────────────
_MATCHUP: Dict[Tuple[str, str], float] = {
    ("전차",       "전차"):        1.0,
    ("전차",       "기계화보병"):   1.4,
    ("전차",       "대전차"):       0.8,
    ("전차",       "자주포"):       1.2,
    ("전차",       "정찰"):         1.5,
    ("기계화보병", "전차"):         0.4,
    ("기계화보병", "기계화보병"):   1.0,
    ("기계화보병", "대전차"):       1.0,
    ("기계화보병", "자주포"):       0.8,
    ("기계화보병", "정찰"):         1.2,
    ("대전차",     "전차"):         2.0,
    ("대전차",     "기계화보병"):   0.7,
    ("대전차",     "대전차"):       0.9,
    ("대전차",     "자주포"):       1.5,
    ("대전차",     "정찰"):         0.8,
    ("자주포",     "전차"):         0.5,
    ("자주포",     "기계화보병"):   1.8,
    ("자주포",     "대전차"):       1.0,
    ("자주포",     "자주포"):       0.8,
    ("자주포",     "정찰"):         1.2,
    ("정찰",       "전차"):         0.2,
    ("정찰",       "기계화보병"):   0.3,
    ("정찰",       "대전차"):       0.3,
    ("정찰",       "자주포"):       0.4,
    ("정찰",       "정찰"):         0.5,
}


# ── 모듈 레벨 순수 헬퍼 ────────────────────────────────────────────────

def _los_quality(x1: float, y1: float, x2: float, y2: float) -> float:
    """
    두 좌표 간 시선(LOS) 품질 반환 (1.0=완전개방 / 0.0=완전차폐).
    경로를 8등분하여 중간 지형이 직선 고도 보간을 초과하면 차폐.
    """
    try:
        samples = 8
        e1 = terrain.elevation(x1, y1)
        e2 = terrain.elevation(x2, y2)
        worst_block = 0.0
        for i in range(1, samples):
            t = i / samples
            sx = x1 + (x2 - x1) * t
            sy = y1 + (y2 - y1) * t
            mid_e = terrain.elevation(sx, sy)
            interp_e = e1 + (e2 - e1) * t
            block = max(0.0, (mid_e - interp_e) / 80.0)
            worst_block = max(worst_block, block)
        return max(0.0, 1.0 - worst_block)
    except Exception:
        return 1.0


def _engagement_factor(attacker_type: str, dist: float) -> float:
    """병종별 사거리 기반 교전 효과 계수 (0~1)."""
    if attacker_type == "자주포":
        return 0.0   # 직사 교전 불가
    d_range = _DIRECT_RANGE.get(attacker_type, 2_000.0)
    s_range = _SUPPRESS_RANGE.get(attacker_type, 3_000.0)
    inner   = d_range * 0.4
    if dist <= inner:
        return 1.0
    elif dist <= d_range:
        return 1.0 - (dist - inner) / (d_range - inner) * 0.5
    elif dist <= s_range:
        return 0.5 - (dist - d_range) / (s_range - d_range) * 0.4
    return 0.0


def _matchup_factor(atk_type: str, def_type: str) -> float:
    """공격자-방어자 병종 상성 계수."""
    return _MATCHUP.get((atk_type, def_type), 1.0)


def _status_firepower_mult(status: str) -> float:
    """상태별 화력 배율."""
    return {"active": 1.0, "degraded": 0.8, "suppressed": 0.3}.get(status, 0.0)


def _status_speed_mult(status: str) -> float:
    """상태별 이동속도 배율."""
    return {"active": 1.0, "degraded": 0.7, "suppressed": 0.0}.get(status, 0.0)
