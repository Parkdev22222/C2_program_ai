"""
전술 메모리 관리자.

시뮬레이션 결과를 분석하여 위험 구역(패널티 존)과 유리 구역(보너스 존)을
config/tactical_memory.json에 저장합니다.
경로 추천 도구들이 이 파일을 읽어 점수 계산 시 보정을 적용합니다.

각 존은 교전 당시 상황(combat_context)을 함께 저장하므로 현재 상황과
유사할 때만 패널티/보너스를 강하게 적용합니다.

[Task 26] wargame/harness/tactical_memory.py 에서 이동 (애플리케이션 계층).
terrain → c2.domain.wargame.terrain.
"""

import math
import logging
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TACTICAL_MEMORY_FILE = Path(__file__).resolve().parents[4] / "config" / "tactical_memory.json"

_DEFAULT_DATA = {
    "version": 2,
    "penalty_zones": [],
    "bonus_zones": [],
    "updated_at": "",
    "total_episodes_analyzed": 0,
}


# ── 교전 상황 유사도 ──────────────────────────────────────────────────

# 적 위치 유사도 기준 거리: 이 거리(m) 이상 떨어지면 위치 유사도 0
_POSITION_REF_DIST = 6_000.0

# 지형 고도 기준값: 정규화에 사용 (맵 최대 고도 가정)
_ELEV_REF = 600.0


def _jaccard(set_a: list, set_b: list) -> float:
    """두 리스트의 Jaccard 유사도 (공집합이면 1.0 반환)."""
    if not set_a and not set_b:
        return 1.0
    a, b = set(set_a), set(set_b)
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 1.0


def _positional_similarity(stored_positions: list, current_positions: list) -> float:
    """
    저장된 적 위치 목록과 현재 적 위치 목록의 공간적 유사도 (0~1).

    각 저장 위치에서 가장 가까운 현재 위치까지의 거리를 _POSITION_REF_DIST로
    정규화하여 평균. 어느 한쪽이 비어 있으면 0.5(중립) 반환.
    """
    if not stored_positions or not current_positions:
        return 0.5
    sims = []
    for sp in stored_positions:
        sx, sy = float(sp[0]), float(sp[1])
        min_dist = min(
            math.hypot(sx - float(cp[0]), sy - float(cp[1]))
            for cp in current_positions
        )
        sims.append(max(0.0, 1.0 - min_dist / _POSITION_REF_DIST))
    return sum(sims) / len(sims)


def _terrain_similarity(stored_terrain: dict, current_terrain: dict) -> float:
    """
    두 지형 프로파일(terrain dict)의 유사도 (0~1).

    비교 항목:
      - 평균 고도 차이 (가중치 0.40)
      - 고도 표준편차 차이 — 지형 기복 유사성 (가중치 0.30)
      - 평균 엄폐도 차이 (가중치 0.20)
      - 고도 샘플 RMSE — 세부 지형 형태 (가중치 0.10, 샘플 있을 때만)

    어느 한쪽이 비어 있으면 0.5(중립) 반환.
    """
    if not stored_terrain or not current_terrain:
        return 0.5

    # 1. 평균 고도 유사도
    elev_diff = abs(stored_terrain.get("elev_mean", 0) - current_terrain.get("elev_mean", 0))
    elev_sim = max(0.0, 1.0 - elev_diff / _ELEV_REF)

    # 2. 고도 표준편차 유사도 (지형 기복)
    std_diff = abs(stored_terrain.get("elev_std", 0) - current_terrain.get("elev_std", 0))
    std_sim = max(0.0, 1.0 - std_diff / (_ELEV_REF * 0.3))

    # 3. 엄폐도 유사도
    cover_diff = abs(stored_terrain.get("cover_mean", 0) - current_terrain.get("cover_mean", 0))
    cover_sim = max(0.0, 1.0 - cover_diff / 0.65)

    # 4. 고도 샘플 RMSE (3×3 그리드 9개 값)
    s_samples = stored_terrain.get("elev_samples", [])
    c_samples = current_terrain.get("elev_samples", [])
    if s_samples and c_samples and len(s_samples) == len(c_samples):
        n = len(s_samples)
        rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(s_samples, c_samples)) / n)
        sample_sim = max(0.0, 1.0 - rmse / _ELEV_REF)
        return (
            elev_sim   * 0.40
            + std_sim  * 0.30
            + cover_sim * 0.20
            + sample_sim * 0.10
        )

    return elev_sim * 0.50 + std_sim * 0.35 + cover_sim * 0.15


