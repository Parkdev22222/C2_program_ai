"""
에피소드 실행 모듈.

WargameEngine을 생성/초기화하고 한 에피소드를 실행하며
EpisodeMetrics를 수집하여 반환합니다.
"""

import json
import logging
import math
import random
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from .metrics import EpisodeMetrics, collect_metrics

logger = logging.getLogger(__name__)


# ── 규칙 기반 전술 AI ────────────────────────────────────────────────

class RuleBasedTactician:
    """
    규칙 기반 전술 AI.

    매 재계획 시 아래 우선순위로 의사결정:
      1. 탐지율 < 40% + 정찰부대 가용 → 정찰 임무
      2. 아군 평균 전투력 < 28% → 전 부대 방어
      3. 적 탐지 있음 + 아군 전투력 충분 → 유형·상황 기반 공격
         - 전투력 30% 이하 개별 부대 → 현위치 방어
         - 정찰부대(Delta/Red4) → 측방 경계
         - 전차(Charlie) → 적 전차·자주포 우선 타격 (고지대 기동)
         - 대전차(Echo) → 적 전차 우선 타격
         - 기계화보병(Alpha, Bravo) → 좌/우 포위 기동
      4. 탐지된 적 없음 → 중앙선 방향 전진 유지

    지형 고도를 고려한 공격 위치 선정과 전술 메모리 패널티 존 회피를 적용합니다.
    """

    # 병종별 우선 타겟 유형 (높을수록 먼저)
    _TARGET_PRIO = {
        "전차":      {"전차": 3, "자주포": 3, "기계화보병": 2, "대전차": 2, "정찰": 1},
        "기계화보병": {"기계화보병": 3, "정찰": 2, "대전차": 2, "전차": 1, "자주포": 1},
        "대전차":    {"전차": 4, "자주포": 3, "기계화보병": 2, "정찰": 1},
        "정찰":      {"정찰": 2, "기계화보병": 1},
        "자주포":    {"기계화보병": 3, "자주포": 2, "전차": 2, "정찰": 1},
    }

    def make_plan(self, state: dict, tm=None) -> dict:
        """
        현재 게임 상태를 분석하여 BLUFOR 임무계획 딕셔너리를 반환.

        Args:
            state: engine.get_state() 결과
            tm: TacticalMemory 인스턴스 (없으면 패널티 미적용)
        """
        units  = state.get("units", [])
        intel  = state.get("intelligence", {}).get("BLUFOR", [])

        blufor = [
            u for u in units
            if u["side"] == "BLUFOR" and u["status"] != "destroyed"
        ]
        if not blufor:
            return {"reasoning": "아군 전멸", "mission_plans": []}

        detected   = [e for e in intel if e["status"] == "detected"]
        approx     = [e for e in intel if e["status"] == "approximate"]
        lost_list  = [e for e in intel if e["status"] == "lost"]
        known_opfor = detected + approx

        total_opfor   = len(intel)
        detected_ratio = len(detected) / max(total_opfor, 1)
        avg_blufor_cp  = sum(u.get("combat_power", 100) for u in blufor) / len(blufor)

        # ── 의사결정 ──────────────────────────────────────────────
        recon_units = [u for u in blufor if u.get("unit_type") == "정찰"
                       and u.get("combat_power", 0) > 40]

        # 1. 정찰 우선
        if detected_ratio < 0.40 and recon_units and (approx or lost_list):
            logger.debug("[Tactician] 결정: 정찰 우선")
            return self._make_recon_plan(blufor, known_opfor, lost_list, tm)

        # 2. 전체 방어
        if avg_blufor_cp < 28.0:
            logger.debug("[Tactician] 결정: 전면 방어 (평균 CP=%.1f%%)", avg_blufor_cp)
            return self._make_defensive_plan(blufor)

        # 3. 탐지된 적 공격
        if known_opfor:
            logger.debug("[Tactician] 결정: 공격 (탐지 %d개)", len(known_opfor))
            return self._make_attack_plan(blufor, known_opfor, tm)

        # 4. 전진 유지
        logger.debug("[Tactician] 결정: 전진 유지")
        return self._make_advance_plan(blufor)

    # ── 정찰 임무 ────────────────────────────────────────────────

    def _make_recon_plan(self, blufor, known_opfor, lost_list, tm) -> dict:
        plans = []
        recon_units = [u for u in blufor if u.get("unit_type") == "정찰"
                       and u.get("combat_power", 0) > 40]

        # 정찰 타겟 선정: lost > approximate 우선
        targets = lost_list if lost_list else known_opfor
        for i, recon in enumerate(recon_units):
            target = targets[i % len(targets)]
            tx, ty = float(target["known_x"]), float(target["known_y"])
            wps = _safe_recon_waypoints(recon["x"], recon["y"], tx, ty, tm)
            plans.append({
                "company_id":  recon["id"],
                "mission_type": "recon",
                "waypoints":   wps,
                "objective":   f"{target['unit_id']} 위치 정밀 확인",
            })

        # 나머지 부대 방어 대기
        for u in blufor:
            if u["id"] not in {p["company_id"] for p in plans}:
                if u.get("unit_type") != "정찰":
                    plans.append({
                        "company_id":  u["id"],
                        "mission_type": "defend",
                        "waypoints":   [[u["x"], u["y"]]],
                        "objective":   "정찰 완료까지 대기",
                    })

        return {"reasoning": "[Tactician] 정찰 우선", "mission_plans": plans}

    # ── 방어 임무 ────────────────────────────────────────────────

    def _make_defensive_plan(self, blufor) -> dict:
        plans = []
        for u in blufor:
            cp = u.get("combat_power", 100)
            if cp < 15:
                # 매우 심각 → 후방 철수
                plans.append({
                    "company_id":   u["id"],
                    "mission_type": "withdraw",
                    "waypoints":    [[max(500.0, u["x"] - 3_000), u["y"]]],
                    "objective":    "후방 철수",
                })
            else:
                plans.append({
                    "company_id":   u["id"],
                    "mission_type": "defend",
                    "waypoints":    [[u["x"], u["y"]]],
                    "objective":    "현위치 방어",
                })
        return {"reasoning": "[Tactician] 전면 방어", "mission_plans": plans}

    # ── 공격 임무 ────────────────────────────────────────────────

    def _make_attack_plan(self, blufor, known_opfor, tm) -> dict:
        plans = []

        # 부대 수에 따른 대형 결정
        attack_units = [
            u for u in blufor
            if u.get("unit_type") not in ("정찰",) and u.get("combat_power", 0) > 30
        ]
        n = len(attack_units)

        for idx, u in enumerate(blufor):
            cp         = u.get("combat_power", 100)
            unit_type  = u.get("unit_type", "기계화보병")

            # 전투력 미달 → 방어
            if cp < 30:
                plans.append({
                    "company_id":   u["id"],
                    "mission_type": "defend",
                    "waypoints":    [[u["x"], u["y"]]],
                    "objective":    "전투력 부족 — 현위치 방어",
                })
                continue

            # 정찰부대 → 측방 경계
            if unit_type == "정찰":
                flank_wp = _flank_watch_point(u["x"], u["y"], known_opfor)
                plans.append({
                    "company_id":   u["id"],
                    "mission_type": "recon",
                    "waypoints":    [flank_wp],
                    "objective":    "측방 경계",
                })
                continue

            # 우선 타겟 선정
            target = _pick_target(u, known_opfor, self._TARGET_PRIO.get(unit_type, {}))
            tx, ty = float(target["known_x"]), float(target["known_y"])

            # 공격 위치 선정 (지형 + 전술 메모리)
            atk_pos = _best_attack_position(u["x"], u["y"], tx, ty, n, idx, tm)

            # 포위 기동: 기계화보병은 좌/우 포위
            if unit_type == "기계화보병" and n >= 3:
                flank_sign = 1 if idx % 2 == 0 else -1
                atk_pos = _flank_attack_position(u["x"], u["y"], tx, ty,
                                                  flank_sign, tm)

            plans.append({
                "company_id":   u["id"],
                "mission_type": "attack",
                "waypoints":    [atk_pos, [round(tx), round(ty)]],
                "objective":    f"{target['unit_id']} 격멸",
            })

        return {"reasoning": "[Tactician] 유형 기반 공격", "mission_plans": plans}

    # ── 전진 유지 ────────────────────────────────────────────────

    def _make_advance_plan(self, blufor) -> dict:
        """적 탐지 없을 때 중앙선(x=15000, y=15000) 방향 전진."""
        plans = []
        for u in blufor:
            if u.get("combat_power", 0) < 20:
                plans.append({
                    "company_id":   u["id"],
                    "mission_type": "defend",
                    "waypoints":    [[u["x"], u["y"]]],
                    "objective":    "전투력 부족 — 대기",
                })
                continue
            # 현재 위치에서 맵 중앙 방향으로 2km 전진
            dx = 15_000 - u["x"]
            dy = 15_000 - u["y"]
            dist = math.hypot(dx, dy) or 1.0
            step = min(2_000, dist * 0.4)
            wp = [
                round(u["x"] + dx / dist * step),
                round(u["y"] + dy / dist * step),
            ]
            plans.append({
                "company_id":   u["id"],
                "mission_type": "advance",
                "waypoints":    [wp],
                "objective":    "전진 정찰",
            })
        return {"reasoning": "[Tactician] 전진 유지", "mission_plans": plans}


