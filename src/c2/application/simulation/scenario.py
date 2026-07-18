"""
워게임 시나리오 정의 (철원 대대급 6 vs 6, 한국어 중대명).

기본 시나리오는 `setup_cheorwon_bn()` 하나이며, 모든 부대명은 한국어 중대명을 사용한다.

  BLUFOR (아군, 남서부):
    보병1중대·보병2중대·보병3중대 (기계화보병)
    전차중대 (전차)
    대전차중대 (대전차)
    자주포중대 (자주포 — K9A1)

  OPFOR (적군, 북동부):
    적보병1중대·적보병2중대·적보병3중대 (기계화보병)
    적전차중대 (전차)
    적대전차중대 (대전차)
    적자주포중대 (자주포 — M1978 곡산)

  부대 유형별 특성:
    기계화보병 (MechInf) : 화력100, 속도2.5 m/s
    전차       (Armor)   : 화력155~160, 속도2.0 m/s
    대전차     (AT)      : 화력 85~90, 속도2.2 m/s
    자주포     (SPG)     : 화력130, 속도1.8 m/s

`setup_custom_scenario()`로 임의 편성도 가능하다.
"""

import math as _math
import random as _rng

from c2.domain.wargame.unit import Unit


# ── 랜덤 배치 구역 정의 ────────────────────────────────────────────
# BLUFOR: 남서부 분지 (대대 정면 ~5km)
_BLUFOR_ZONE = dict(x_min=5_000, x_max=10_000, y_min=5_000, y_max=10_000)
# OPFOR : 북동부 고원 (대대 정면 ~5km)
_OPFOR_ZONE  = dict(x_min=18_000, x_max=23_000, y_min=18_000, y_max=23_000)
# 같은 진영 부대 간 최소 이격 거리
_MIN_SEP = 1_500.0


def _pick_pos(zone: dict, placed: list, min_sep: float = _MIN_SEP, tries: int = 60):
    """충돌 없이 구역 내 랜덤 좌표 반환."""
    for _ in range(tries):
        x = _rng.uniform(zone["x_min"], zone["x_max"])
        y = _rng.uniform(zone["y_min"], zone["y_max"])
        if all(_math.hypot(x - px, y - py) >= min_sep for px, py in placed):
            return x, y
    # tries 초과 시 중앙값 반환 (이격 조건 포기)
    return (zone["x_min"] + zone["x_max"]) / 2, (zone["y_min"] + zone["y_max"]) / 2


