"""
워게임 시나리오 정의.

BN vs BN (5 vs 5) — 학익진(鶴翼陣) 대형:
  양측 모두 학의 날개처럼 중앙이 뒤로, 양 날개가 앞으로 펼쳐진 V자 형태.
  적을 포위·협격하기 위한 진형.

  BLUFOR (남서부, 북동 방향 지향):
    Delta(정찰)·Echo(대전차) — 좌우 날개 끝 (전방)
    Alpha·Bravo(기계화보병) — 좌우 날개 중단
    Charlie(전차)            — 중앙 후방 (예비 돌파)

  OPFOR (북동부, 남서 방향 지향):
    Red4(정찰)·Red1(기계화보병) — 우익
    Red2(기계화보병)            — 좌익
    Red3(전차)                  — 중앙 후방
    Red5(자주포)                — 후방 화력지원

  부대 유형별 특성:
    기계화보병 (MechInf) : 화력100, 속도2.5 m/s
    전차       (Armor)   : 화력160, 속도2.0 m/s
    정찰       (Recon)   : 화력 45, 속도4.5 m/s
    대전차     (AT)      : 화력 90, 속도2.2 m/s
    자주포     (SPG)     : 화력130, 속도1.8 m/s
"""

import math as _math
import random as _rng

from .models import Unit


# ── 랜덤 배치 구역 정의 ────────────────────────────────────────────
# BLUFOR: 남서부 분지 (지형상 아군 집결지)
_BLUFOR_ZONE = dict(x_min=2_000, x_max=13_000, y_min=1_500, y_max=12_000)
# OPFOR : 북동부 고원 (지형상 적 집결지)
_OPFOR_ZONE  = dict(x_min=17_000, x_max=28_000, y_min=17_000, y_max=28_500)
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


def setup_bn_vs_bn() -> list:
    """BN vs BN (5 vs 5) 학익진 초기 배치."""
    return [
        # ══════════════════════════════════════════
        # BLUFOR 학익진 — 중앙 후방, 날개 전방 (북동 지향)
        #
        #   Delta(11.5,3.5)       Echo(11.5,8.5)   ← 날개 끝 (전방)
        #     Alpha(9.0,4.5)   Bravo(9.0,7.5)      ← 날개 중단
        #          Charlie(7.0,6.0)                 ← 중앙 후방
        # ══════════════════════════════════════════

        Unit(id="Charlie", side="BLUFOR", unit_type="전차",
             x=7_000.0, y=6_000.0,
             combat_power=100.0, firepower_index=160.0, max_speed=2.0,
             status="active", waypoints=[], current_action="hold", color="#00BCD4"),

        Unit(id="Alpha", side="BLUFOR", unit_type="기계화보병",
             x=9_000.0, y=4_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=2.5,
             status="active", waypoints=[], current_action="hold", color="#1E88E5"),

        Unit(id="Bravo", side="BLUFOR", unit_type="기계화보병",
             x=9_000.0, y=7_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=2.5,
             status="active", waypoints=[], current_action="hold", color="#42A5F5"),

        Unit(id="Delta", side="BLUFOR", unit_type="정찰",
             x=11_500.0, y=3_500.0,
             combat_power=100.0, firepower_index=45.0, max_speed=4.5,
             status="active", waypoints=[], current_action="hold", color="#80DEEA"),

        Unit(id="Echo", side="BLUFOR", unit_type="대전차",
             x=11_500.0, y=8_500.0,
             combat_power=100.0, firepower_index=90.0, max_speed=2.2,
             status="active", waypoints=[], current_action="hold", color="#B3E5FC"),

        # ══════════════════════════════════════════
        # OPFOR 학익진 — 중앙 후방, 날개 전방 (남서 지향)
        #
        #          Red3(23.0,21.0)                  ← 중앙 후방
        #    Red1(21.0,19.5)   Red2(21.0,22.5)      ← 날개 중단
        #  Red4(19.5,17.5)                           ← 날개 끝 (전방)
        #                         Red5(25.0,21.0)    ← 후방 화력지원
        # ══════════════════════════════════════════

        Unit(id="Red3", side="OPFOR", unit_type="전차",
             x=23_000.0, y=21_000.0,
             combat_power=100.0, firepower_index=155.0, max_speed=2.0,
             status="active", waypoints=[], current_action="hold", color="#FF7043"),

        Unit(id="Red1", side="OPFOR", unit_type="기계화보병",
             x=21_000.0, y=19_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=2.5,
             status="active", waypoints=[], current_action="hold", color="#E53935"),

        Unit(id="Red2", side="OPFOR", unit_type="기계화보병",
             x=21_000.0, y=22_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=2.5,
             status="active", waypoints=[], current_action="hold", color="#EF5350"),

        Unit(id="Red4", side="OPFOR", unit_type="정찰",
             x=19_500.0, y=17_500.0,
             combat_power=100.0, firepower_index=45.0, max_speed=4.5,
             status="active", waypoints=[], current_action="hold", color="#FFAB91"),

        Unit(id="Red5", side="OPFOR", unit_type="자주포",
             x=25_000.0, y=21_000.0,
             combat_power=100.0, firepower_index=130.0, max_speed=1.8,
             status="active", waypoints=[], current_action="hold", color="#FFCCBC"),
    ]