def compute_context_similarity(stored: dict, current: dict) -> float:
    """
    저장된 교전 상황(stored)과 현재 상황(current)의 유사도를 0~1로 반환.

    저장 또는 현재 컨텍스트가 없으면 0.5(중립)를 반환.

    비교 기준:
      - 적 부대 유형 Jaccard 유사도  (가중치 0.25)
      - 전투력 비율 유사도            (가중치 0.20)
      - 적 수량 유사도                (가중치 0.10)
      - 적 위치 공간 유사도           (가중치 0.25)
      - 교전 지역 지형 유사도         (가중치 0.20)
    """
    if not stored or not current:
        return 0.5

    # 1. 적 부대 유형 유사도
    enemy_type_sim = _jaccard(
        stored.get("enemy_unit_types", []),
        current.get("enemy_unit_types", []),
    )

    # 2. 전투력 비율 유사도
    stored_ratio  = stored.get("force_ratio", 1.0)
    current_ratio = current.get("force_ratio", 1.0)
    ratio_diff = abs(stored_ratio - current_ratio)
    ratio_sim = max(0.0, 1.0 - ratio_diff / max(stored_ratio, current_ratio, 0.01))

    # 3. 적 수량 유사도
    stored_cnt  = stored.get("enemy_count", 1)
    current_cnt = current.get("enemy_count", 1)
    count_sim = 1.0 - abs(stored_cnt - current_cnt) / max(stored_cnt, current_cnt, 1)

    # 4. 적 위치 공간 유사도
    pos_sim = _positional_similarity(
        stored.get("enemy_positions", []),
        current.get("enemy_positions", []),
    )

    # 5. 교전 지역 지형 유사도
    terrain_sim = _terrain_similarity(
        stored.get("terrain", {}),
        current.get("terrain", {}),
    )

    similarity = (
        enemy_type_sim * 0.25
        + ratio_sim    * 0.20
        + count_sim    * 0.10
        + pos_sim      * 0.25
        + terrain_sim  * 0.20
    )
    return float(max(0.0, min(1.0, similarity)))


# ── 메인 클래스 ──────────────────────────────────────────────────────

