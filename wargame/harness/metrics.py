"""
에피소드 메트릭 수집 및 집계 모듈.

EpisodeMetrics: 단일 에피소드 실행 결과를 담는 데이터클래스.
collect_metrics(): WargameEngine 상태에서 메트릭을 수집하는 함수.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EpisodeMetrics:
    """단일 에피소드 실행 결과 메트릭."""

    episode_id: str
    timestamp: str
    winner: str                         # "BLUFOR" / "OPFOR" / "draw"
    duration_ticks: int

    # 부대 현황
    blufor_initial: int
    blufor_survived: int
    opfor_initial: int
    opfor_survived: int

    # 생존율 / 격멸율
    blufor_survival_rate: float         # 자동 계산
    opfor_elimination_rate: float

    # 전투 효율
    total_damage_dealt: float           # OPFOR에 가한 전투력 피해 합계
    total_damage_taken: float           # BLUFOR가 받은 전투력 피해 합계
    combat_efficiency: float            # dealt / taken (0 나누기 안전)

    # 탐지/기습 지표
    detected_engagement_rate: float     # detected 상태에서의 교전 비율
    surprise_received_count: int        # "lost" 상태에서 공격받은 횟수

    # 임무 계획 정보
    mission_plans_applied: int
    recon_conducted: bool
    last_plan: dict = field(default_factory=dict)

    # 이벤트 요약
    events_summary: list = field(default_factory=list)  # 주요 이벤트 상위 10건

    # ── 직렬화 ───────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """JSON 직렬화용 딕셔너리 반환."""
        return {
            "episode_id": self.episode_id,
            "timestamp": self.timestamp,
            "winner": self.winner,
            "duration_ticks": self.duration_ticks,
            "blufor_initial": self.blufor_initial,
            "blufor_survived": self.blufor_survived,
            "opfor_initial": self.opfor_initial,
            "opfor_survived": self.opfor_survived,
            "blufor_survival_rate": self.blufor_survival_rate,
            "opfor_elimination_rate": self.opfor_elimination_rate,
            "total_damage_dealt": self.total_damage_dealt,
            "total_damage_taken": self.total_damage_taken,
            "combat_efficiency": self.combat_efficiency,
            "detected_engagement_rate": self.detected_engagement_rate,
            "surprise_received_count": self.surprise_received_count,
            "mission_plans_applied": self.mission_plans_applied,
            "recon_conducted": self.recon_conducted,
            "last_plan": self.last_plan,
            "events_summary": self.events_summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EpisodeMetrics":
        """딕셔너리에서 EpisodeMetrics 복원."""
        return cls(
            episode_id=d.get("episode_id", ""),
            timestamp=d.get("timestamp", ""),
            winner=d.get("winner", "draw"),
            duration_ticks=d.get("duration_ticks", 0),
            blufor_initial=d.get("blufor_initial", 0),
            blufor_survived=d.get("blufor_survived", 0),
            opfor_initial=d.get("opfor_initial", 0),
            opfor_survived=d.get("opfor_survived", 0),
            blufor_survival_rate=d.get("blufor_survival_rate", 0.0),
            opfor_elimination_rate=d.get("opfor_elimination_rate", 0.0),
            total_damage_dealt=d.get("total_damage_dealt", 0.0),
            total_damage_taken=d.get("total_damage_taken", 0.0),
            combat_efficiency=d.get("combat_efficiency", 0.0),
            detected_engagement_rate=d.get("detected_engagement_rate", 0.0),
            surprise_received_count=d.get("surprise_received_count", 0),
            mission_plans_applied=d.get("mission_plans_applied", 0),
            recon_conducted=d.get("recon_conducted", False),
            last_plan=d.get("last_plan", {}),
            events_summary=d.get("events_summary", []),
        )

    def summary_str(self) -> str:
        """로그용 요약 문자열 반환."""
        return (
            f"[에피소드 {self.episode_id}] "
            f"결과={self.winner} | "
            f"BLUFOR생존율={self.blufor_survival_rate:.0%} | "
            f"OPFOR격멸율={self.opfor_elimination_rate:.0%} | "
            f"교환비={self.combat_efficiency:.1f} | "
            f"기습피격={self.surprise_received_count}회 | "
            f"정찰={self.recon_conducted} | "
            f"틱={self.duration_ticks}"
        )


def collect_metrics(engine, last_plan: Optional[dict] = None) -> EpisodeMetrics:
    """
    WargameEngine 상태에서 에피소드 메트릭을 수집합니다.

    Args:
        engine: WargameEngine 인스턴스
        last_plan: 마지막으로 적용된 임무계획 딕셔너리 (없으면 None)

    Returns:
        EpisodeMetrics 인스턴스
    """
    import uuid

    try:
        state = engine.get_state()
    except Exception as e:
        logger.error(f"collect_metrics: get_state() 실패: {e}")
        state = {}

    try:
        events = engine.db.get_recent_events(50)
    except Exception as e:
        logger.warning(f"collect_metrics: get_recent_events() 실패: {e}")
        events = []

    # ── 기본 정보 ──
    episode_id = uuid.uuid4().hex[:12]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tick = state.get("tick", 0)

    winner_raw = state.get("winner") or "draw"
    # "무승부" → "draw"로 정규화
    if winner_raw == "무승부":
        winner = "draw"
    elif winner_raw in ("BLUFOR", "OPFOR"):
        winner = winner_raw
    else:
        winner = "draw"

    # ── 부대 현황 집계 ──
    units = state.get("units", [])
    blufor_units = [u for u in units if u.get("side") == "BLUFOR"]
    opfor_units = [u for u in units if u.get("side") == "OPFOR"]

    blufor_initial = len(blufor_units)
    opfor_initial = len(opfor_units)

    blufor_survived = sum(
        1 for u in blufor_units
        if u.get("status") != "destroyed" and u.get("combat_power", 0) > 5.0
    )
    opfor_survived = sum(
        1 for u in opfor_units
        if u.get("status") != "destroyed" and u.get("combat_power", 0) > 5.0
    )

    blufor_survival_rate = (
        blufor_survived / blufor_initial if blufor_initial > 0 else 0.0
    )
    opfor_elimination_rate = (
        1.0 - opfor_survived / opfor_initial if opfor_initial > 0 else 0.0
    )

    # ── 전투 피해 집계 (이벤트 파싱) ──
    total_damage_dealt = 0.0    # BLUFOR → OPFOR
    total_damage_taken = 0.0    # OPFOR → BLUFOR
    surprise_received_count = 0
    combat_events = 0
    detected_engagement_events = 0
    mission_plans_applied = 0
    recon_conducted = False

    # BLUFOR / OPFOR 부대 ID 집합
    blufor_ids = {u.get("id") for u in blufor_units}
    opfor_ids = {u.get("id") for u in opfor_units}

    import re

    for ev in events:
        etype = ev.get("event_type", "")
        msg = ev.get("message", "")

        # 교전 이벤트: "AttackerID→DefenderID: -XX.X% CP (...)"
        if etype in ("COMBAT", "SURPRISE"):
            # 피해량 파싱
            dmg_match = re.search(r"-([\d.]+)%\s*CP", msg)
            dmg = float(dmg_match.group(1)) if dmg_match else 0.0

            # 방향 파싱: "ID1→ID2"
            arrow_match = re.search(r"(\w+)→(\w+)", msg)
            if arrow_match:
                attacker_id = arrow_match.group(1)
                defender_id = arrow_match.group(2)

                if attacker_id in blufor_ids and defender_id in opfor_ids:
                    total_damage_dealt += dmg
                elif attacker_id in opfor_ids and defender_id in blufor_ids:
                    total_damage_taken += dmg

            combat_events += 1

            # 기습 피격 (OPFOR가 lost 상태에서 공격 → BLUFOR 피해)
            if etype == "SURPRISE":
                # 메시지에 "[기습]" 포함 여부 확인
                if "[기습]" in msg:
                    # 방어자가 BLUFOR인지 확인
                    if arrow_match and arrow_match.group(2) in blufor_ids:
                        surprise_received_count += 1

        # DESTROYED 이벤트에서 추가 피해 집계 (선택적)
        elif etype == "DESTROYED":
            unit_id_match = re.search(r"^(\w+)\s+전투불능", msg)
            if unit_id_match:
                destroyed_id = unit_id_match.group(1)
                # 이미 집계된 전투 이벤트에서 처리되므로 별도 처리 없음

        # 정찰 탐지 이벤트
        elif etype == "DETECTION":
            detected_engagement_events += 1

        # 임무계획 이벤트
        elif etype == "ORDER":
            mission_plans_applied += 1
            # 정찰 임무 확인
            if "recon" in msg.lower() or "정찰" in msg:
                recon_conducted = True

    # 탐지 후 교전 비율: 탐지 이벤트 대비 교전 이벤트 비율
    detected_engagement_rate = (
        detected_engagement_events / max(combat_events, 1)
        if combat_events > 0
        else 0.0
    )
    detected_engagement_rate = min(1.0, detected_engagement_rate)

    # 교환비
    combat_efficiency = (
        total_damage_dealt / total_damage_taken
        if total_damage_taken > 0.0
        else (total_damage_dealt if total_damage_dealt > 0.0 else 1.0)
    )

    # ── 이벤트 요약 (상위 10건) ──
    important_types = {"COMBAT", "SURPRISE", "DESTROYED", "ENDEX", "DETECTION", "ORDER", "AIR_STRIKE"}
    important_events = [
        ev for ev in events
        if ev.get("event_type") in important_types
    ]
    events_summary = [
        {
            "tick": ev.get("tick", 0),
            "type": ev.get("event_type", ""),
            "message": ev.get("message", "")[:120],
        }
        for ev in important_events[-10:]
    ]

    metrics = EpisodeMetrics(
        episode_id=episode_id,
        timestamp=timestamp,
        winner=winner,
        duration_ticks=tick,
        blufor_initial=blufor_initial,
        blufor_survived=blufor_survived,
        opfor_initial=opfor_initial,
        opfor_survived=opfor_survived,
        blufor_survival_rate=blufor_survival_rate,
        opfor_elimination_rate=opfor_elimination_rate,
        total_damage_dealt=round(total_damage_dealt, 2),
        total_damage_taken=round(total_damage_taken, 2),
        combat_efficiency=round(combat_efficiency, 3),
        detected_engagement_rate=round(detected_engagement_rate, 3),
        surprise_received_count=surprise_received_count,
        mission_plans_applied=mission_plans_applied,
        recon_conducted=recon_conducted,
        last_plan=last_plan or {},
        events_summary=events_summary,
    )

    logger.info(metrics.summary_str())
    return metrics
