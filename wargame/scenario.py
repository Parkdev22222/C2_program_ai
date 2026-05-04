"""
워게임 시나리오 정의.

BN vs BN: BLUFOR (Alpha, Bravo) vs OPFOR (Red1, Red2)
  - 각 중대: 만편성 기계화 보병 (APC 8대 + 보병 80명)
  - BLUFOR 초기 위치: 남부 (y≈5000-6500)
  - OPFOR 초기 위치: 북부 (y≈20000-21500)
"""

from .models import Unit


def setup_bn_vs_bn() -> list:
    """BN vs BN 초기 부대 목록 반환."""
    return [
        # ── BLUFOR ──────────────────────────────────────────────
        Unit(
            id="Alpha",
            side="BLUFOR",
            x=7_500.0, y=5_000.0,
            combat_power=100.0,
            firepower_index=100.0,
            max_speed=2.5,        # m/s (≈9 km/h 오프로드)
            status="active",
            waypoints=[],
            current_action="hold",
            color="#2196F3",      # 파랑
        ),
        Unit(
            id="Bravo",
            side="BLUFOR",
            x=7_500.0, y=6_500.0,
            combat_power=100.0,
            firepower_index=100.0,
            max_speed=2.5,
            status="active",
            waypoints=[],
            current_action="hold",
            color="#03A9F4",      # 하늘
        ),
        # ── OPFOR ───────────────────────────────────────────────
        Unit(
            id="Red1",
            side="OPFOR",
            x=22_000.0, y=20_000.0,
            combat_power=100.0,
            firepower_index=100.0,
            max_speed=2.5,
            status="active",
            waypoints=[],
            current_action="hold",
            color="#F44336",      # 빨강
        ),
        Unit(
            id="Red2",
            side="OPFOR",
            x=22_000.0, y=21_500.0,
            combat_power=100.0,
            firepower_index=100.0,
            max_speed=2.5,
            status="active",
            waypoints=[],
            current_action="hold",
            color="#FF5722",      # 주황-빨강
        ),
    ]


def setup_company_attack() -> list:
    """단일 중대 공격 훈련 시나리오."""
    return [
        Unit(
            id="Alpha1",
            side="BLUFOR",
            x=5_000.0, y=5_000.0,
            combat_power=100.0,
            firepower_index=100.0,
            max_speed=2.5,
            color="#2196F3",
        ),
        Unit(
            id="Opfor1",
            side="OPFOR",
            x=15_000.0, y=15_000.0,
            combat_power=100.0,
            firepower_index=80.0,
            max_speed=2.0,
            color="#F44336",
        ),
    ]