class TacticalMemory:
    def __init__(self):
        self._data = self._load()

    def _load(self) -> dict:
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
        try:
            self._data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            TACTICAL_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TACTICAL_MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"전술 메모리 저장 실패: {e}")

    def get_penalty_zones(self) -> list:
        return self._data.get("penalty_zones", [])

    def get_bonus_zones(self) -> list:
        return self._data.get("bonus_zones", [])

    def apply_penalties(
        self,
        x: float,
        y: float,
        base_score: float,
        current_context: Optional[dict] = None,
    ) -> float:
        """
        (x, y) 위치에 패널티/보너스를 적용한 점수를 반환.

        current_context가 제공되면 저장된 교전 상황과의 유사도에 따라
        패널티/보너스 강도를 조절합니다.

        current_context 예시:
            {
                "enemy_unit_types": ["전차", "기계화보병"],
                "enemy_count": 2,
                "enemy_positions": [[12000.0, 8000.0], [14000.0, 9000.0]],
                "force_ratio": 0.8,
                "terrain": {"elev_mean": 250.0, "elev_std": 40.0, "cover_mean": 0.3,
                            "elev_samples": [...]},
            }
        """
        score = base_score
        for zone in self.get_penalty_zones():
            dist = math.hypot(x - zone["x"], y - zone["y"])
            if dist > zone["radius"]:
                continue

            # 거리 기반 강도 (중심에 가까울수록 강함)
            distance_strength = 1.0 - (dist / zone["radius"])

            # 상황 유사도 기반 강도 조절
            if current_context is not None:
                ctx_sim = compute_context_similarity(
                    zone.get("combat_context", {}),
                    current_context,
                )
                # 유사도 0 → 패널티 10%만 적용 / 유사도 1 → 100% 적용
                effective_penalty = zone["penalty"] * (0.10 + 0.90 * ctx_sim)
            else:
                effective_penalty = zone["penalty"]

            score *= (1.0 - effective_penalty * distance_strength)

        for zone in self.get_bonus_zones():
            dist = math.hypot(x - zone["x"], y - zone["y"])
            if dist > zone["radius"]:
                continue

            distance_strength = 1.0 - (dist / zone["radius"])

            if current_context is not None:
                ctx_sim = compute_context_similarity(
                    zone.get("combat_context", {}),
                    current_context,
                )
                effective_bonus = zone.get("bonus", 0.3) * (0.10 + 0.90 * ctx_sim)
            else:
                effective_bonus = zone.get("bonus", 0.3)

            score *= (1.0 + effective_bonus * distance_strength)

        return max(0.001, score)

    def add_penalty_zone(
        self,
        x: float, y: float,
        radius: float = 2000.0,
        penalty: float = 0.6,
        reason: str = "",
        source_episode: str = "",
        combat_context: Optional[dict] = None,
    ) -> str:
        """
        패널티 존 추가.

        같은 위치 근처(radius 50% 이내)에 이미 존재하면 hit_count 증가 +
        penalty 강화. combat_context가 제공되면 존에 함께 저장.
        """
        for zone in self._data["penalty_zones"]:
            dist = math.hypot(x - zone["x"], y - zone["y"])
            if dist < zone["radius"] * 0.5:
                zone["hit_count"] = zone.get("hit_count", 1) + 1
                zone["penalty"] = min(0.95, zone["penalty"] + 0.1)
                zone["reason"] = reason or zone["reason"]
                # 컨텍스트가 없던 존에 새 컨텍스트가 들어오면 업데이트
                if combat_context and not zone.get("combat_context"):
                    zone["combat_context"] = combat_context
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
            "combat_context": combat_context or {},
        }
        self._data["penalty_zones"].append(zone)
        self._save()
        logger.info(
            f"패널티 존 추가: ({x:.0f}, {y:.0f}) r={radius:.0f}m "
            f"penalty={penalty:.2f} — {reason}"
        )
        return zone_id

    def add_bonus_zone(
        self,
        x: float, y: float,
        radius: float = 2000.0,
        bonus: float = 0.3,
        reason: str = "",
        source_episode: str = "",
        combat_context: Optional[dict] = None,
    ) -> str:
        """보너스 존 추가 (유리했던 구역)."""
        for zone in self._data["bonus_zones"]:
            dist = math.hypot(x - zone["x"], y - zone["y"])
            if dist < zone["radius"] * 0.5:
                zone["hit_count"] = zone.get("hit_count", 1) + 1
                zone["bonus"] = min(0.9, zone.get("bonus", bonus) + 0.05)
                zone["reason"] = reason or zone["reason"]
                if combat_context and not zone.get("combat_context"):
                    zone["combat_context"] = combat_context
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
            "combat_context": combat_context or {},
        }
        self._data["bonus_zones"].append(zone)
        self._save()
        logger.info(
            f"보너스 존 추가: ({x:.0f}, {y:.0f}) r={radius:.0f}m "
            f"bonus={bonus:.2f} — {reason}"
        )
        return zone_id

    def prune_weak_zones(self, min_penalty: float = 0.15):
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


# ── 공간 규칙 추출기 ─────────────────────────────────────────────────

