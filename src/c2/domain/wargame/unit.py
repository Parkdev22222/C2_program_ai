"""워게임 도메인 엔티티 — 부대(Unit) 및 공중지원(AirSupport).

순수 dataclass. 인프라(SQLite 등) 의존성 없음.
"""

import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional

# 공중지원 유형별 기본 파라미터
# damage_rate (%/hour): 반경 중심에서 duration 동안 누적 시 직격 최소 ~33% 피해가 되도록 설정
# 공식: total_damage ≈ damage_rate * (duration / 3600)
AIR_SUPPORT_PRESETS = {
    "cas": {           # 근접항공지원 (A-10 류) — 지속 제압, ~40% 직격
        "damage_rate": 480.0,  # 480 * (300/3600) = 40%
        "radius": 1_500.0,     # m
        "duration": 300.0,     # 게임 초
        "delay": 6.0,          # 게임 초 (투입 전 대기)
    },
    "strike": {        # 정밀타격 (F-35 류) — 순간 고위력, ~33% 직격
        "damage_rate": 2_000.0,  # 2000 * (60/3600) = 33.3%
        "radius": 400.0,
        "duration": 60.0,
        "delay": 12.0,
    },
    "artillery": {     # 장거리 포병지원 — 광역 지속, ~33% 직격
        "damage_rate": 200.0,  # 200 * (600/3600) = 33.3%
        "radius": 2_500.0,
        "duration": 600.0,
        "delay": 30.0,
    },
    "helicopter": {    # 공격헬기 지원 — ~30% 직격
        "damage_rate": 450.0,  # 450 * (240/3600) = 30%
        "radius": 1_000.0,
        "duration": 240.0,
        "delay": 60.0,
    },
}


@dataclass
class AirSupport:
    """공중지원 임무 단위."""
    call_sign: str              # 호출부호 (예: "DARKSTAR-1")
    support_type: str           # "cas" | "strike" | "artillery" | "helicopter"
    target_x: float             # 폭격 중심 x (m)
    target_y: float             # 폭격 중심 y (m)
    radius: float               # 피해 반경 (m)
    damage_rate: float          # %/hour — 반경 중심 최대 피해율
    duration: float             # 지속 시간 (게임 초)
    delay: float                # 투입 지연 (게임 초)
    side: str = "BLUFOR"        # 요청 측: "BLUFOR" | "OPFOR"
    status: str = "pending"     # "pending" | "active" | "completed"
    elapsed: float = 0.0        # 활성화 후 경과 게임 시간 (초)
    # OPFOR 공중지원 피격 재계획 콜백 1회 발동 여부 (직렬화 제외)
    hit_reported: bool = field(default=False, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("hit_reported", None)
        return d


@dataclass
class Unit:
    id: str                             # "보병1중대" | "보병2중대" | "적보병1중대" | "적보병2중대"
    side: str                           # "BLUFOR" | "OPFOR"
    x: float                            # 지도 좌표 (m, 동쪽)
    y: float                            # 지도 좌표 (m, 북쪽)
    combat_power: float                 # 0-100 %
    firepower_index: float              # 상대적 화력지수 (100 = 만편성 기계화 중대)
    max_speed: float                    # m/s (최대 기동 속도)
    status: str = "active"             # "active" | "suppressed" | "destroyed"
    waypoints: List[List[float]] = field(default_factory=list)  # [[x,y], ...]
    current_action: str = "hold"       # "move" | "attack" | "defend" | "hold"
    color: str = "blue"                # UI 색상
    unit_type: str = ""                # "기계화보병" | "전차" | "정찰" | "대전차" | "자주포"
    mission_lock_ticks: int = 0        # 신규 임무 발령 후 룰 기반 AI 차단 잔여 틱 수 (0=해제)
    indirect_range: float = 15_000.0   # 자주포 간접사격 최대 사거리(m). 기본=엔진 _INDIRECT_MAX_RANGE.
                                       #  K9 ~40km, 북한 곡산 자주포 ~60km(RAP) 등 체계별 실사거리 반영용.
    # ── 표적 추적 (BLUFOR 공격 임무) — DB 미영속 (런타임 전용) ──────────
    target_unit_id: Optional[str] = None   # 이 부대가 공격·추격할 적 부대 ID
    target_ref_x: Optional[float] = None   # 임무 발령 시점 표적 인지 위치 x (재계획 트리거 기준)
    target_ref_y: Optional[float] = None   # 임무 발령 시점 표적 인지 위치 y
    target_replan_fired: bool = False      # 표적 이동 재계획 콜백 1회 발동 여부
    pursuing: bool = False                 # LLM 경유지 완주 후 표적 지속 추격 중 여부
    # ── 임무 오버레이 (지도 표시용) — DB 미영속 (런타임 전용) ──────────
    mission_type: str = ""                 # 발령된 임무 유형(attack/defend/flank...) — 지도 라벨
    mission_objective_x: Optional[float] = None  # 임무 최종 목표 x (경유지 소진 후에도 유지)
    mission_objective_y: Optional[float] = None  # 임무 최종 목표 y

    # ── 파생 속성 ──────────────────────────────────────────────────

    def effective_firepower(self) -> float:
        """현재 전투력 기반 실질 화력."""
        if self.status == "destroyed":
            return 0.0
        return self.firepower_index * (self.combat_power / 100.0)

    def is_active(self) -> bool:
        return self.status != "destroyed" and self.combat_power > 0

    def distance_to(self, other: "Unit") -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5

    def to_dict(self) -> dict:
        d = asdict(self)
        d["waypoints"] = json.dumps(d["waypoints"])
        return d

    @classmethod
    def from_row(cls, row: dict) -> "Unit":
        row = dict(row)
        row["waypoints"] = json.loads(row.get("waypoints") or "[]")
        return cls(**row)