def setup_cheorwon_bn() -> list:
    """철원 축선 기계화대대 교전 (가상) — 6 vs 6.

    docs/scenario_cheorwon.md 반영판. 변경점:
      - 정찰 부대 없음: 양측 정찰(Delta/Red4)을 기계화보병 중대로 대체.
        정찰은 UAV가 담당한다고 가정 → 엔진 full_recon 모드로 처음부터 전 위치 detected.
      - 자주포 실사거리: K9(Foxtrot) 40km / 북한 곡산(Red5) 60km(RAP) 를 indirect_range로 반영.
    """
    return [
        # ── BLUFOR (대한민국) — 남서부 방어 → 역습 ──────────────────
        Unit(id="Alpha", side="BLUFOR", unit_type="기계화보병",
             x=8_000.0, y=5_000.0,
             combat_power=100.0, firepower_index=100.0, max_speed=2.5,
             status="active", waypoints=[], current_action="hold", color="#1E88E5"),
        Unit(id="Bravo", side="BLUFOR", unit_type="기계화보병",
             x=8_000.0, y=8_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=2.5,
             status="active", waypoints=[], current_action="hold", color="#42A5F5"),
        Unit(id="Delta", side="BLUFOR", unit_type="기계화보병",   # (구 정찰 → 보병중대)
             x=12_000.0, y=10_000.0,
             combat_power=100.0, firepower_index=100.0, max_speed=2.5,
             status="active", waypoints=[], current_action="hold", color="#26C6DA"),
        Unit(id="Charlie", side="BLUFOR", unit_type="전차",
             x=5_500.0, y=6_500.0,
             combat_power=100.0, firepower_index=160.0, max_speed=2.0,
             status="active", waypoints=[], current_action="hold", color="#00BCD4"),
        Unit(id="Echo", side="BLUFOR", unit_type="대전차",
             x=11_000.0, y=6_000.0,
             combat_power=100.0, firepower_index=90.0, max_speed=2.2,
             status="active", waypoints=[], current_action="hold", color="#B3E5FC"),
        Unit(id="Foxtrot", side="BLUFOR", unit_type="자주포",     # K9A1 — 실사거리 40km
             x=3_500.0, y=4_000.0,
             combat_power=100.0, firepower_index=130.0, max_speed=1.8,
             indirect_range=40_000.0,
             status="active", waypoints=[], current_action="hold", color="#4DD0E1"),

        # ── OPFOR (북한) — 북동부 공격 ──────────────────────────────
        Unit(id="Red1", side="OPFOR", unit_type="기계화보병",
             x=21_000.0, y=19_000.0,
             combat_power=100.0, firepower_index=100.0, max_speed=2.5,
             status="active", waypoints=[], current_action="hold", color="#E53935"),
        Unit(id="Red2", side="OPFOR", unit_type="기계화보병",
             x=21_000.0, y=22_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=2.5,
             status="active", waypoints=[], current_action="hold", color="#EF5350"),
        Unit(id="Red4", side="OPFOR", unit_type="기계화보병",     # (구 정찰 → 보병중대)
             x=18_000.0, y=18_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=2.5,
             status="active", waypoints=[], current_action="hold", color="#FF8A65"),
        Unit(id="Red3", side="OPFOR", unit_type="전차",
             x=23_500.0, y=20_500.0,
             combat_power=100.0, firepower_index=155.0, max_speed=2.0,
             status="active", waypoints=[], current_action="hold", color="#FF7043"),
        Unit(id="Red6", side="OPFOR", unit_type="대전차",
             x=24_000.0, y=23_000.0,
             combat_power=100.0, firepower_index=85.0, max_speed=2.2,
             status="active", waypoints=[], current_action="hold", color="#FFAB91"),
        Unit(id="Red5", side="OPFOR", unit_type="자주포",         # M1978 곡산 — 실사거리 60km(RAP)
             x=26_500.0, y=21_000.0,
             combat_power=100.0, firepower_index=130.0, max_speed=1.8,
             indirect_range=60_000.0,
             status="active", waypoints=[], current_action="hold", color="#FFCCBC"),
    ]


