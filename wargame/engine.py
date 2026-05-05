"""
워게임 시뮬레이션 엔진.

시간 체계:
  time_scale: 실제 1초 = game_time_scale 게임 초 (기본 60 = 1분/초)
  tick_interval: 실제 몇 초마다 tick (기본 0.5s = 2Hz)
  dt: 1 tick당 게임 시간(초) = tick_interval * time_scale

교전 모델 (란체스터 선형 근사):
  공격자_화력 = 화력지수 * (전투력/100)
  방어자_피해 = 공격자_화력 * 교전계수 * 고도우위 * (1-엄폐) * dt / 3600 * base_rate
  base_rate = 20.0  → 만편성 쌍방 교전 시 시간당 20% 전투력 손실
"""

import math
import threading
import time
from typing import List, Optional, Callable

from .models import Unit, AirSupport, AIR_SUPPORT_PRESETS, WargameDB
from .terrain import terrain

# 교전 파라미터
ENGAGEMENT_RANGE = 2_500.0       # m — 기계화 보병 직사거리
SUPPRESSION_RANGE = 4_000.0      # m — 제압사격 거리
BASE_ATTRITION_RATE = 20.0       # %/hour at full strength, full engagement
SUPPRESSION_THRESHOLD = 40.0     # % 이하 전투력 → 제압 상태
DESTROYED_THRESHOLD = 5.0        # % 이하 → 전투 불능


def _engagement_factor(dist: float) -> float:
    """거리 기반 교전 효과 계수 (0~1)."""
    if dist <= 1_000:
        return 1.0
    elif dist <= ENGAGEMENT_RANGE:
        return 1.0 - (dist - 1_000) / (ENGAGEMENT_RANGE - 1_000) * 0.5
    elif dist <= SUPPRESSION_RANGE:
        return 0.5 - (dist - ENGAGEMENT_RANGE) / (SUPPRESSION_RANGE - ENGAGEMENT_RANGE) * 0.4
    return 0.0


