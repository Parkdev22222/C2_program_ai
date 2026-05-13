"""
전술 메모리 관리자.

시뮬레이션 결과를 분석하여 위험 구역(패널티 존)과 유리 구역(보너스 존)을
config/tactical_memory.json에 저장합니다.
경로 추천 도구들이 이 파일을 읽어 점수 계산 시 보정을 적용합니다.
"""

import math
import logging
import json
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

TACTICAL_MEMORY_FILE = Path(__file__).parent.parent.parent / "config" / "tactical_memory.json"

_DEFAULT_DATA = {
    "version": 1,
    "penalty_zones": [],
    "bonus_zones": [],
    "updated_at": "",
    "total_episodes_analyzed": 0,
}


class TacticalMemory:
    def __init__(self):
        self._data = self._load()

    def _load(self) -> dict:
        """파일에서 전술 메모리 로드. 없으면 초기값 반환."""
        try:
            if TACTICAL_MEMORY_FILE.exists():
                with open(TACTICAL_MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.debug(f"전술 메모리 로드: 패널티 존 {len(data.get('penalty_zones', []))}개")
                return data
        except Exception as e:
            logger.warning(f"전술 메모리 로드 실패: {e}")
        return dict(_DEFAULT_DATA)

    def _save(self):
        """전술 메모리를 파일에 저장."""
        try:
            self._data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            TACTICAL_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TACTICAL_MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"전술 메모리 저장 실패: {e}")

    def get_penalty_zones(self) -> list:
        """현재 활성 패널티 존 목록 반환."""
        return self._data.get("penalty_zones", [])

    def get_bonus_zones(self) -> list:
        return self._data.get("bonus_zones", [])

    def apply_penalties(self, x: float, y: float, base_score: float) -> float:
        """
        (x, y) 위치에 대한 패널티/보너스를 base_score에 적용하여 반환.

        여러 존이 겹칠 경우 각 패널티를 순서대로 곱함.
        """
        score = base_score
        for zone in self.get_penalty_zones():
            dist = math.hypot(x - zone["x"], y - zone["y"])
            if dist <= zone["radius"]:
                # 중심에 가까울수록 패널티 강도 증가 (선형 감쇠)
                strength = 1.0 - (dist / zone["radius"])
                score *= (1.0 - zone["penalty"] * strength)
        for zone in self.get_bonus_zones():
            dist = math.hypot(x - zone["x"], y - zone["y"])
            if dist <= zone["radius"]:
                strength = 1.0 - (dist / zone["radius"])
                score *= (1.0 + zone.get("bonus", 0.3) * strength)
        return max(0.001, score)  # 0 이하 방지

    def add_penalty_zone(
        self,
        x: float, y: float,
        radius: float = 2000.0,
        penalty: float = 0.6,
        reason: str = "",
        source_episode: str = "",
    ) -> str:
        """
        패널티 존 추가. 기존 존과 50% 이상 겹치면 penalty 강화 후 반환.
        반환값: zone_id
        """
        # 중복 확인: 같은 위치 근처(radius 50% 이내)에 이미 존재하면 hit_count 증가 + penalty 강화
        for zone in self._data["penalty_zones"]:
            dist = math.hypot(x - zone["x"], y - zone["y"])
            if dist < zone["radius"] * 0.5:
                zone["hit_count"] = zone.get("hit_count", 1) + 1
                zone["penalty"] = min(0.95, zone["penalty"] + 0.1)  # 최대 0.95
                zone["reason"] = reason or zone["reason"]
                self._save()
                return zone["zone_id"]

        import uuid
        zone_id = f"pz_{uuid.uuid4().hex[:8]}"
        zone = {
            "zone_id": zone_id,
            "x": float(x),
            "y": float(y),
            "radius": float(radius),
            "penalty": float(penalty),
            "reason": reason,
            "hit_count": 1,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source_episode": source_episode,
        }
        self._data["penalty_zones"].append(zone)
        self._save()
        logger.info(f"패널티 존 추가: ({x:.0f}, {y:.0f}) r={radius:.0f}m penalty={penalty:.2f} — {reason}")
        return zone_id

    def add_bonus_zone(
        self,
        x: float, y: float,
        radius: float = 2000.0,
        bonus: float = 0.3,
        reason: str = "",
        source_episode: str = "",
    ) -> str:
        """보너스 존 추가 (유리했던 구역)."""
        # 중복 확인: 같은 위치 근처(radius 50% 이내)에 이미 존재하면 bonus 강화 후 반환
        for zone in self._data["bonus_zones"]:
            dist = math.hypot(x - zone["x"], y - zone["y"])
            if dist < zone["radius"] * 0.5:
                zone["hit_count"] = zone.get("hit_count", 1) + 1
                zone["bonus"] = min(0.9, zone.get("bonus", bonus) + 0.05)
                zone["reason"] = reason or zone["reason"]
                self._save()
                return zone["zone_id"]

        import uuid
        zone_id = f"bz_{uuid.uuid4().hex[:8]}"
        zone = {
            "zone_id": zone_id,
            "x": float(x),
            "y": float(y),
            "radius": float(radius),
            "bonus": float(bonus),
            "reason": reason,
            "hit_count": 1,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source_episode": source_episode,
        }
        self._data["bonus_zones"].append(zone)
        self._save()
        logger.info(f"보너스 존 추가: ({x:.0f}, {y:.0f}) r={radius:.0f}m bonus={bonus:.2f} — {reason}")
        return zone_id

    def prune_weak_zones(self, min_penalty: float = 0.15):
        """패널티가 너무 낮아진 존 제거."""
        before = len(self._data["penalty_zones"])
        self._data["penalty_zones"] = [
            z for z in self._data["penalty_zones"] if z["penalty"] >= min_penalty
        ]
        removed = before - len(self._data["penalty_zones"])
        if removed > 0:
            self._save()
            logger.info(f"약한 패널티 존 {removed}개 제거")
        return removed

    def get_stats(self) -> dict:
        return {
            "penalty_zones": len(self._data.get("penalty_zones", [])),
            "bonus_zones": len(self._data.get("bonus_zones", [])),
            "total_episodes_analyzed": self._data.get("total_episodes_analyzed", 0),
        }

    def increment_episodes(self):
        self._data["total_episodes_analyzed"] = self._data.get("total_episodes_analyzed", 0) + 1
        self._save()