# 부대 유형별 스탯 (setup_custom_scenario에서 사용)
UNIT_TYPE_SPECS: dict = {
    "기계화보병": {"firepower_index": 100.0, "max_speed": 2.5},
    "전차":       {"firepower_index": 160.0, "max_speed": 2.0},
    "정찰":       {"firepower_index":  45.0, "max_speed": 4.5},
    "대전차":     {"firepower_index":  90.0, "max_speed": 2.2},
    "자주포":     {"firepower_index": 130.0, "max_speed": 1.8},
}

# 시나리오별 색상 팔레트
_BLUFOR_COLORS = ["#1E88E5", "#42A5F5", "#00BCD4", "#80DEEA", "#B3E5FC", "#26C6DA", "#4DD0E1", "#29B6F6"]
_OPFOR_COLORS  = ["#E53935", "#EF5350", "#FF7043", "#FFAB91", "#FFCCBC", "#FF8A65", "#FF5722", "#F44336"]


def setup_custom_scenario(blufor_defs: list, opfor_defs: list) -> list:
    """
    사용자 정의 시나리오 생성.

    Args:
        blufor_defs: [{"id": "Alpha", "unit_type": "기계화보병", "x": None, "y": None}, ...]
                     x, y가 None이면 BLUFOR 구역 내 랜덤 배치
        opfor_defs:  [{"id": "Red1",  "unit_type": "전차",       "x": None, "y": None}, ...]
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


def setup_bn_vs_bn_blufor_random() -> list:
    """
    BN vs BN — BLUFOR만 진영 구역 내 랜덤 초기 배치, OPFOR는 고정 학익진.

    아군 부대 유형·전투력·색상은 고정, 좌표만 BLUFOR 구역 내에서 랜덤 생성.
    같은 진영 부대 간 최소 이격 거리(_MIN_SEP)를 보장합니다.
    """
    base = setup_bn_vs_bn()
    blufor_placed: list = []
    for unit in base:
        if unit.side == "BLUFOR":
            x, y = _pick_pos(_BLUFOR_ZONE, blufor_placed)
            blufor_placed.append((x, y))
            unit.x = x
            unit.y = y
    return base


    """
    BN vs BN — 매 에피소드마다 진영별 구역 내 랜덤 초기 배치.

    부대 유형·전투력·색상은 고정, 좌표만 각 진영 구역 내에서 랜덤 생성.
    같은 진영 부대 간 최소 이격 거리(_MIN_SEP)를 보장합니다.
    """
    base = setup_bn_vs_bn()

    blufor_placed: list = []
    opfor_placed:  list = []

    for unit in base:
        if unit.side == "BLUFOR":
            x, y = _pick_pos(_BLUFOR_ZONE, blufor_placed)
            blufor_placed.append((x, y))
        else:
            x, y = _pick_pos(_OPFOR_ZONE, opfor_placed)
            opfor_placed.append((x, y))
        unit.x = x
        unit.y = y

    return base


# 부대 유형 라벨 (UI 표시용) — 철원 시나리오 편성 기준
UNIT_TYPE_LABEL = {
    "Alpha":   "기계화보병",
    "Bravo":   "기계화보병",
    "Charlie":  "전차",
    "Delta":   "기계화보병",   # (구 정찰 → 보병중대)
    "Echo":    "대전차",
    "Foxtrot": "자주포",
    "Red1":    "기계화보병",
    "Red2":    "기계화보병",
    "Red3":    "전차",
    "Red4":    "기계화보병",   # (구 정찰 → 보병중대)
    "Red5":    "자주포",
    "Red6":    "대전차",
}


def get_unit_type(unit_id: str) -> str:
    return UNIT_TYPE_LABEL.get(unit_id, "부대")