class WargameEngine:
    """
    메인 워게임 시뮬레이션 엔진.

    Usage:
        engine = WargameEngine(units)
        engine.start()
        engine.apply_mission_plan(plan_dict)
        state = engine.get_state()
        engine.stop()
    """

    def __init__(
        self,
        units: List[Unit],
        db: Optional[WargameDB] = None,
        time_scale: float = 60.0,
        tick_interval: float = 0.5,
        on_tick: Optional[Callable] = None,
    ):
        self.units: List[Unit] = units
        self.db = db or WargameDB()
        self.time_scale = time_scale        # 게임 시간 배율
        self.tick_interval = tick_interval  # 실제 tick 간격 (초)
        self.on_tick = on_tick              # tick마다 호출할 콜백

        self.tick = 0
        self.game_time = 0.0               # 게임 시간 (초)
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.air_supports: List[AirSupport] = []   # 공중지원 임무 목록
        self._opfor_ai_last: float = 0.0           # 마지막 OPFOR AI 갱신 게임시간
        self.opfor_ai_fire_count: int = 0       # OPFOR AI 발동 횟수 (UI 알람용)

        # 초기 상태 저장
        self.db.save_units(units)
        self.db.save_snapshot(0, 0.0, units)

    # ── 외부 API ─────────────────────────────────────────────────

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="WargameLoop")
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3)

    def apply_air_support_plan(self, plan: dict):
        """
        공중지원 계획 JSON을 등록.

        plan 형식:
          {"air_support_plans": [
            {
              "call_sign": "DARKSTAR-1",
              "support_type": "cas",       # cas | strike | artillery | helicopter
              "target": [x, y],            # 폭격 중심 좌표 (m)
              "radius": 1500,              # 선택 — 없으면 preset 기본값
              "delay": 120                 # 선택 — 투입 지연 (게임 초)
            }, ...
          ]}
        """
        with self._lock:
            for sp in plan.get("air_support_plans", []):
                stype = sp.get("support_type", "cas")
                preset = AIR_SUPPORT_PRESETS.get(stype, AIR_SUPPORT_PRESETS["cas"])
                target = sp.get("target", [0, 0])
                as_obj = AirSupport(
                    call_sign=sp.get("call_sign", f"AIR-{len(self.air_supports)+1}"),
                    support_type=stype,
                    target_x=float(target[0]),
                    target_y=float(target[1]),
                    radius=float(sp.get("radius", preset["radius"])),
                    damage_rate=float(sp.get("damage_rate", preset["damage_rate"])),
                    duration=float(sp.get("duration", preset["duration"])),
                    delay=float(sp.get("delay", preset["delay"])),
                    status="pending",
                    elapsed=0.0,
                )
                self.air_supports.append(as_obj)
                self.db.log_event(
                    self.tick, self.game_time, "AIR_ORDER",
                    f"{as_obj.call_sign} ({stype}) 요청 — "
                    f"목표({target[0]/1000:.1f}km,{target[1]/1000:.1f}km) "
                    f"반경{as_obj.radius:.0f}m 지연{as_obj.delay:.0f}s"
                )

    def reset(self, units: List[Unit]):
        """시뮬레이션 초기화 (새 부대 목록으로 재설정)."""
        was_running = self.running
        self.stop()
        with self._lock:
            self.units = units
            self.air_supports = []
            self.tick = 0
            self.game_time = 0.0
            self._opfor_ai_last = 0.0
            self.opfor_ai_fire_count = 0
            self.db.clear()
            self.db.save_units(units)
            self.db.save_snapshot(0, 0.0, units)
        if was_running:
            self.start()

    def apply_mission_plan(self, plan: dict):
        """
        LLM이 생성한 임무계획 JSON을 부대에 적용.

        plan 형식:
          {"mission_plans": [
            {"company_id": "Alpha", "waypoints": [[x,y],...], "mission_type": "attack"},
            ...
          ]}
        """
        with self._lock:
            id_map = {u.id: u for u in self.units}
            for mp in plan.get("mission_plans", []):
                uid = mp.get("company_id", "")
                if uid not in id_map:
                    continue
                u = id_map[uid]
                if not u.is_active():
                    continue
                wps = [[float(p[0]), float(p[1])] for p in mp.get("waypoints", [])]
                u.waypoints = wps
                u.current_action = mp.get("mission_type", "move")
                self.db.update_unit(u)
                self.db.log_event(
                    self.tick, self.game_time, "ORDER",
                    f"{uid} 임무부여: {u.current_action} → {len(wps)}개 WP"
                )

    def get_state(self) -> dict:
        """현재 시뮬레이션 상태를 딕셔너리로 반환."""
        with self._lock:
            units_data = []
            for u in self.units:
                units_data.append({
                    "id": u.id,
                    "side": u.side,
                    "x": round(u.x, 1),
                    "y": round(u.y, 1),
                    "elevation": round(terrain.elevation(u.x, u.y), 1),
                    "combat_power": round(u.combat_power, 1),
                    "status": u.status,
                    "current_action": u.current_action,
                    "waypoints": u.waypoints,
                    "color": u.color,
                })
            return {
                "tick": self.tick,
                "game_time": self.game_time,
                "game_time_str": _fmt_time(self.game_time),
                "units": units_data,
                "running": self.running,
                "winner": self._check_winner(),
                "opfor_ai_fire_count": self.opfor_ai_fire_count,
            "air_supports": [
                {
                    "call_sign": a.call_sign,
                    "support_type": a.support_type,
                    "target_x": a.target_x,
                    "target_y": a.target_y,
                    "radius": a.radius,
                    "status": a.status,
                    "delay_remaining": max(0.0, a.delay - a.elapsed) if a.status == "pending" else 0.0,
                    "duration_remaining": max(0.0, a.duration - a.elapsed) if a.status == "active" else 0.0,
                }
                for a in self.air_supports
            ],
            }

    # ── 시뮬레이션 루프 ──────────────────────────────────────────

    def _loop(self):
        while self.running:
            t0 = time.time()
            with self._lock:
                self._tick()
            elapsed = time.time() - t0
            sleep_time = max(0.0, self.tick_interval - elapsed)
            if sleep_time:
                time.sleep(sleep_time)

    def _tick(self):
        dt = self.tick_interval * self.time_scale  # 게임 시간(초)
        self._move_units(dt)
        self._resolve_combat(dt)
        self._resolve_air_support(dt)
        self._update_opfor_ai(dt)
        self._update_status()
        self.tick += 1
        self.game_time += dt

        # 10 tick마다 스냅샷
        if self.tick % 10 == 0:
            self.db.save_snapshot(self.tick, self.game_time, self.units)

        if self.on_tick:
            try:
                self.on_tick(self.get_state.__func__(self))  # state without lock
            except Exception:
                pass

        # 승리 조건 확인
        winner = self._check_winner()
        if winner:
            self.running = False
            self.db.log_event(self.tick, self.game_time, "ENDEX",
                              f"전투 종료: {winner} 승리")

    # ── 이동 ─────────────────────────────────────────────────────

    def _move_units(self, dt: float):
        for u in self.units:
            if not u.is_active() or u.status == "suppressed":
                continue
            if not u.waypoints:
                continue

            target = u.waypoints[0]
            tx, ty = target[0], target[1]
            dx, dy = tx - u.x, ty - u.y
            dist = math.hypot(dx, dy)

            if dist < 50.0:
                # 웨이포인트 도달
                u.waypoints.pop(0)
                if not u.waypoints:
                    u.current_action = "hold"
                    self.db.log_event(
                        self.tick, self.game_time, "WAYPOINT",
                        f"{u.id} 목표지점 도착"
                    )
                continue

            spd = u.max_speed * terrain.movement_speed_factor(u.x, u.y)
            # 전투력에 따른 속도 감소 (전투력 30% 미만 → 속도 50%)
            if u.combat_power < 30:
                spd *= 0.5 + u.combat_power / 60
            step = min(spd * dt, dist)
            u.x += dx / dist * step
            u.y += dy / dist * step

    # ── 교전 ─────────────────────────────────────────────────────

    def _resolve_combat(self, dt: float):
        active = [u for u in self.units if u.is_active()]
        blufor = [u for u in active if u.side == "BLUFOR"]
        opfor  = [u for u in active if u.side == "OPFOR"]

        dt_h = dt / 3600.0  # 게임 시간을 시간 단위로 변환

        for attacker in blufor:
            for defender in opfor:
                self._exchange_fire(attacker, defender, dt_h)

        for attacker in opfor:
            for defender in blufor:
                self._exchange_fire(attacker, defender, dt_h)

    def _exchange_fire(self, attacker: Unit, defender: Unit, dt_h: float):
        dist = attacker.distance_to(defender)
        ef = _engagement_factor(dist)
        if ef <= 0:
            return

        elev_adv = terrain.elevation_advantage(
            attacker.x, attacker.y, defender.x, defender.y
        )
        cover = terrain.cover_factor(defender.x, defender.y)

        atk_fp = attacker.effective_firepower()
        damage = (
            atk_fp / 100.0          # 정규화
            * BASE_ATTRITION_RATE   # %/hr 기준 피해율
            * ef                    # 거리 교전 계수
            * elev_adv              # 고도 우위
            * (1 - cover)           # 엄폐 감소
            * dt_h                  # 시간 적용
        )

        # 난수 변동 ±30%
        import random
        damage *= random.uniform(0.7, 1.3)
        defender.combat_power = max(0.0, defender.combat_power - damage)

        # 교전 로그 (5% 이상 피해 시만)
        if damage >= 5.0:
            self.db.log_event(
                self.tick, self.game_time, "COMBAT",
                f"{attacker.id}→{defender.id}: -{damage:.1f}% CP "
                f"(거리{dist/1000:.1f}km, 고도우위{elev_adv:.2f})"
            )

    # ── 공중지원 ─────────────────────────────────────────────────

    def _resolve_air_support(self, dt: float):
        import random
        dt_h = dt / 3600.0
        opfor = [u for u in self.units if u.side == "OPFOR" and u.is_active()]

        for air in self.air_supports:
            if air.status == "completed":
                continue

            if air.status == "pending":
                air.elapsed += dt
                if air.elapsed >= air.delay:
                    air.elapsed = 0.0
                    air.status = "active"
                    self.db.log_event(
                        self.tick, self.game_time, "AIR_ACTIVE",
                        f"{air.call_sign} ({air.support_type}) 투입 — "
                        f"목표({air.target_x/1000:.1f}km,{air.target_y/1000:.1f}km)"
                    )
                continue

            # active: 반경 내 OPFOR에 피해 적용
            air.elapsed += dt
            for u in opfor:
                dist = math.hypot(u.x - air.target_x, u.y - air.target_y)
                if dist > air.radius:
                    continue
                # 거리 감쇠: 중심 1.0 → 반경 끝 0.0
                proximity = 1.0 - dist / air.radius
                cover = terrain.cover_factor(u.x, u.y) * 0.5  # 엄폐 절반만 적용
                damage = (
                    air.damage_rate
                    * proximity
                    * (1.0 - cover)
                    * dt_h
                    * random.uniform(0.7, 1.3)
                )
                u.combat_power = max(0.0, u.combat_power - damage)
                if damage >= 3.0:
                    self.db.log_event(
                        self.tick, self.game_time, "AIR_STRIKE",
                        f"{air.call_sign}→{u.id}: -{damage:.1f}% CP "
                        f"(거리{dist/1000:.1f}km)"
                    )

            if air.elapsed >= air.duration:
                air.status = "completed"
                self.db.log_event(
                    self.tick, self.game_time, "AIR_COMPLETE",
                    f"{air.call_sign} ({air.support_type}) 임무 완료"
                )

    # ── 양측 룰 기반 AI ──────────────────────────────────────────

    _OPFOR_AI_INTERVAL = 60.0

    def _update_opfor_ai(self, dt: float):
        """양측 룰 기반 AI 업데이트 (60초 주기)."""
        self._opfor_ai_last += dt
        if self._opfor_ai_last < self._OPFOR_AI_INTERVAL:
            return
        self._opfor_ai_last = 0.0
        self._run_faction_ai("OPFOR")
        self._run_faction_ai("BLUFOR")
        self.opfor_ai_fire_count += 1

    def _run_faction_ai(self, side: str):
        """단일 진영 룰 기반 행동 결정."""
        enemy_side = "OPFOR" if side == "BLUFOR" else "BLUFOR"
        enemies = [u for u in self.units if u.side == enemy_side and u.is_active()]
        if not enemies:
            return

        en_cx = sum(u.x for u in enemies) / len(enemies)
        en_cy = sum(u.y for u in enemies) / len(enemies)
        log_type = f"{side}_AI"

        for u in self.units:
            if u.side != side or not u.is_active():
                continue
            if u.status == "suppressed":
                u.waypoints = []
                u.current_action = "defend"
                continue

            nearest = min(enemies, key=lambda e: u.distance_to(e))
            dist = u.distance_to(nearest)

            if u.combat_power < 30:
                self._ai_withdraw(u, side, log_type)
            elif u.max_speed >= 4.0:
                self._ai_recon(u, nearest, en_cx, en_cy, log_type)
            elif u.max_speed <= 1.9:
                self._ai_standoff(u, en_cx, en_cy, log_type)
            elif u.firepower_index >= 140:
                self._ai_armor(u, nearest, dist, log_type)
            else:
                self._ai_infantry(u, nearest, dist, en_cx, en_cy, side, log_type)

    def _ai_withdraw(self, u: Unit, side: str, log_type: str):
        """후퇴: 자기 진영 집결지 방향으로 이동."""
        if side == "OPFOR":
            rally_x = max(u.x, 22_000.0)
            rally_y = max(u.y, 20_000.0)
        else:
            rally_x = min(u.x, 9_000.0)
            rally_y = min(u.y, 8_000.0)
        u.waypoints = [[rally_x, rally_y]]
        u.current_action = "withdraw"
        self.db.log_event(self.tick, self.game_time, log_type,
                          f"{u.id} 전투력저하({u.combat_power:.0f}%) → 후퇴")

    def _ai_recon(self, u: Unit, nearest: Unit,
                  en_cx: float, en_cy: float, log_type: str):
        """정찰: 적 진형 측방을 고속 우회 기동."""
        dist = u.distance_to(nearest)
        if dist <= ENGAGEMENT_RANGE:
            angle = math.atan2(u.y - nearest.y, u.x - nearest.x) + math.pi / 2
            flank_x = max(0, min(29_999, u.x + math.cos(angle) * 3_000))
            flank_y = max(0, min(29_999, u.y + math.sin(angle) * 3_000))
            u.waypoints = [[flank_x, flank_y],
                           [max(0, min(29_999, nearest.x + math.cos(angle) * 1_500)),
                            max(0, min(29_999, nearest.y + math.sin(angle) * 1_500))]]
            u.current_action = "flank"
        else:
            angle = math.atan2(en_cy - u.y, en_cx - u.x)
            side_angle = angle + math.pi / 2
            u.waypoints = [[max(0, min(29_999, en_cx + math.cos(side_angle) * 4_000)),
                            max(0, min(29_999, en_cy + math.sin(side_angle) * 4_000))]]
            u.current_action = "flank"
        self.db.log_event(self.tick, self.game_time, log_type,
                          f"{u.id}(정찰) → 측방 우회 기동")

    def _ai_standoff(self, u: Unit, en_cx: float, en_cy: float, log_type: str):
        """자주포/지원화기: 최적 사격 위치 유지."""
        dist_to_en = math.hypot(u.x - en_cx, u.y - en_cy)
        ideal_dist = SUPPRESSION_RANGE * 0.85
        if abs(dist_to_en - ideal_dist) < 500:
            u.waypoints = []
            u.current_action = "hold"
        else:
            angle = math.atan2(u.y - en_cy, u.x - en_cx)
            u.waypoints = [[max(0, min(29_999, en_cx + math.cos(angle) * ideal_dist)),
                            max(0, min(29_999, en_cy + math.sin(angle) * ideal_dist))]]
            u.current_action = "move"
        self.db.log_event(self.tick, self.game_time, log_type,
                          f"{u.id}(지원) → 사격진지 유지 (거리{dist_to_en/1000:.1f}km)")

    def _ai_armor(self, u: Unit, nearest: Unit, dist: float, log_type: str):
        """전차: 최근접 적을 향해 공세 기동."""
        if dist <= 1_500:
            u.waypoints = []
            u.current_action = "attack"
        else:
            angle = math.atan2(nearest.y - u.y, nearest.x - u.x)
            target_x = nearest.x - math.cos(angle) * 1_200
            target_y = nearest.y - math.sin(angle) * 1_200
            mid_x = (u.x + target_x) / 2
            mid_y = (u.y + target_y) / 2
            u.waypoints = [[max(0, min(29_999, mid_x)), max(0, min(29_999, mid_y))],
                           [max(0, min(29_999, target_x)), max(0, min(29_999, target_y))]]
            u.current_action = "attack"
        self.db.log_event(self.tick, self.game_time, log_type,
                          f"{u.id}(전차) → {nearest.id} 공격 기동 (거리{dist/1000:.1f}km)")

    def _ai_infantry(self, u: Unit, nearest: Unit, dist: float,
                      en_cx: float, en_cy: float, side: str, log_type: str):
        """기계화보병: 교전거리까지 전진, 좌우 엇갈려 정면 압박."""
        faction_ids = [v.id for v in self.units if v.side == side]
        idx = faction_ids.index(u.id) if u.id in faction_ids else 0
        side_sign = 1 if idx % 2 == 0 else -1
        perp_x = -(en_cy - u.y)
        perp_y = en_cx - u.x
        perp_len = math.hypot(perp_x, perp_y) or 1
        offset = 800 * side_sign

        if dist <= ENGAGEMENT_RANGE:
            u.waypoints = []
            u.current_action = "attack"
        else:
            angle = math.atan2(en_cy - u.y, en_cx - u.x)
            target_dist = ENGAGEMENT_RANGE * 0.8
            target_x = en_cx - math.cos(angle) * target_dist + perp_x / perp_len * offset
            target_y = en_cy - math.sin(angle) * target_dist + perp_y / perp_len * offset
            u.waypoints = [[max(0, min(29_999, target_x)),
                            max(0, min(29_999, target_y))]]
            u.current_action = "attack"
        self.db.log_event(self.tick, self.game_time, log_type,
                          f"{u.id}(보병) → 정면 압박 (거리{dist/1000:.1f}km)")

    # ── 상태 갱신 ─────────────────────────────────────────────────

    def _update_status(self):
        for u in self.units:
            if u.combat_power <= DESTROYED_THRESHOLD:
                if u.status != "destroyed":
                    u.status = "destroyed"
                    u.waypoints = []
                    self.db.log_event(
                        self.tick, self.game_time, "DESTROYED",
                        f"{u.id} 전투불능"
                    )
            elif u.combat_power <= SUPPRESSION_THRESHOLD:
                if u.status == "active":
                    u.status = "suppressed"
                    self.db.log_event(
                        self.tick, self.game_time, "SUPPRESSED",
                        f"{u.id} 제압됨 (CP {u.combat_power:.0f}%)"
                    )
            else:
                if u.status == "suppressed":
                    u.status = "active"
                    self.db.log_event(
                        self.tick, self.game_time, "RECOVERED",
                        f"{u.id} 제압 해제"
                    )

    def _check_winner(self) -> Optional[str]:
        blu_alive = any(u.is_active() for u in self.units if u.side == "BLUFOR")
        opf_alive = any(u.is_active() for u in self.units if u.side == "OPFOR")
        if not blu_alive and not opf_alive:
            return "무승부"
        if not opf_alive:
            return "BLUFOR"
        if not blu_alive:
            return "OPFOR"
        return None


def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