# ── 전술 헬퍼 함수 ───────────────────────────────────────────────────

_MAP_W, _MAP_H = 30_000, 30_000
_BORDER = 500


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _elev(x, y) -> float:
    try:
        from wargame.terrain import terrain as _t
        return float(_t.elevation(x, y))
    except Exception:
        return 100.0


def _cover(x, y) -> float:
    try:
        from wargame.terrain import terrain as _t
        return float(_t.cover_factor(x, y))
    except Exception:
        return 0.3


def _score_pos(x, y, tx, ty, tm=None) -> float:
    """후보 위치 (x,y)에서 타겟 (tx,ty) 공격 시 종합 점수."""
    elev     = _elev(x, y)
    cov      = _cover(x, y)
    tgt_elev = _elev(tx, ty)
    elev_adv = elev / max(tgt_elev, 1.0)

    dist = math.hypot(x - tx, y - ty)
    # 유효 교전 거리 (2500m) 안에서 높은 점수
    dist_score = max(0.0, 1.0 - max(0.0, dist - 2_500) / 4_000)

    raw = elev_adv * 0.5 + cov * 0.3 + dist_score * 0.2

    if tm is not None:
        try:
            raw = tm.apply_penalties(x, y, raw)
        except Exception:
            pass
    return raw


def _best_attack_position(ux, uy, tx, ty, total_units, unit_idx, tm=None):
    """
    공격 후보 위치 평가 → 최적 위치 반환.
    16방향 × 3거리 후보 중 _score_pos 기준 최고점 선택.
    """
    best_score, best_pos = -1.0, [round(ux), round(uy)]
    for dist_m in (1_200, 2_000, 3_000):
        for deg in range(0, 360, 22):
            rad = math.radians(deg)
            cx = _clamp(tx + math.cos(rad) * dist_m, _BORDER, _MAP_W - _BORDER)
            cy = _clamp(ty + math.sin(rad) * dist_m, _BORDER, _MAP_H - _BORDER)
            sc = _score_pos(cx, cy, tx, ty, tm)
            if sc > best_score:
                best_score = sc
                best_pos = [round(cx), round(cy)]
    return best_pos


