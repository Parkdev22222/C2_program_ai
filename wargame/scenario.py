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

from .models import Unit


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