class SpatialRuleExtractor:
    """
    에피소드 전투 이벤트를 분석하여 공간적 패널티/보너스 존을 추출합니다.

    분석 기준:
      - BLUFOR 부대 파괴 위치      → 강한 패널티 존
      - 고피해 COMBAT 교전 위치   → 중간 패널티 존
      - OPFOR 파괴 위치 (승리 시) → 보너스 존

    각 존에 교전 당시 combat_context를 함께 저장합니다.
    """

    def __init__(self, tactical_memory: TacticalMemory):
        self._memory = tactical_memory

    def analyze_episode(self, metrics, engine=None) -> dict:
        """
        에피소드 메트릭과 전투 이벤트를 분석하여 패널티/보너스 존을 업데이트.

        Returns:
            {"penalty_zones_added": int, "bonus_zones_added": int}
        """
        penalty_added = 0
        bonus_added = 0
        events = metrics.events_summary or []

        # 에피소드 전체 교전 상황 요약 (기본 컨텍스트)
        episode_context = _extract_episode_context(metrics, engine)

        for event in events:
            msg   = event.get("message", "")
            etype = event.get("event_type", "") or event.get("type", "")
            coords = _parse_coords_from_message(msg)

            # 이벤트 레벨 컨텍스트 (이벤트별 상세 정보가 있으면 오버라이드)
            evt_context = _extract_event_context(event, episode_context)

            # 1. BLUFOR 부대 파괴 → 강한 패널티
            if etype == "DESTROYED" and "BLUFOR" in msg and coords:
                self._memory.add_penalty_zone(
                    x=coords[0], y=coords[1],
                    radius=2500.0,
                    penalty=0.75,
                    reason=f"BLUFOR 부대 파괴: {msg[:80]}",
                    source_episode=metrics.episode_id,
                    combat_context=evt_context,
                )
                penalty_added += 1

            # 2. 고피해 COMBAT → 중간 패널티
            elif etype == "COMBAT" and "피해" in msg and coords:
                damage = _parse_damage_from_message(msg)
                if damage > 30:
                    self._memory.add_penalty_zone(
                        x=coords[0], y=coords[1],
                        radius=1500.0,
                        penalty=0.5,
                        reason=f"고피해 교전 (피해 {damage:.0f}%): {msg[:60]}",
                        source_episode=metrics.episode_id,
                        combat_context=evt_context,
                    )
                    penalty_added += 1

        # 3. 승리 에피소드 — OPFOR 파괴 위치 → 보너스
        if metrics.winner == "BLUFOR":
            for event in events:
                etype = event.get("event_type", "") or event.get("type", "")
                msg   = event.get("message", "")
                coords = _parse_coords_from_message(msg)
                evt_context = _extract_event_context(event, episode_context)
                if etype == "DESTROYED" and "OPFOR" in msg and coords:
                    self._memory.add_bonus_zone(
                        x=coords[0], y=coords[1],
                        radius=1500.0,
                        bonus=0.2,
                        reason=f"효과적 교전: {msg[:60]}",
                        source_episode=metrics.episode_id,
                        combat_context=evt_context,
                    )
                    bonus_added += 1

        self._memory.increment_episodes()
        return {"penalty_zones_added": penalty_added, "bonus_zones_added": bonus_added}


# ── 지형 샘플링 ──────────────────────────────────────────────────────

# 3×3 그리드 오프셋 (단위: 상대 비율 × radius)
_GRID_OFFSETS = [
    (-1, -1), (0, -1), (1, -1),
    (-1,  0), (0,  0), (1,  0),
    (-1,  1), (0,  1), (1,  1),
]


def sample_terrain_profile(cx: float, cy: float, radius: float = 2000.0) -> dict:
    """
    (cx, cy) 중심으로 3×3 그리드 지형 프로파일 샘플링.

    Returns:
        {
            "center_x": float, "center_y": float,
            "elev_mean": float, "elev_std": float,
            "cover_mean": float,
            "elev_samples": [float × 9],   # 3×3 그리드 고도값
        }
    지형 모듈 로드 실패 시 빈 dict 반환.
    """
    try:
        from c2.domain.wargame.terrain import terrain as _t
    except Exception:
        return {}

    step = radius * 0.7  # 그리드 간격: radius의 70%
    elevs = []
    covers = []
    for (di, dj) in _GRID_OFFSETS:
        sx = max(0.0, min(29_999.0, cx + di * step))
        sy = max(0.0, min(29_999.0, cy + dj * step))
        try:
            elevs.append(float(_t.elevation(sx, sy)))
            covers.append(float(_t.cover_factor(sx, sy)))
        except Exception:
            elevs.append(0.0)
            covers.append(0.0)

    n = len(elevs)
    elev_mean = sum(elevs) / n
    elev_std  = math.sqrt(sum((e - elev_mean) ** 2 for e in elevs) / n)
    cover_mean = sum(covers) / n

    return {
        "center_x":    round(cx, 1),
        "center_y":    round(cy, 1),
        "elev_mean":   round(elev_mean, 1),
        "elev_std":    round(elev_std, 1),
        "cover_mean":  round(cover_mean, 3),
        "elev_samples": [round(e, 1) for e in elevs],
    }


# ── 컨텍스트 추출 헬퍼 ───────────────────────────────────────────────