def _flank_attack_position(ux, uy, tx, ty, flank_sign: int, tm=None):
    """
    측방 포위 기동 위치 계산.
    flank_sign: +1 → 좌익, -1 → 우익.
    """
    dx = tx - ux
    dy = ty - uy
    dist = math.hypot(dx, dy) or 1.0
    bearing = math.atan2(dy, dx)

    # 목표 방향에서 ±60° 측방, 2km 거리
    flank_angle = bearing + flank_sign * math.pi / 3
    flank_dist = min(dist * 0.5, 3_000)

    fx = _clamp(ux + math.cos(flank_angle) * flank_dist, _BORDER, _MAP_W - _BORDER)
    fy = _clamp(uy + math.sin(flank_angle) * flank_dist, _BORDER, _MAP_H - _BORDER)

    # 전술 메모리 패널티 → 측방 양쪽 비교 후 낮은 쪽 선택
    if tm is not None:
        try:
            alt_angle = bearing - flank_sign * math.pi / 3
            ax = _clamp(ux + math.cos(alt_angle) * flank_dist, _BORDER, _MAP_W - _BORDER)
            ay = _clamp(uy + math.sin(alt_angle) * flank_dist, _BORDER, _MAP_H - _BORDER)
            sc_f  = _score_pos(fx, fy, tx, ty, tm)
            sc_a  = _score_pos(ax, ay, tx, ty, tm)
            if sc_a > sc_f:
                fx, fy = ax, ay
        except Exception:
            pass

    return [round(fx), round(fy)]