class SpatialRuleExtractor:
    """
    에피소드의 전투 이벤트를 분석하여 공간적 패널티/보너스 존을 추출합니다.

    분석 기준:
    - COMBAT 이벤트에서 BLUFOR 피해가 컸던 좌표 → 패널티 존
    - DESTROYED 이벤트에서 BLUFOR 부대 파괴 위치 → 강한 패널티 존
    - BLUFOR가 높은 피해를 가한 위치 → 보너스 존
    """

    def __init__(self, tactical_memory: TacticalMemory):
        self._memory = tactical_memory

    def analyze_episode(self, metrics, engine=None) -> dict:
        """
        에피소드 메트릭과 전투 이벤트를 분석하여 패널티/보너스 존을 업데이트합니다.

        Returns:
            {"penalty_zones_added": int, "bonus_zones_added": int}
        """
        penalty_added = 0
        bonus_added = 0

        # events_summary에서 공간 정보 추출
        events = metrics.events_summary or []

        # 1. DESTROYED 이벤트 — BLUFOR 부대 파괴 위치 → 강한 패널티
        for event in events:
            msg = event.get("message", "")
            etype = event.get("event_type", "") or event.get("type", "")

            # 메시지에서 좌표 파싱: "x=12500, y=8300" 형식
            coords = _parse_coords_from_message(msg)

            if etype == "DESTROYED" and "BLUFOR" in msg and coords:
                self._memory.add_penalty_zone(
                    x=coords[0], y=coords[1],
                    radius=2500.0,
                    penalty=0.75,
                    reason=f"BLUFOR 부대 파괴 지점: {msg[:80]}",
                    source_episode=metrics.episode_id,
                )
                penalty_added += 1

            # COMBAT 이벤트에서 BLUFOR 피해 큰 위치
            elif etype == "COMBAT" and "피해" in msg and coords:
                # 피해량 파싱 시도
                damage = _parse_damage_from_message(msg)
                if damage > 30:  # 30% 이상 피해
                    self._memory.add_penalty_zone(
                        x=coords[0], y=coords[1],
                        radius=1500.0,
                        penalty=0.5,
                        reason=f"고피해 교전 지점 (피해 {damage:.0f}%): {msg[:60]}",
                        source_episode=metrics.episode_id,
                    )
                    penalty_added += 1

        # 2. 승리 에피소드에서 OPFOR 파괴 위치 → 약한 보너스 (공격 유리 구역)
        if metrics.winner == "BLUFOR":
            for event in events:
                etype = event.get("event_type", "") or event.get("type", "")
                msg = event.get("message", "")
                coords = _parse_coords_from_message(msg)
                if etype == "DESTROYED" and "OPFOR" in msg and coords:
                    self._memory.add_bonus_zone(
                        x=coords[0], y=coords[1],
                        radius=1500.0,
                        bonus=0.2,
                        reason=f"효과적 교전 지점: {msg[:60]}",
                        source_episode=metrics.episode_id,
                    )
                    bonus_added += 1

        self._memory.increment_episodes()
        return {"penalty_zones_added": penalty_added, "bonus_zones_added": bonus_added}


def _parse_coords_from_message(msg: str):
    """메시지에서 좌표 (x, y) 파싱. 실패 시 None 반환."""
    # "x=12500, y=8300" 또는 "(12500, 8300)" 또는 "12500m, 8300m" 형식
    patterns = [
        r"x[=:]\s*([\d.]+)[,\s]+y[=:]\s*([\d.]+)",
        r"\(\s*([\d.]+)\s*,\s*([\d.]+)\s*\)",
        r"([\d]{4,5})[m\s,]+([\d]{4,5})",
    ]
    for pat in patterns:
        m = re.search(pat, msg)
        if m:
            x, y = float(m.group(1)), float(m.group(2))
            if 0 <= x <= 30000 and 0 <= y <= 30000:
                return (x, y)
    return None


def _parse_damage_from_message(msg: str) -> float:
    """메시지에서 피해량(%) 파싱."""
    m = re.search(r"([\d.]+)\s*%", msg)
    if m:
        return float(m.group(1))
    return 0.0


# 싱글톤 인스턴스
_tactical_memory: TacticalMemory = None


def get_tactical_memory() -> TacticalMemory:
    global _tactical_memory
    if _tactical_memory is None:
        _tactical_memory = TacticalMemory()
    return _tactical_memory