def _extract_episode_context(metrics, engine=None) -> dict:
    """에피소드 메트릭에서 기본 교전 컨텍스트 추출."""
    ctx: dict = {}

    try:
        ctx["force_ratio"] = float(getattr(metrics, "blufor_survival_rate", 1.0))
        ctx["blufor_cp_at_time"] = float(getattr(metrics, "blufor_survival_rate", 1.0)) * 100
        ctx["opfor_cp_at_time"] = (
            1.0 - float(getattr(metrics, "opfor_elimination_rate", 0.0))
        ) * 100
        ctx["game_tick"] = int(getattr(metrics, "duration_ticks", 0))
    except Exception:
        pass

    # 엔진 상태에서 부대 유형·위치 수집
    if engine is not None:
        try:
            state = engine.get_state()
            units = state.get("units", [])
            opfor_units = [u for u in units if u.get("side") == "OPFOR"]
            ctx["enemy_unit_types"] = list({u.get("unit_type", "unknown") for u in opfor_units})
            ctx["friendly_unit_types"] = list({
                u.get("unit_type", "unknown")
                for u in units if u.get("side") == "BLUFOR"
            })
            ctx["enemy_count"] = len(opfor_units)
            # 적 부대 위치 목록 [[x, y], ...]
            ctx["enemy_positions"] = [
                [float(u.get("x", 0)), float(u.get("y", 0))]
                for u in opfor_units
            ]
            # 에피소드 전체 교전 지역 지형: 적 위치들의 중심점 기준 샘플링
            if ctx["enemy_positions"]:
                cx_ep = sum(p[0] for p in ctx["enemy_positions"]) / len(ctx["enemy_positions"])
                cy_ep = sum(p[1] for p in ctx["enemy_positions"]) / len(ctx["enemy_positions"])
                ctx["terrain"] = sample_terrain_profile(cx_ep, cy_ep, radius=3000.0)
        except Exception:
            pass

    # events_summary에서 폴백 (엔진 없을 때)
    if "enemy_unit_types" not in ctx:
        events = getattr(metrics, "events_summary", []) or []
        types = set()
        positions = []
        for evt in events:
            msg = evt.get("message", "")
            for kw in ("전차", "기계화보병", "보병", "포병", "헬기", "드론", "자주포"):
                if kw in msg and "OPFOR" in msg:
                    types.add(kw)
            coords = _parse_coords_from_message(msg)
            if coords and "OPFOR" in msg:
                positions.append(list(coords))
        if types:
            ctx["enemy_unit_types"] = list(types)
        ctx["enemy_count"] = len(types) or 1
        if positions:
            ctx["enemy_positions"] = positions

    return ctx


def _extract_event_context(event: dict, episode_context: dict) -> dict:
    """개별 이벤트에서 교전 컨텍스트 추출 (episode_context를 기반으로 보강)."""
    ctx = dict(episode_context)

    msg  = event.get("message", "")
    tick = event.get("tick") or event.get("game_tick")
    if tick is not None:
        ctx["game_tick"] = int(tick)

    # 메시지에서 부대 유형 추출
    types_in_msg = set()
    for kw in ("전차", "기계화보병", "보병", "포병", "헬기", "드론", "자주포"):
        if kw in msg:
            types_in_msg.add(kw)
    if types_in_msg:
        existing = set(ctx.get("enemy_unit_types", []))
        ctx["enemy_unit_types"] = list(existing | types_in_msg)

    ctx["engagement_type"] = event.get("event_type", "") or event.get("type", "")

    # 이벤트 좌표가 있으면 해당 지점의 지형 샘플링 (이벤트 레벨 정밀 지형)
    coords = _parse_coords_from_message(msg)
    if coords:
        # 이벤트 위치를 적 위치로 추가
        existing_pos = list(ctx.get("enemy_positions", []))
        if list(coords) not in existing_pos:
            existing_pos.append(list(coords))
        ctx["enemy_positions"] = existing_pos

        # 이벤트 좌표 기준 지형 프로파일 (기존 episode terrain보다 정밀)
        evt_terrain = sample_terrain_profile(coords[0], coords[1], radius=2000.0)
        if evt_terrain:
            ctx["terrain"] = evt_terrain

    return ctx


# ── 파싱 유틸 ────────────────────────────────────────────────────────

def _parse_coords_from_message(msg: str):
    """메시지에서 좌표 (x, y) 파싱. 실패 시 None 반환."""
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
    m = re.search(r"([\d.]+)\s*%", msg)
    if m:
        return float(m.group(1))
    return 0.0


# ── 싱글톤 ───────────────────────────────────────────────────────────

_tactical_memory: TacticalMemory = None


def get_tactical_memory() -> TacticalMemory:
    global _tactical_memory
    if _tactical_memory is None:
        _tactical_memory = TacticalMemory()
    return _tactical_memory