def _flank_watch_point(ux, uy, known_opfor) -> list:
    """정찰부대 측방 경계 위치 — 적 중심에서 90° 측방 3km."""
    if not known_opfor:
        return [round(ux), round(uy)]
    cx = sum(float(e["known_x"]) for e in known_opfor) / len(known_opfor)
    cy = sum(float(e["known_y"]) for e in known_opfor) / len(known_opfor)
    bearing = math.atan2(cy - uy, cx - ux)
    side_angle = bearing + math.pi / 2
    wx = _clamp(ux + math.cos(side_angle) * 3_000, _BORDER, _MAP_W - _BORDER)
    wy = _clamp(uy + math.sin(side_angle) * 3_000, _BORDER, _MAP_H - _BORDER)
    return [round(wx), round(wy)]


def _safe_recon_waypoints(ux, uy, tx, ty, tm=None) -> list:
    """
    정찰 경로: 측방 우회 경유지 → 관측 포인트 (standoff 5km).
    전술 메모리 패널티 최소화 방향 선택.
    """
    dx = tx - ux
    dy = ty - uy
    dist = math.hypot(dx, dy) or 1.0
    bearing = math.atan2(dy, dx)

    # 측방 우회 (60° 또는 -60° 중 패널티 낮은 방향)
    flank_dist = min(dist * 0.45, 5_000)
    best_fscore, fx, fy = -1.0, ux, uy
    for offset in (math.pi / 3, -math.pi / 3):
        fa  = bearing + offset
        _fx = _clamp(ux + math.cos(fa) * flank_dist, _BORDER, _MAP_W - _BORDER)
        _fy = _clamp(uy + math.sin(fa) * flank_dist, _BORDER, _MAP_H - _BORDER)
        sc  = _elev(_fx, _fy) * 0.5 + _cover(_fx, _fy) * 200 * 0.5
        if tm is not None:
            try:
                sc = tm.apply_penalties(_fx, _fy, sc)
            except Exception:
                pass
        if sc > best_fscore:
            best_fscore, fx, fy = sc, _fx, _fy

    # 관측 포인트: 목표 후방 5km (아군 쪽)
    obs_angle = bearing + math.pi
    ox = _clamp(tx + math.cos(obs_angle) * 5_000, _BORDER, _MAP_W - _BORDER)
    oy = _clamp(ty + math.sin(obs_angle) * 5_000, _BORDER, _MAP_H - _BORDER)

    return [[round(fx), round(fy)], [round(ox), round(oy)]]


def _pick_target(unit: dict, known_opfor: list, prio_map: dict) -> dict:
    """우선순위 맵 기반 최적 타겟 선정."""
    if not known_opfor:
        return {"known_x": 15_000, "known_y": 15_000, "unit_id": "unknown"}

    def score(entry):
        type_score = prio_map.get(entry.get("unit_type", ""), 1)
        dist = math.hypot(unit["x"] - entry["known_x"], unit["y"] - entry["known_y"])
        dist_score = max(0.0, 1.0 - dist / 20_000)
        # 탐지 정확도 가산
        det_bonus = 1.5 if entry.get("status") == "detected" else 1.0
        return type_score * det_bonus + dist_score * 0.3

    return max(known_opfor, key=score)


# ── EpisodeRunner ────────────────────────────────────────────────────