def setup_cheorwon_bn() -> list:
    """철원 축선 기계화대대 교전 (가상) — 6 vs 6.

    docs/scenario_cheorwon.md 반영판. 변경점:
      - 정찰 부대 없음: 양측 정찰(보병3중대/적보병3중대)을 기계화보병 중대로 대체.
        정찰은 UAV가 담당한다고 가정 → 엔진 full_recon 모드로 처음부터 전 위치 detected.
      - 자주포 실사거리: K9(자주포중대) 40km / 북한 곡산(적자주포중대) 60km(RAP) 를 indirect_range로 반영.
    """
    return [
        # ── BLUFOR (대한민국) — 남서부 (대대 정면 ~5km) ──────────────
        Unit(id="보병1중대", side="BLUFOR", unit_type="기계화보병",
             x=7_000.0, y=6_000.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#1E88E5"),
        Unit(id="보병2중대", side="BLUFOR", unit_type="기계화보병",
             x=8_000.0, y=7_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#42A5F5"),
        Unit(id="보병3중대", side="BLUFOR", unit_type="기계화보병",
             x=9_500.0, y=9_000.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#26C6DA"),
        Unit(id="전차중대", side="BLUFOR", unit_type="전차",
             x=6_000.0, y=7_000.0,
             combat_power=100.0, firepower_index=160.0, max_speed=6.0,
             status="active", waypoints=[], current_action="hold", color="#00BCD4"),
        Unit(id="대전차중대", side="BLUFOR", unit_type="대전차",
             x=9_000.0, y=6_000.0,
             combat_power=100.0, firepower_index=90.0, max_speed=5.5,
             status="active", waypoints=[], current_action="hold", color="#B3E5FC"),
        Unit(id="자주포중대", side="BLUFOR", unit_type="자주포",     # K9A1(실제 40km) — 게임 유효 15km
             x=5_500.0, y=5_500.0,
             combat_power=100.0, firepower_index=130.0, max_speed=4.0,
             indirect_range=15_000.0,
             status="active", waypoints=[], current_action="hold", color="#4DD0E1"),

        # ── OPFOR (북한) — 북동부 (대대 정면 ~5km) ──────────────────
        Unit(id="적보병1중대", side="OPFOR", unit_type="기계화보병",
             x=20_000.0, y=19_000.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#E53935"),
        Unit(id="적보병2중대", side="OPFOR", unit_type="기계화보병",
             x=19_000.0, y=20_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#EF5350"),
        Unit(id="적보병3중대", side="OPFOR", unit_type="기계화보병",
             x=18_500.0, y=18_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#FF8A65"),
        Unit(id="적전차중대", side="OPFOR", unit_type="전차",
             x=21_000.0, y=20_000.0,
             combat_power=100.0, firepower_index=155.0, max_speed=6.0,
             status="active", waypoints=[], current_action="hold", color="#FF7043"),
        Unit(id="적대전차중대", side="OPFOR", unit_type="대전차",
             x=21_500.0, y=21_500.0,
             combat_power=100.0, firepower_index=85.0, max_speed=5.5,
             status="active", waypoints=[], current_action="hold", color="#FFAB91"),
        Unit(id="적자주포중대", side="OPFOR", unit_type="자주포",         # M1978 곡산(실제 60km) — 게임 유효 18km
             x=22_500.0, y=22_500.0,
             combat_power=100.0, firepower_index=130.0, max_speed=4.0,
             indirect_range=18_000.0,
             status="active", waypoints=[], current_action="hold", color="#FFCCBC"),
    ]


# 부대 유형별 스탯 (setup_custom_scenario에서 사용)
UNIT_TYPE_SPECS: dict = {
    "기계화보병": {"firepower_index": 100.0, "max_speed": 5.0},
    "전차":       {"firepower_index": 160.0, "max_speed": 6.0},
    "정찰":       {"firepower_index":  45.0, "max_speed": 7.0},
    "대전차":     {"firepower_index":  90.0, "max_speed": 5.5},
    "자주포":     {"firepower_index": 130.0, "max_speed": 4.0},
}

# 시나리오별 색상 팔레트
_BLUFOR_COLORS = ["#1E88E5", "#42A5F5", "#00BCD4", "#80DEEA", "#B3E5FC", "#26C6DA", "#4DD0E1", "#29B6F6"]
_OPFOR_COLORS  = ["#E53935", "#EF5350", "#FF7043", "#FFAB91", "#FFCCBC", "#FF8A65", "#FF5722", "#F44336"]


def setup_custom_scenario(blufor_defs: list, opfor_defs: list) -> list:
    """
    사용자 정의 시나리오 생성.

    Args:
        blufor_defs: [{"id": "보병1중대", "unit_type": "기계화보병", "x": None, "y": None}, ...]
                     x, y가 None이면 BLUFOR 구역 내 랜덤 배치
        opfor_defs:  [{"id": "적보병1중대",  "unit_type": "전차",       "x": None, "y": None}, ...]
                     x, y가 None이면 OPFOR 구역 내 랜덤 배치

    Returns:
        list[Unit]
    """
    units: list = []
    default_specs = UNIT_TYPE_SPECS["기계화보병"]

    blufor_placed: list = []
    for i, bd in enumerate(blufor_defs):
        specs = UNIT_TYPE_SPECS.get(bd.get("unit_type", "기계화보병"), default_specs)
        x = bd.get("x")
        y = bd.get("y")
        if x is None or y is None:
            x, y = _pick_pos(_BLUFOR_ZONE, blufor_placed)
        blufor_placed.append((float(x), float(y)))
        units.append(Unit(
            id=bd["id"],
            side="BLUFOR",
            unit_type=bd.get("unit_type", "기계화보병"),
            x=float(x),
            y=float(y),
            combat_power=100.0,
            firepower_index=specs["firepower_index"],
            max_speed=specs["max_speed"],
            status="active",
            waypoints=[],
            current_action="hold",
            color=_BLUFOR_COLORS[i % len(_BLUFOR_COLORS)],
        ))

    opfor_placed: list = []
    for i, od in enumerate(opfor_defs):
        specs = UNIT_TYPE_SPECS.get(od.get("unit_type", "기계화보병"), default_specs)
        x = od.get("x")
        y = od.get("y")
        if x is None or y is None:
            x, y = _pick_pos(_OPFOR_ZONE, opfor_placed)
        opfor_placed.append((float(x), float(y)))
        units.append(Unit(
            id=od["id"],
            side="OPFOR",
            unit_type=od.get("unit_type", "기계화보병"),
            x=float(x),
            y=float(y),
            combat_power=100.0,
            firepower_index=specs["firepower_index"],
            max_speed=specs["max_speed"],
            status="active",
            waypoints=[],
            current_action="hold",
            color=_OPFOR_COLORS[i % len(_OPFOR_COLORS)],
        ))

    return units


# 부대 유형 라벨 (UI 표시용) — 철원 시나리오 편성 기준
UNIT_TYPE_LABEL = {
    "보병1중대":   "기계화보병",
    "보병2중대":   "기계화보병",
    "보병3중대":   "기계화보병",
    "전차중대":    "전차",
    "대전차중대":  "대전차",
    "자주포중대":  "자주포",
    "적보병1중대": "기계화보병",
    "적보병2중대": "기계화보병",
    "적보병3중대": "기계화보병",
    "적전차중대":  "전차",
    "적대전차중대": "대전차",
    "적자주포중대": "자주포",
}


def get_unit_type(unit_id: str) -> str:
    return UNIT_TYPE_LABEL.get(unit_id, "부대")
