"""
워게임 시나리오 정의.

BN vs BN (5 vs 5):
  BLUFOR 5개 부대 — 기계화보병×2, 전차, 정찰, 대전차
  OPFOR  5개 부대 — 기계화보병×2, 전차, 정찰, 자주포

  부대 유형별 특성:
    기계화보병 (MechInf) : 화력100, 속도2.5 m/s — 범용 전투
    전차       (Armor)   : 화력160, 속도2.0 m/s — 고화력·고방호
    정찰       (Recon)   : 화력 45, 속도4.5 m/s — 고속 정찰·우회
    대전차     (AT)      : 화력 90, 속도2.2 m/s — 대기갑 전문
    자주포     (SPG)     : 화력130, 속도1.8 m/s — 원거리 화력지원
"""

from .models import Unit


def setup_bn_vs_bn() -> list:
    """BN vs BN (5 vs 5) 초기 부대 목록 반환."""
    return [
        # ══════════════════════════════════════════
        # BLUFOR — 남서부 집결 (y 3,000~8,000)
        # ══════════════════════════════════════════

        # 기계화보병 Alpha — 좌익
        Unit(
            id="Alpha", side="BLUFOR", unit_type="기계화보병",
            x=7_000.0, y=4_000.0,
            combat_power=100.0, firepower_index=100.0, max_speed=2.5,
            status="active", waypoints=[], current_action="hold", color="#1E88E5",
        ),
        # 기계화보병 Bravo — 우익
        Unit(
            id="Bravo", side="BLUFOR", unit_type="기계화보병",
            x=7_000.0, y=7_000.0,
            combat_power=100.0, firepower_index=100.0, max_speed=2.5,
            status="active", waypoints=[], current_action="hold", color="#42A5F5",
        ),
        # 전차 Charlie — 중앙 돌파
        Unit(
            id="Charlie", side="BLUFOR", unit_type="전차",
            x=9_000.0, y=5_500.0,
            combat_power=100.0, firepower_index=160.0, max_speed=2.0,
            status="active", waypoints=[], current_action="hold", color="#00BCD4",
        ),
        # 정찰 Delta — 선두 정찰
        Unit(
            id="Delta", side="BLUFOR", unit_type="정찰",
            x=5_500.0, y=5_500.0,
            combat_power=100.0, firepower_index=45.0, max_speed=4.5,
            status="active", waypoints=[], current_action="hold", color="#80DEEA",
        ),
        # 대전차 Echo — 후방 지원
        Unit(
            id="Echo", side="BLUFOR", unit_type="대전차",
            x=8_500.0, y=3_500.0,
            combat_power=100.0, firepower_index=90.0, max_speed=2.2,
            status="active", waypoints=[], current_action="hold", color="#B3E5FC",
        ),

        # ══════════════════════════════════════════
        # OPFOR — 북동부 집결 (y 19,000~23,000)
        # ══════════════════════════════════════════

        # 기계화보병 Red1 — 좌익
        Unit(
            id="Red1", side="OPFOR", unit_type="기계화보병",
            x=23_000.0, y=19_500.0,
            combat_power=100.0, firepower_index=100.0, max_speed=2.5,
            status="active", waypoints=[], current_action="hold", color="#E53935",
        ),
        # 기계화보병 Red2 — 우익
        Unit(
            id="Red2", side="OPFOR", unit_type="기계화보병",
            x=23_000.0, y=22_500.0,
            combat_power=100.0, firepower_index=100.0, max_speed=2.5,
            status="active", waypoints=[], current_action="hold", color="#EF5350",
        ),
        # 전차 Red3 — 중앙
        Unit(
            id="Red3", side="OPFOR", unit_type="전차",
            x=21_000.0, y=21_000.0,
            combat_power=100.0, firepower_index=155.0, max_speed=2.0,
            status="active", waypoints=[], current_action="hold", color="#FF7043",
        ),
        # 정찰 Red4 — 전방 정찰
        Unit(
            id="Red4", side="OPFOR", unit_type="정찰",
            x=24_500.0, y=21_000.0,
            combat_power=100.0, firepower_index=45.0, max_speed=4.5,
            status="active", waypoints=[], current_action="hold", color="#FFAB91",
        ),
        # 자주포 Red5 — 후방 화력지원
        Unit(
            id="Red5", side="OPFOR", unit_type="자주포",
            x=25_000.0, y=20_000.0,
            combat_power=100.0, firepower_index=130.0, max_speed=1.8,
            status="active", waypoints=[], current_action="hold", color="#FFCCBC",
        ),
    ]


# 부대 유형 라벨 (UI 표시용)
UNIT_TYPE_LABEL = {
    "Alpha":   "기계화보병",
    "Bravo":   "기계화보병",
    "Charlie":  "전차",
    "Delta":   "정찰",
    "Echo":    "대전차",
    "Red1":    "기계화보병",
    "Red2":    "기계화보병",
    "Red3":    "전차",
    "Red4":    "정찰",
    "Red5":    "자주포",
}


def get_unit_type(unit_id: str) -> str:
    return UNIT_TYPE_LABEL.get(unit_id, "부대")