class EpisodeRunner:
    """
    단일 워게임 에피소드를 실행하고 메트릭을 수집합니다.

    engine_factory를 통해 새 엔진을 생성하며,
    RuleBasedTactician으로 임무계획을 자동 수립합니다.
    randomize_positions=True(기본값)이면 매 에피소드마다 부대 시작 위치를 랜덤 배치합니다.
    """

    def __init__(
        self,
        engine_factory: Callable,
        agent=None,
        planner=None,
        randomize_positions: bool = True,
    ):
        self._engine_factory     = engine_factory
        self._agent              = agent
        self._planner            = planner
        self._randomize          = randomize_positions
        self._tactician          = RuleBasedTactician()

    def run_episode(
        self,
        max_real_seconds: float = 90.0,
        replan_interval_ticks: int = 120,
        initial_mission: str = "auto",
    ) -> EpisodeMetrics:
        """
        한 에피소드를 실행하고 메트릭을 반환합니다.

        Args:
            max_real_seconds: 에피소드 최대 실행 시간 (실제 초)
            replan_interval_ticks: 재계획 주기 (틱 단위)
            initial_mission: 초기 임무 유형 ("recon" | "attack" | "auto")
        """
        engine = None
        last_plan: dict = {}
        last_plan_tick: int = 0

        try:
            # 1. 새 엔진 생성
            engine = self._engine_factory()
            logger.info("에피소드 시작: 새 엔진 생성")

            # 2. 시작 위치 랜덤화 (옵션)
            if self._randomize:
                _randomize_unit_positions(engine)

            # 3. 엔진 시작
            engine.start()

            # 4. 전술 메모리 로드 (재계획 시 패널티 적용용)
            tm = _load_tactical_memory()

            # 5. 초기 임무계획 적용
            try:
                initial_result = self._apply_initial_plan(engine, initial_mission, tm)
                if initial_result:
                    last_plan = initial_result
                    last_plan_tick = 0
            except Exception as e:
                logger.warning(f"초기 임무계획 적용 실패: {e}")

            # 6. 메인 루프
            start_time = time.time()

            while True:
                if time.time() - start_time > max_real_seconds:
                    logger.info(f"에피소드 시간 초과: {max_real_seconds}초")
                    break

                try:
                    state = engine.get_state()
                except Exception as e:
                    logger.error(f"get_state() 실패: {e}")
                    break

                if state.get("winner"):
                    logger.info(f"에피소드 종료: 승자={state['winner']}")
                    break

                if not state.get("running", True):
                    logger.info("에피소드 종료: 엔진 정지")
                    break

                current_tick = state.get("tick", 0)
                if self._should_replan(engine, last_plan_tick, current_tick, replan_interval_ticks):
                    try:
                        new_plan = self._replan(engine, state, tm)
                        if new_plan:
                            last_plan = new_plan
                            last_plan_tick = current_tick
                            logger.debug(f"재계획 적용: tick={current_tick}")
                    except Exception as e:
                        logger.warning(f"재계획 실패: {e}")

                time.sleep(1.0)

        except Exception as e:
            logger.error(f"run_episode 오류: {e}")
        finally:
            if engine is not None:
                try:
                    engine.stop()
                except Exception as e:
                    logger.warning(f"engine.stop() 오류: {e}")

        # 7. 메트릭 수집
        if engine is not None:
            try:
                metrics = collect_metrics(engine, last_plan=last_plan)
                logger.info(f"에피소드 완료: {metrics.summary_str()}")
                return metrics
            except Exception as e:
                logger.error(f"메트릭 수집 실패: {e}")

        return _default_metrics()

    def _apply_initial_plan(self, engine, mission_type: str, tm) -> Optional[dict]:
        if mission_type == "auto":
            state = engine.get_state()
            intel = state.get("intelligence", {}).get("BLUFOR", [])
            total = len(intel)
            detected = sum(1 for e in intel if e["status"] == "detected")
            mission_type = "recon" if total > 0 and detected / max(total, 1) < 0.4 else "attack"

        if mission_type == "recon":
            return self._apply_rule_plan(engine, "recon", tm)
        return self._apply_rule_plan(engine, "attack", tm)

    def _apply_rule_plan(self, engine, hint: str, tm) -> Optional[dict]:
        """RuleBasedTactician으로 임무계획 생성 후 엔진 적용."""
        try:
            state = engine.get_state()
            # hint가 "recon"이면 탐지율을 강제로 낮춰 정찰 결정 유도
            if hint == "recon":
                # 인텔을 임시로 비워 정찰 결정 강제
                fake_state = dict(state)
                fake_state["intelligence"] = {"BLUFOR": []}
                plan = self._tactician.make_plan(fake_state, tm)
            else:
                plan = self._tactician.make_plan(state, tm)

            if plan and plan.get("mission_plans"):
                engine.apply_mission_plan(plan)
                logger.info(
                    f"[Tactician] 임무 적용: {len(plan['mission_plans'])}개 부대 "
                    f"— {plan.get('reasoning', '')}"
                )
                return plan
        except Exception as e:
            logger.warning(f"규칙 기반 임무계획 적용 실패: {e}")
        return None

    def _should_replan(self, engine, last_plan_tick, current_tick, interval) -> bool:
        if (current_tick - last_plan_tick) >= interval:
            return True
        try:
            recent_events = engine.db.get_recent_events(10)
            for ev in recent_events:
                if ev.get("tick", 0) <= last_plan_tick:
                    continue
                etype = ev.get("event_type", "")
                if etype == "DETECTION":
                    return True
                if etype == "DESTROYED":
                    msg = ev.get("message", "")
                    if any(uid in msg for uid in ("Alpha", "Bravo", "Charlie", "Delta", "Echo")):
                        return True
        except Exception:
            pass
        return False

    def _replan(self, engine, state: dict, tm) -> Optional[dict]:
        """재계획: RuleBasedTactician 호출."""
        try:
            plan = self._tactician.make_plan(state, tm)
            if plan and plan.get("mission_plans"):
                engine.apply_mission_plan(plan)
                return plan
        except Exception as e:
            logger.warning(f"_replan 실패: {e}")
        return None


# ── 유틸 함수 ────────────────────────────────────────────────────────

def _load_tactical_memory():
    """TacticalMemory 싱글톤 로드. 실패 시 None 반환."""
    try:
        from wargame.harness.tactical_memory import get_tactical_memory
        return get_tactical_memory()
    except Exception:
        return None


def _randomize_unit_positions(engine):
    """
    엔진 유닛 위치를 진영별 구역 내 랜덤 값으로 갱신.
    engine.start() 호출 전에 실행해야 합니다.
    """
    try:
        from wargame.scenario import _pick_pos, _BLUFOR_ZONE, _OPFOR_ZONE, _MIN_SEP
    except ImportError:
        logger.warning("scenario 모듈 랜덤 배치 함수 로드 실패 — 위치 고정")
        return

    blufor_placed: list = []
    opfor_placed:  list = []

    for unit in engine.units:
        if unit.side == "BLUFOR":
            x, y = _pick_pos(_BLUFOR_ZONE, blufor_placed, _MIN_SEP)
            blufor_placed.append((x, y))
        else:
            x, y = _pick_pos(_OPFOR_ZONE, opfor_placed, _MIN_SEP)
            opfor_placed.append((x, y))
        unit.x = x
        unit.y = y

    # DB 초기 스냅샷 갱신
    try:
        engine.db.save_units(engine.units)
        engine.db.save_snapshot(0, 0.0, engine.units)
    except Exception as e:
        logger.debug(f"DB 스냅샷 갱신 실패 (무시): {e}")

    logger.info(
        "부대 위치 랜덤화 완료 — "
        f"BLUFOR: {[(round(x/1000,1), round(y/1000,1)) for x,y in blufor_placed]}, "
        f"OPFOR: {[(round(x/1000,1), round(y/1000,1)) for x,y in opfor_placed]}"
    )


def _default_metrics() -> EpisodeMetrics:
    from datetime import datetime
    import uuid as _uuid
    return EpisodeMetrics(
        episode_id=_uuid.uuid4().hex[:12],
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        winner="draw",
        duration_ticks=0,
        blufor_initial=0,
        blufor_survived=0,
        opfor_initial=0,
        opfor_survived=0,
        blufor_survival_rate=0.0,
        opfor_elimination_rate=0.0,
        total_damage_dealt=0.0,
        total_damage_taken=0.0,
        combat_efficiency=1.0,
        detected_engagement_rate=0.0,
        surprise_received_count=0,
        mission_plans_applied=0,
        recon_conducted=False,
        last_plan={},
        events_summary=[],
    )
