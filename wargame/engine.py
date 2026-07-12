"""
워게임 시뮬레이션 엔진 (개선판).

시간 체계:
  time_scale:    실제 1초 = game_time_scale 게임 초 (기본 60 = 1분/초)
  tick_interval: 실제 몇 초마다 tick (기본 0.5s = 2Hz)
  dt:            1 tick당 게임 시간(초) = tick_interval * time_scale

교전 모델 개선 사항:
  - 병종별 교전 사거리 분리 (전차 3km / 대전차 4km / 기계화보병 1.5km 등)
  - 병종 상성 계수 (대전차→전차 ×2.0, 자주포→보병 ×1.8 등)
  - 교전 집중도 제한 (최근접 적에 집중, 나머지 30% 제압사격)
  - 자주포 간접사격 분리 (직사 교전 제외, AoE 간접사격)
  - 상태 4단계화 (active/degraded/suppressed/destroyed)
  - LOS 지형 차폐 탐지 반영
  - 이동 중 피탐지 증가 / 정지+엄폐 피탐지 감소
  - 확률적 탐지 (거리 threshold → 확률 함수)
  - Dead Reckoning (탐지 상실 후 마지막 속도로 위치 추정)
"""

import logging
import math
import random
import threading
import time
from typing import Dict, List, Optional, Callable, Tuple

from .models import Unit, AirSupport, AIR_SUPPORT_PRESETS, WargameDB
from .terrain import terrain

logger = logging.getLogger(__name__)

# ── 병종별 교전 사거리 ────────────────────────────────────────────────
_DIRECT_RANGE: Dict[str, float] = {
    "전차":      3_000.0,
    "기계화보병": 1_500.0,
    "대전차":    4_000.0,
    "자주포":        0.0,   # 직사 교전 불가, 별도 간접사격
    "정찰":        800.0,
}
_SUPPRESS_RANGE: Dict[str, float] = {
    "전차":      5_000.0,
    "기계화보병": 2_500.0,
    "대전차":    4_500.0,
    "자주포":        0.0,
    "정찰":      1_200.0,
}
_INDIRECT_MAX_RANGE = 15_000.0   # 자주포 간접사격 최대 사거리
_INDIRECT_MIN_RANGE =    800.0   # 자주포 간접사격 최소 사거리 (근거리 사각지대)
_SPG_CLOSE_RANGE    =  1_000.0   # 이 거리 이내에서 자주포는 근거리 취약 패널티 적용
_SPG_CLOSE_MULT     =      1.8   # 근거리 교전 시 자주포 피해 배율 (1.0 = 패널티 없음)

# ── 병종 상성 계수 ────────────────────────────────────────────────────
_MATCHUP: Dict[Tuple[str, str], float] = {
    ("전차",       "전차"):        1.0,
    ("전차",       "기계화보병"):   1.4,
    ("전차",       "대전차"):       0.8,
    ("전차",       "자주포"):       1.2,
    ("전차",       "정찰"):         1.5,
    ("기계화보병", "전차"):         0.4,
    ("기계화보병", "기계화보병"):   1.0,
    ("기계화보병", "대전차"):       1.0,
    ("기계화보병", "자주포"):       0.8,
    ("기계화보병", "정찰"):         1.2,
    ("대전차",     "전차"):         2.0,
    ("대전차",     "기계화보병"):   0.7,
    ("대전차",     "대전차"):       0.9,
    ("대전차",     "자주포"):       1.5,
    ("대전차",     "정찰"):         0.8,
    ("자주포",     "전차"):         0.5,
    ("자주포",     "기계화보병"):   1.8,
    ("자주포",     "대전차"):       1.0,
    ("자주포",     "자주포"):       0.8,
    ("자주포",     "정찰"):         1.2,
    ("정찰",       "전차"):         0.2,
    ("정찰",       "기계화보병"):   0.3,
    ("정찰",       "대전차"):       0.3,
    ("정찰",       "자주포"):       0.4,
    ("정찰",       "정찰"):         0.5,
}

# ── 부대 상태 임계값 (4단계) ─────────────────────────────────────────
DESTROYED_THRESHOLD  = 15.0   # CP ≤ 15%  → 전투불능
SUPPRESSED_THRESHOLD = 30.0   # CP ≤ 30%  → 제압 (이동 불가, 화력 ×0.3)
DEGRADED_THRESHOLD   = 50.0   # CP ≤ 50%  → 저하 (이동속도 ×0.7, 화력 ×0.8)

# ── 기타 파라미터 ─────────────────────────────────────────────────────
BASE_ATTRITION_RATE  = 20.0   # %/hour — 만편성 쌍방 교전 기준 손실률
_CONTACT_RANGE       = 1_500  # 근접 조우 (병종 무관)
_APPROX_INIT_RADIUS  = 1_000  # 초기 개략 위치 오차 반경(m) — 실제 위치 반경 1km 이내
_DETECT_RANGE: Dict[str, float] = {
    "정찰":      8_000,
    "전차":      4_000,
    "기계화보병": 3_000,
    "대전차":    3_000,
    "자주포":    2_000,
}
# 확률적 탐지: 탐지범위 × 이 비율 이내면 100% 탐지
_DETECT_CERTAIN_RATIO = 0.5
# 이동 중 피탐지 배율 / 정지+엄폐 피탐지 배율
_EXPOSURE_MOVING     = 1.5
_EXPOSURE_CONCEALED  = 0.6
# Dead Reckoning: 탐지 상실 후 틱당 노이즈 증가량 (m)
_DR_NOISE_PER_TICK   = 80.0
# 제압 상태 회복: 교전 밖으로 이탈 후 이 게임초 경과 시 degraded로 회복
_SUPPRESS_RECOVER_SEC = 120.0
# 대포병 탐지(counter-battery): 자주포가 간접사격하면 위치가 적에게 노출된다.
#   기본은 음향표정 수준의 approximate, 확률적으로 대포병 레이더가 정확 위치(detected)를 포착.
#   사격을 멈추면 _update_intelligence의 ticks_since_lost 감쇠로 lost 처리 → shoot-and-scoot.
_COUNTER_BATTERY_DETECT_PROB   = 0.35   # 사격 시 정확 위치(detected) 포착 확률(틱당)
_COUNTER_BATTERY_APPROX_RADIUS = 700    # approximate 노출 시 개략 위치 오차 반경(m)
# 포병 화력지원 전투력 감쇠 지수: 간접사격 위력이 전투력(combat_power) 감소에
# 초선형(제곱)으로 약해진다. effective_firepower()가 이미 선형 factor(cp/100)를 포함하므로
# 여기서는 (cp/100)^(exp-1) 을 추가로 곱해 총 (cp/100)^exp 가 되게 한다.
#   exp=2.0 → CP 100%:×1.0, 75%:×0.56, 50%:×0.25, 25%:×0.06  (피해 많이 입을수록 급감)
_SPG_FIRE_DEGRADE_EXP = 2.0
# 자주포 사격 종료 후 지도에 사격 표시가 잔류하는 시간(게임초) — 고배속에서 시인성 확보
_INDIRECT_FIRE_LINGER = 30.0

# ── OPFOR 전략 AI 파라미터 ────────────────────────────────────────────
# 정찰 완료 임계값: BLUFOR 탐지 수가 이 이상이면 임무 결정 단계로 전환
_OPFOR_DETECT_THRESHOLD = 2
# 방어 진지 간 최소 이격 거리
_OPFOR_DEFEND_MIN_SEP = 3_000.0
# BLUFOR 초기 배치 중심 (남서부) — 정찰 방향 기준점
_BLUFOR_APPROX_CX = 8_000.0
_BLUFOR_APPROX_CY = 8_000.0

# ── 은밀 기동(발각 회피) 경로 파라미터 ────────────────────────────────
# LLM이 준 임무 waypoint의 각 구간을 적 정찰 발각 위험이 낮은 경로로 확장한다.
_STEALTH_STEP_M       = 800.0    # 위험 적분 시 경로 샘플 간격(m)
_STEALTH_EXPOSURE_W   = 0.15     # 지형 노출(엄폐 부족) 기본 위험 가중치
_STEALTH_MIN_LEG_M    = 2_000.0  # 이보다 짧은 구간은 우회 분할 중단
_STEALTH_MAX_DEPTH    = 3        # 구간 재귀 분할 최대 깊이
_STEALTH_OFFSETS_M    = (1_500.0, 3_000.0, 5_000.0)  # 측방 우회 후보 크기
# 우회로 위험 적분이 직선 대비 이 비율 미만이어야 채택 (약간의 우회는 허용)
_STEALTH_IMPROVE_RATIO = 0.98

# ── 표적 추격/재계획 파라미터 ──────────────────────────────────────────
# 경유지 완주 후 표적이 이 거리(m)보다 멀면 표적 인지 위치로 재기동(추격)
_PURSUE_REACQUIRE_M = 60.0
# 임무 발령 후 표적이 이 거리(m) 이상 이동하면 LLM 공격 재계획 트리거
_TARGET_MOVE_REPLAN_M = 1_000.0


# ── 모듈 레벨 헬퍼 ────────────────────────────────────────────────────

def _los_quality(x1: float, y1: float, x2: float, y2: float) -> float:
    """
    두 좌표 간 시선(LOS) 품질 반환 (1.0=완전개방 / 0.0=완전차폐).
    경로를 8등분하여 중간 지형이 직선 고도 보간을 초과하면 차폐.
    """
    try:
        samples = 8
        e1 = terrain.elevation(x1, y1)
        e2 = terrain.elevation(x2, y2)
        worst_block = 0.0
        for i in range(1, samples):
            t = i / samples
            sx = x1 + (x2 - x1) * t
            sy = y1 + (y2 - y1) * t
            mid_e = terrain.elevation(sx, sy)
            interp_e = e1 + (e2 - e1) * t
            block = max(0.0, (mid_e - interp_e) / 80.0)
            worst_block = max(worst_block, block)
        return max(0.0, 1.0 - worst_block)
    except Exception:
        return 1.0


def _engagement_factor(attacker_type: str, dist: float) -> float:
    """병종별 사거리 기반 교전 효과 계수 (0~1)."""
    if attacker_type == "자주포":
        return 0.0   # 직사 교전 불가
    d_range = _DIRECT_RANGE.get(attacker_type, 2_000.0)
    s_range = _SUPPRESS_RANGE.get(attacker_type, 3_000.0)
    inner   = d_range * 0.4
    if dist <= inner:
        return 1.0
    elif dist <= d_range:
        return 1.0 - (dist - inner) / (d_range - inner) * 0.5
    elif dist <= s_range:
        return 0.5 - (dist - d_range) / (s_range - d_range) * 0.4
    return 0.0


def _matchup_factor(atk_type: str, def_type: str) -> float:
    """공격자-방어자 병종 상성 계수."""
    return _MATCHUP.get((atk_type, def_type), 1.0)


def _status_firepower_mult(status: str) -> float:
    """상태별 화력 배율."""
    return {"active": 1.0, "degraded": 0.8, "suppressed": 0.3}.get(status, 0.0)


def _status_speed_mult(status: str) -> float:
    """상태별 이동속도 배율."""
    return {"active": 1.0, "degraded": 0.7, "suppressed": 0.0}.get(status, 0.0)


class WargameEngine:
    """
    메인 워게임 시뮬레이션 엔진 (개선판).

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
        on_game_over=None,
    ):
        self.units: List[Unit] = units
        self.db = db or WargameDB()
        self.time_scale    = time_scale
        self.tick_interval = tick_interval
        self.on_tick       = on_tick
        self.on_game_over  = on_game_over

        self.tick      = 0
        self.game_time = 0.0
        self.running   = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.air_supports: List[AirSupport] = []
        self._opfor_ai_last: float = 0.0
        self.opfor_ai_fire_count: int = 0
        self._blufor_llm_units: set = set()
        # 자주포 간접사격 현황 (지도 표시용) — {shooter_id: {...}}, 사격 시 갱신·정지 후 잔류
        self._spg_fire_state: Dict[str, dict] = {}

        # Dead Reckoning 속도 추적
        self._prev_positions: Dict[str, Tuple[float, float]] = {}
        self._unit_velocity:  Dict[str, Tuple[float, float]] = {}

        # 제압 회복 타이머: {unit_id: 제압 해제 가능 게임시간}
        self._suppress_recover_at: Dict[str, float] = {}

        # FOW 인텔
        self._intelligence: dict = {"BLUFOR": {}, "OPFOR": {}}
        self._init_intelligence()

        # OPFOR 전략 상태 머신
        self._opfor_strategy: str = "recon"      # "recon" | "defend" | "attack"
        self._opfor_strategy_decided: bool = False
        self._opfor_defend_positions: Dict[str, Tuple[float, float]] = {}

        # 신규 탐지 자동 임무계획 훅
        # BLUFOR가 OPFOR를 최초 탐지 시 호출: callback(enemy_id, unit_type, x, y)
        self.on_new_opfor_detection: Optional[Callable] = None
        self._auto_plan_triggered_ids: set = set()  # 이미 자동 임무계획 발동된 OPFOR ID

        # BLUFOR 전투력 임계값 도달 자동 임무계획 훅
        # callback(unit_id, unit_type, threshold_pct, current_cp)
        # threshold_pct: 70 또는 30 (각 유닛×임계값 조합당 1회만 발동)
        self.on_blufor_cp_threshold: Optional[Callable] = None
        _CP_THRESHOLDS = [70.0, 30.0]
        # {unit_id: set of already-fired threshold values}
        self._blufor_cp_thresholds_fired: Dict[str, set] = {}
        self._CP_THRESHOLDS: list = _CP_THRESHOLDS

        # BLUFOR 유닛이 OPFOR 공중지원에 피격 시 호출
        # callback(unit_id, unit_type, call_sign, current_cp)
        # 공중지원 1회당 1회만 발동 (air.hit_reported 플래그로 관리)
        self.on_blufor_air_hit: Optional[Callable] = None

        # BLUFOR 공격부대의 표적이 임무 발령 후 1km 이상 이동 시 호출
        # callback(unit_id, unit_type, target_id, moved_dist_m)
        # 부대별 1회만 발동 (u.target_replan_fired 플래그로 관리)
        self.on_target_moved: Optional[Callable] = None

        # UAV 완전 정찰 모드: True면 매 틱 모든 활성 적을 실위치로 detected 처리 (FOW 무시)
        self.full_recon: bool = False

        # OPFOR 공중지원 쿨다운 (게임 초 단위)
        self._opfor_air_cooldown: float = 0.0
        self._OPFOR_AIR_INTERVAL = 900.0   # 게임 15분마다 공중지원 요청 가능

        # 공중지원 횟수 제한 (양측 공통)
        self._AIR_USE_LIMIT   = 5          # 300틱당 최대 사용 횟수
        self._AIR_RESET_TICKS = 300        # 횟수 리셋 주기 (틱)
        self._air_use_count: Dict[str, int] = {"BLUFOR": 0, "OPFOR": 0}
        self._air_reset_at: int = self._AIR_RESET_TICKS  # 다음 리셋 틱

        self.db.save_units(units)
        self.db.save_snapshot(0, 0.0, units)

    # ── 외부 API ─────────────────────────────────────────────────────

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="WargameLoop"
        )
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3)

    def run_until_over(self, timeout_real_seconds: float = 120.0) -> dict:
        """시뮬레이션 실행 후 게임 종료 또는 타임아웃까지 블로킹 대기."""
        import threading as _th
        done = _th.Event()
        orig_cb = self.on_game_over

        def _done(state):
            done.set()
            if orig_cb:
                try:
                    orig_cb(state)
                except Exception:
                    pass

        self.on_game_over = _done
        self.start()
        done.wait(timeout=timeout_real_seconds)
        self.stop()
        self.on_game_over = orig_cb
        return self.get_state()

    def apply_air_support_plan(self, plan: dict):
        with self._lock:
            self._apply_air_support_plan_locked(plan, side="BLUFOR")

    def _apply_air_support_plan_locked(self, plan: dict, side: str = "BLUFOR"):
        """락 보유 상태에서 공중지원 계획 적용 (BLUFOR/OPFOR 공용)."""
        for sp in plan.get("air_support_plans", []):
            if self._air_use_count.get(side, 0) >= self._AIR_USE_LIMIT:
                logger.warning(
                    f"[공중지원 제한] {side} 공중지원 횟수 초과 "
                    f"({self._air_use_count[side]}/{self._AIR_USE_LIMIT}) — 요청 무시"
                )
                continue
            self._air_use_count[side] = self._air_use_count.get(side, 0) + 1
            stype  = sp.get("support_type", "cas")
            preset = AIR_SUPPORT_PRESETS.get(stype, AIR_SUPPORT_PRESETS["cas"])
            target = sp.get("target", [0, 0])
            as_obj = AirSupport(
                call_sign=sp.get("call_sign", f"{side[:3]}-AIR-{len(self.air_supports)+1}"),
                support_type=stype,
                target_x=float(target[0]),
                target_y=float(target[1]),
                radius=float(sp.get("radius",      preset["radius"])),
                damage_rate=float(sp.get("damage_rate", preset["damage_rate"])),
                duration=float(sp.get("duration",    preset["duration"])),
                delay=float(sp.get("delay",       preset["delay"])),
                side=side,
                status="pending",
                elapsed=0.0,
            )
            self.air_supports.append(as_obj)
            self.db.log_event(
                self.tick, self.game_time, "AIR_ORDER",
                f"[{side}] {as_obj.call_sign} ({stype}) 요청 — "
                f"목표({target[0]/1000:.1f}km,{target[1]/1000:.1f}km) "
                f"반경{as_obj.radius:.0f}m 지연{as_obj.delay:.0f}s",
            )

    def reset(self, units: List[Unit]):
        was_running = self.running
        self.stop()
        with self._lock:
            self.units             = units
            self.air_supports      = []
            self._spg_fire_state   = {}
            self.tick              = 0
            self.game_time         = 0.0
            self._opfor_ai_last    = 0.0
            self.opfor_ai_fire_count = 0
            self._blufor_llm_units = set()
            self._prev_positions   = {}
            self._unit_velocity    = {}
            self._suppress_recover_at = {}
            self._intelligence     = {"BLUFOR": {}, "OPFOR": {}}
            self._init_intelligence()
            self._opfor_strategy          = "recon"
            self._opfor_strategy_decided  = False
            self._opfor_defend_positions  = {}
            self._auto_plan_triggered_ids   = set()
            self._blufor_cp_thresholds_fired = {}
            self._opfor_air_cooldown         = 0.0
            self._air_use_count  = {"BLUFOR": 0, "OPFOR": 0}
            self._air_reset_at   = self._AIR_RESET_TICKS
            self.db.clear()
            self.db.save_units(units)
            self.db.save_snapshot(0, 0.0, units)
        if was_running:
            self.start()

    def clear_detection_triggers(self):
        """재계획 완료 후 호출 — 다음 탐지/임계값 이벤트가 다시 발동될 수 있도록 초기화."""
        with self._lock:
            self._auto_plan_triggered_ids.clear()
            # CP 임계값은 부대별로 유지 (같은 CP 레벨에서 재발동 방지)
            # — 단, 유닛이 완전히 재초기화되지 않는 한 동일 임계값 재발동은 없음

    def apply_mission_plan(self, plan: dict):
        with self._lock:
            id_map = {u.id: u for u in self.units}
            for mp in plan.get("mission_plans", []):
                uid = mp.get("company_id", "")
                if uid not in id_map:
                    continue
                u = id_map[uid]
                if not u.is_active():
                    continue
                try:
                    wps = [[float(p[0]), float(p[1])] for p in mp.get("waypoints", [])]
                except Exception:
                    wps = [[float(p["x"]), float(p["y"])] for p in mp.get("waypoints", [])]
                # BLUFOR 하위 제대: LLM이 준 목표 waypoint는 유지하되,
                # 각 구간(현위치→A, A→B, ...)을 적 정찰 발각 위험이 낮은
                # 은밀 기동 경로로 확장한다.
                raw_wp_count = len(wps)
                # 임무 오버레이용 최종 목표(은밀경로 확장 전 원본 마지막 WP) 저장
                _objective = list(wps[-1]) if wps else None
                if u.side == "BLUFOR" and wps:
                    wps = self._stealth_expand_waypoints(u, wps)
                u.waypoints       = wps
                u.current_action  = mp.get("mission_type", "move")
                u.mission_type    = mp.get("mission_type", "move")
                if _objective is not None:
                    u.mission_objective_x, u.mission_objective_y = _objective[0], _objective[1]

                # ── 표적 부대 지정 (공격 임무) ─────────────────────────
                # LLM이 이 부대가 담당할 적 부대(target_unit_id)를 명시하면,
                # 경유지 완주 후 표적의 현재 인지 위치로 지속 추격한다.
                u.pursuing            = False
                u.target_replan_fired = False
                tid = mp.get("target_unit_id") or mp.get("target_id")
                u.target_unit_id = tid if tid else None
                u.target_ref_x = u.target_ref_y = None
                if tid and u.side == "BLUFOR":
                    ref = self._intelligence.get("BLUFOR", {}).get(tid)
                    if ref and ref.get("status") in ("detected", "approximate"):
                        u.target_ref_x = ref["known_x"]
                        u.target_ref_y = ref["known_y"]

                if u.side == "BLUFOR" and wps:
                    self._blufor_llm_units.add(uid)
                    u.mission_lock_ticks = 30  # 신규 임무 발령 → 30틱간 AI 개입 차단
                self.db.update_unit(u)
                _wp_note = (f"{len(wps)}개 WP"
                            if len(wps) == raw_wp_count
                            else f"{len(wps)}개 WP (은밀경로 확장 {raw_wp_count}→{len(wps)})")
                self.db.log_event(
                    self.tick, self.game_time, "ORDER",
                    f"{uid} 임무부여: {u.current_action} → {_wp_note}",
                )

    # ── FOW 인텔 ─────────────────────────────────────────────────────

    def _init_intelligence(self):
        """양측에 적 위치 개략 정보(approximate) 부여."""
        for observer in ("BLUFOR", "OPFOR"):
            enemy_side = "OPFOR" if observer == "BLUFOR" else "BLUFOR"
            self._intelligence[observer] = {}
            for u in self.units:
                if u.side != enemy_side:
                    continue
                # 실제 위치 반경 _APPROX_INIT_RADIUS(1km) 이내 원형 분포로 오차 부여
                _ang = random.uniform(0.0, 2.0 * math.pi)
                _rad = _APPROX_INIT_RADIUS * math.sqrt(random.random())
                nx = math.cos(_ang) * _rad
                ny = math.sin(_ang) * _rad
                self._intelligence[observer][u.id] = {
                    "unit_id":            u.id,
                    "enemy_side":         enemy_side,
                    "status":             "approximate",
                    "known_x":            max(0.0, min(29_999.0, u.x + nx)),
                    "known_y":            max(0.0, min(29_999.0, u.y + ny)),
                    "unit_type":          "",
                    "combat_power":       None,
                    "last_detected_tick": -1,
                    "detected_by":        None,
                    "ticks_since_lost":   0,
                    "ever_detected":      False,
                }

    def _update_intelligence(self):
        """
        매 틱 호출. 개선된 탐지 로직:
          1. LOS 지형 차폐 반영
          2. 이동 중 피탐지 증가 / 정지+엄폐 피탐지 감소
          3. 확률적 탐지 (거리 함수 기반)
          4. Dead Reckoning (탐지 상실 후 속도 기반 위치 추정)
        """
        dt = self.tick_interval * self.time_scale

        for observer in ("BLUFOR", "OPFOR"):
            enemy_side  = "OPFOR" if observer == "BLUFOR" else "BLUFOR"
            obs_units   = [u for u in self.units if u.side == observer and u.is_active()]
            enemy_units = [u for u in self.units if u.side == enemy_side]

            for enemy in enemy_units:
                entry = self._intelligence[observer].get(enemy.id)
                if entry is None:
                    continue

                # 격멸된 부대: Dead Reckoning 중지 + 탐지 상실 처리
                if enemy.status == "destroyed":
                    if entry["status"] in ("detected", "approximate"):
                        entry["status"] = "lost"
                        entry["ticks_since_lost"] = 0
                    self._unit_velocity[enemy.id] = (0.0, 0.0)
                    continue

                # UAV 완전 정찰 모드: 모든 활성 적을 실위치로 항상 detected (FOW 무시)
                if getattr(self, "full_recon", False):
                    entry.update({
                        "status": "detected",
                        "known_x": enemy.x, "known_y": enemy.y,
                        "unit_type": enemy.unit_type,
                        "combat_power": round(enemy.combat_power, 1),
                        "last_detected_tick": self.tick, "detected_by": "UAV",
                        "ticks_since_lost": 0, "ever_detected": True,
                    })
                    continue

                # 적 부대 이동/정지 여부에 따른 피탐지 배율
                is_moving = bool(enemy.waypoints)
                cov       = terrain.cover_factor(enemy.x, enemy.y)
                if is_moving:
                    exposure = _EXPOSURE_MOVING
                elif cov >= 0.4:
                    exposure = _EXPOSURE_CONCEALED
                else:
                    exposure = 1.0

                # 관측부대별 탐지 판정
                detected_by = None
                for obs in obs_units:
                    base_range = max(
                        _DETECT_RANGE.get(obs.unit_type, 3_000),
                        _CONTACT_RANGE,
                    )
                    dist = obs.distance_to(enemy)

                    # LOS 차폐 반영
                    los = _los_quality(obs.x, obs.y, enemy.x, enemy.y)
                    if los < 0.3:
                        effective_range = _CONTACT_RANGE  # 차폐 심하면 근접만
                    elif los < 0.7:
                        effective_range = base_range * 0.5
                    else:
                        effective_range = base_range

                    effective_range *= exposure

                    # 확률적 탐지
                    certain_range = effective_range * _DETECT_CERTAIN_RATIO
                    if dist <= certain_range:
                        detect_prob = 1.0
                    elif dist <= effective_range:
                        detect_prob = 1.0 - (dist - certain_range) / (effective_range - certain_range) * 0.9
                    else:
                        detect_prob = 0.0

                    if detect_prob > 0 and random.random() < detect_prob:
                        detected_by = obs
                        break

                if detected_by is not None:
                    prev_status = entry["status"]
                    entry.update({
                        "status":             "detected",
                        "known_x":            enemy.x,
                        "known_y":            enemy.y,
                        "unit_type":          enemy.unit_type,
                        "combat_power":       round(enemy.combat_power, 1),
                        "last_detected_tick": self.tick,
                        "detected_by":        detected_by.id,
                        "ticks_since_lost":   0,
                        "ever_detected":      True,
                    })
                    if prev_status != "detected":
                        self.db.log_event(
                            self.tick, self.game_time, "DETECTION",
                            f"[{observer}] {detected_by.id}({detected_by.unit_type})가 "
                            f"적 {enemy.id}({enemy.unit_type}) 탐지 — "
                            f"위치({enemy.x/1000:.1f}km, {enemy.y/1000:.1f}km)",
                        )
                        # BLUFOR가 OPFOR를 최초 탐지 → 자동 임무계획 콜백
                        if (observer == "BLUFOR"
                                and enemy.id not in self._auto_plan_triggered_ids
                                and self.on_new_opfor_detection is not None):
                            self._auto_plan_triggered_ids.add(enemy.id)
                            try:
                                self.on_new_opfor_detection(
                                    enemy.id, enemy.unit_type, enemy.x, enemy.y
                                )
                            except Exception as _cb_err:
                                logger.error(f"on_new_opfor_detection 콜백 오류: {_cb_err}")
                else:
                    if entry.get("ever_detected"):
                        # 한 번이라도 탐지된 부대: 지속 추적 —
                        # 실제 위치·전투력을 매 틱 동기화하여
                        # 지도 마커·패널 게이지·공중지원 목표 좌표가 실제와 일치하도록 유지
                        entry["known_x"] = enemy.x
                        entry["known_y"] = enemy.y
                        entry["combat_power"] = round(enemy.combat_power, 1)
                        entry["unit_type"] = enemy.unit_type
                        entry["ticks_since_lost"] = 0
                        entry["status"] = "detected"  # 탐지 상태 유지
                    elif entry["status"] == "detected":
                        entry["status"] = "lost"
                        entry["ticks_since_lost"] = 0
                        self.db.log_event(
                            self.tick, self.game_time, "DETECTION_LOST",
                            f"[{observer}] 적 {enemy.id} 탐지 상실 — "
                            f"최종 위치({entry['known_x']/1000:.1f}km, {entry['known_y']/1000:.1f}km)",
                        )
                    elif entry["status"] == "lost":
                        # Dead Reckoning: 마지막 속도 벡터로 위치 추정 + 누적 노이즈
                        vx, vy = self._unit_velocity.get(enemy.id, (0.0, 0.0))
                        entry["known_x"] = max(0.0, min(29_999.0,
                            entry["known_x"] + vx * dt))
                        entry["known_y"] = max(0.0, min(29_999.0,
                            entry["known_y"] + vy * dt))
                        tsl = entry.get("ticks_since_lost", 0)
                        noise = min(tsl * _DR_NOISE_PER_TICK, 3_000.0)
                        entry["known_x"] += random.uniform(-noise * 0.5, noise * 0.5)
                        entry["known_y"] += random.uniform(-noise * 0.5, noise * 0.5)
                        entry["ticks_since_lost"] = tsl + 1

    def get_intelligence_report(self, side: str = "BLUFOR") -> dict:
        with self._lock:
            entries = list(self._intelligence.get(side, {}).values())
            return {
                "side":       side,
                "game_time":  _fmt_time(self.game_time),
                "tick":       self.tick,
                "enemy_intel": [
                    {
                        "unit_id":            e["unit_id"],
                        "status":             e["status"],
                        "known_x_m":          int(e["known_x"]),
                        "known_y_m":          int(e["known_y"]),
                        "unit_type":          e["unit_type"] or "미확인",
                        "combat_power":       e["combat_power"],
                        "detected_by":        e["detected_by"],
                        "last_detected_tick": e["last_detected_tick"],
                    }
                    for e in entries
                ],
            }

    def _recent_indirect_fire(self) -> list:
        """지도 표시용 최근 자주포 사격 목록 (linger 이내). FOW: 적 포병 위치는
        아군 인텔로 탐지된 경우에만 사수 위치를 노출(shooter_visible)."""
        out = []
        blufor_intel = self._intelligence.get("BLUFOR", {})
        for e in self._spg_fire_state.values():
            if self.game_time - e["game_time"] > _INDIRECT_FIRE_LINGER:
                continue
            if e["side"] == "BLUFOR":
                shooter_visible = True
            else:
                intel = blufor_intel.get(e["shooter_id"])
                shooter_visible = bool(intel and intel.get("status") in ("detected", "approximate"))
            item = {
                "shooter_id":      e["shooter_id"],
                "side":            e["side"],
                "unit_type":       e["unit_type"],
                "target_x":        e["target_x"],
                "target_y":        e["target_y"],
                "radius":          e["radius"],
                "shooter_visible": shooter_visible,
            }
            if shooter_visible:
                item["shooter_x"] = e["shooter_x"]
                item["shooter_y"] = e["shooter_y"]
            out.append(item)
        return out

    def get_state(self) -> dict:
        with self._lock:
            units_data = []
            for u in self.units:
                units_data.append({
                    "id":             u.id,
                    "side":           u.side,
                    "unit_type":      u.unit_type,
                    "x":              round(u.x, 1),
                    "y":              round(u.y, 1),
                    "elevation":      round(terrain.elevation(u.x, u.y), 1),
                    "combat_power":   round(u.combat_power, 1),
                    "status":         u.status,
                    "current_action": u.current_action,
                    "waypoints":      u.waypoints,
                    "color":          u.color,
                    "target_unit_id": u.target_unit_id,
                    "pursuing":       u.pursuing,
                    "mission_type":   u.mission_type,
                    "mission_objective": (
                        [round(u.mission_objective_x, 1), round(u.mission_objective_y, 1)]
                        if u.mission_objective_x is not None else None
                    ),
                })
            intel_data = {}
            for side, entries in self._intelligence.items():
                intel_data[side] = [
                    {
                        "unit_id":      e["unit_id"],
                        "status":       e["status"],
                        "known_x":      round(e["known_x"], 1),
                        "known_y":      round(e["known_y"], 1),
                        "unit_type":    e["unit_type"],
                        "combat_power": e["combat_power"],
                        "detected_by":  e["detected_by"],
                    }
                    for e in entries.values()
                ]
            return {
                "tick":               self.tick,
                "game_time":          self.game_time,
                "game_time_str":      _fmt_time(self.game_time),
                "units":              units_data,
                "running":            self.running,
                "winner":             self._check_winner(),
                "opfor_ai_fire_count": self.opfor_ai_fire_count,
                "intelligence":       intel_data,
                "air_supports": [
                    {
                        "call_sign":          a.call_sign,
                        "support_type":       a.support_type,
                        "target_x":           a.target_x,
                        "target_y":           a.target_y,
                        "radius":             a.radius,
                        "status":             a.status,
                        "delay_remaining":    max(0.0, a.delay - a.elapsed) if a.status == "pending" else 0.0,
                        "duration_remaining": max(0.0, a.duration - a.elapsed) if a.status == "active" else 0.0,
                    }
                    for a in self.air_supports
                ],
                "air_use_count":  dict(self._air_use_count),
                "air_use_limit":  self._AIR_USE_LIMIT,
                "air_reset_at":   self._air_reset_at,
                "indirect_fire":  self._recent_indirect_fire(),
            }

    # ── 시뮬레이션 루프 ──────────────────────────────────────────────

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
        dt = self.tick_interval * self.time_scale
        # 임무 잠금 카운트다운 (매 틱 차감)
        for u in self.units:
            if u.mission_lock_ticks > 0:
                u.mission_lock_ticks -= 1
        self._move_units(dt)
        self._update_velocity_tracking(dt)
        self._update_intelligence()
        self._check_target_moved()   # 담당 표적 1km+ 이동 → 재계획 트리거
        self._resolve_combat(dt)
        self._resolve_indirect_fire(dt)
        self._resolve_air_support(dt)
        self._update_opfor_ai(dt)
        self._update_status(dt)
        self.tick      += 1
        self.game_time += dt

        # 공중지원 횟수 300틱마다 리셋
        if self.tick >= self._air_reset_at:
            self._air_use_count = {"BLUFOR": 0, "OPFOR": 0}
            self._air_reset_at  = self.tick + self._AIR_RESET_TICKS
            self.db.log_event(
                self.tick, self.game_time, "AIR_RESET",
                f"공중지원 횟수 리셋 (다음 리셋: tick {self._air_reset_at})",
            )

        self.db.save_unit_realtime(self.tick, self.game_time, self.units)
        if self.tick % 10 == 0:
            self.db.save_snapshot(self.tick, self.game_time, self.units)

        if self.on_tick:
            try:
                self.on_tick(self.get_state())
            except Exception:
                pass

        winner = self._check_winner()
        if winner:
            self.running = False
            self.db.log_event(self.tick, self.game_time, "ENDEX",
                              f"전투 종료: {winner} 승리")
            if self.on_game_over:
                try:
                    self.on_game_over(self.get_state())
                except Exception as e:
                    logger.warning(f"on_game_over callback error: {e}")

    # ── 이동 ─────────────────────────────────────────────────────────

    def _move_units(self, dt: float):
        for u in self.units:
            if not u.is_active():
                continue
            spd_mult = _status_speed_mult(u.status)
            if spd_mult <= 0:
                continue   # suppressed: 이동 불가

            # 추격 중: 매 틱 표적 현재 인지 위치로 경로를 갱신하여 지속 추종
            if u.pursuing:
                self._on_waypoints_empty(u)

            if not u.waypoints:
                # 경유지 없음: (추격 아님) 완주 처리 or 추격 개시
                if not u.pursuing:
                    self._on_waypoints_empty(u)
                if not u.waypoints:
                    continue   # 추격 근접 유지 or 완주(hold) → 이번 틱 이동 없음

            target = u.waypoints[0]
            tx, ty = target[0], target[1]
            dx, dy = tx - u.x, ty - u.y
            dist   = math.hypot(dx, dy)

            if dist < 50.0:
                u.waypoints.pop(0)
                if not u.waypoints:
                    self._on_waypoints_empty(u)
                continue

            base_spd = u.max_speed * terrain.movement_speed_factor(u.x, u.y)
            spd      = base_spd * spd_mult
            step     = min(spd * dt, dist)
            u.x += dx / dist * step
            u.y += dy / dist * step

    def _blufor_known_enemy_pos(self, target_id: str):
        """
        BLUFOR가 인지한 적 부대(target_id)의 현재 위치를 반환.
        표적이 격멸됐거나 인텔이 lost면 None (추격 종료 신호).
        """
        tu = next((x for x in self.units if x.id == target_id), None)
        if tu is not None and not tu.is_active():
            return None
        e = self._intelligence.get("BLUFOR", {}).get(target_id)
        if not e or e.get("status") not in ("detected", "approximate"):
            return None
        return (e["known_x"], e["known_y"])

    def _on_waypoints_empty(self, u: "Unit"):
        """
        경유지를 모두 소진한 BLUFOR 부대 처리.
        - 공격/측방 임무 + 표적 지정 부대: 표적 현재 인지 위치로 지속 추격
          (표적이 _PURSUE_REACQUIRE_M 이상 이동하면 그 위치로 재기동)
        - 그 외(또는 표적 상실): 임무 종료 → hold 전환
        """
        if u.side != "BLUFOR":
            return

        # 표적 지속 추격
        if u.current_action in ("attack", "flank") and u.target_unit_id:
            known = self._blufor_known_enemy_pos(u.target_unit_id)
            if known is not None:
                tx, ty = known
                if math.hypot(tx - u.x, ty - u.y) > _PURSUE_REACQUIRE_M:
                    if not u.pursuing:
                        self.db.log_event(
                            self.tick, self.game_time, "PURSUIT",
                            f"{u.id} 표적 {u.target_unit_id} 추격 개시 "
                            f"→ ({tx/1000:.1f}km, {ty/1000:.1f}km)",
                        )
                    u.waypoints = [[tx, ty]]     # 표적 현위치로 갱신
                    self._blufor_llm_units.add(u.id)   # 추격 중 AI 개입 차단 유지
                else:
                    u.waypoints = []             # 근접 — 정지 후 교전에 맡김
                    self._blufor_llm_units.add(u.id)
                u.pursuing = True
                return
            # 표적 상실/격멸 → 추격 종료
            if u.pursuing or u.target_unit_id:
                self.db.log_event(
                    self.tick, self.game_time, "PURSUIT",
                    f"{u.id} 표적 {u.target_unit_id} 상실/격멸 — 추격 종료",
                )
            u.pursuing = False
            u.target_unit_id = None

        # 임무 종료 → hold (LLM 임무 부대였을 때 1회)
        if u.id in self._blufor_llm_units:
            u.current_action     = "hold"
            u.mission_lock_ticks = 0
            self._blufor_llm_units.discard(u.id)
            self.db.log_event(
                self.tick, self.game_time, "WAYPOINT",
                f"{u.id} 목표지점 도착",
            )

    def _check_target_moved(self):
        """
        BLUFOR 공격부대가 경유지로 접근하는 중(추격 전), 담당 표적이
        임무 발령 시점 대비 _TARGET_MOVE_REPLAN_M 이상 이동하면
        on_target_moved 콜백을 부대별 1회 발동 → LLM 공격 재계획.
        """
        if self.on_target_moved is None:
            return
        for u in self.units:
            if u.side != "BLUFOR" or not u.is_active():
                continue
            if u.pursuing or u.target_replan_fired:
                continue
            if u.current_action not in ("attack", "flank"):
                continue
            if not u.target_unit_id or u.target_ref_x is None:
                continue
            if not u.waypoints:
                continue   # 이미 완주(추격 단계) → 재계획 불필요
            known = self._blufor_known_enemy_pos(u.target_unit_id)
            if known is None:
                continue
            moved = math.hypot(known[0] - u.target_ref_x, known[1] - u.target_ref_y)
            if moved >= _TARGET_MOVE_REPLAN_M:
                u.target_replan_fired = True
                self.db.log_event(
                    self.tick, self.game_time, "TARGET_MOVED",
                    f"{u.id} 담당표적 {u.target_unit_id} {moved/1000:.1f}km 이동 "
                    f"→ 공격 재계획 트리거",
                )
                try:
                    self.on_target_moved(
                        u.id, u.unit_type, u.target_unit_id, round(moved, 1)
                    )
                except Exception as _cb_err:
                    logger.error(f"on_target_moved 콜백 오류: {_cb_err}")

    def _update_velocity_tracking(self, dt: float):
        """Dead Reckoning용 부대 속도 추적."""
        for u in self.units:
            prev = self._prev_positions.get(u.id)
            if prev and dt > 0:
                vx = (u.x - prev[0]) / dt
                vy = (u.y - prev[1]) / dt
                self._unit_velocity[u.id] = (vx, vy)
            self._prev_positions[u.id] = (u.x, u.y)

    # ── 직사 교전 ────────────────────────────────────────────────────

    def _resolve_combat(self, dt: float):
        """
        직사 교전 해소.
        - 자주포 제외 (별도 _resolve_indirect_fire)
        - 교전 집중도: 최근접 적에 100% 화력 / 나머지는 30% 제압사격
        """
        active = [u for u in self.units if u.is_active()]
        blufor = [u for u in active if u.side == "BLUFOR" and u.unit_type != "자주포"]
        opfor  = [u for u in active if u.side == "OPFOR"  and u.unit_type != "자주포"]
        dt_h   = dt / 3600.0

        for attacker in blufor:
            enemies_in_range = [
                e for e in opfor
                if _engagement_factor(attacker.unit_type, attacker.distance_to(e)) > 0
            ]
            if not enemies_in_range:
                continue
            primary = min(enemies_in_range, key=attacker.distance_to)
            self._exchange_fire(attacker, primary, dt_h, focus=True)
            for secondary in enemies_in_range:
                if secondary is not primary:
                    self._exchange_fire(attacker, secondary, dt_h, focus=False)

        for attacker in opfor:
            enemies_in_range = [
                e for e in blufor
                if _engagement_factor(attacker.unit_type, attacker.distance_to(e)) > 0
            ]
            if not enemies_in_range:
                continue
            primary = min(enemies_in_range, key=attacker.distance_to)
            self._exchange_fire(attacker, primary, dt_h, focus=True)
            for secondary in enemies_in_range:
                if secondary is not primary:
                    self._exchange_fire(attacker, secondary, dt_h, focus=False)

    def _check_blufor_cp_threshold(self, unit: Unit, cp_before: float):
        """BLUFOR 유닛의 CP가 임계값(70%, 30%)을 새로 통과했으면 콜백 발동."""
        if unit.side != "BLUFOR" or self.on_blufor_cp_threshold is None:
            return
        if unit.id not in self._blufor_cp_thresholds_fired:
            self._blufor_cp_thresholds_fired[unit.id] = set()
        fired = self._blufor_cp_thresholds_fired[unit.id]
        for thr in self._CP_THRESHOLDS:
            if thr in fired:
                continue
            # cp_before > thr 이었는데 현재 <= thr 이면 임계값 통과
            if cp_before > thr and unit.combat_power <= thr:
                fired.add(thr)
                try:
                    self.on_blufor_cp_threshold(
                        unit.id, unit.unit_type, thr, round(unit.combat_power, 1)
                    )
                except Exception as _cb_err:
                    logger.error(f"on_blufor_cp_threshold 콜백 오류: {_cb_err}")

    def _exchange_fire(self, attacker: Unit, defender: Unit,
                       dt_h: float, focus: bool = True):
        dist = attacker.distance_to(defender)
        ef   = _engagement_factor(attacker.unit_type, dist)
        if ef <= 0:
            return

        elev_adv = terrain.elevation_advantage(
            attacker.x, attacker.y, defender.x, defender.y
        )
        cover = terrain.cover_factor(defender.x, defender.y)

        # FOW 정확도 수정자
        atk_status = self._intelligence[attacker.side].get(
            defender.id, {}
        ).get("status", "approximate")
        accuracy_mult = {"detected": 1.0, "approximate": 0.6, "lost": 0.3}.get(
            atk_status, 0.6
        )

        # 기습 효과
        def_status = self._intelligence[defender.side].get(
            attacker.id, {}
        ).get("status", "approximate")
        surprise_mult = {"detected": 1.0, "approximate": 1.3, "lost": 1.6}.get(
            def_status, 1.0
        )

        # 집중 여부 (비집중 = 제압사격 30%)
        focus_mult = 1.0 if focus else 0.3

        # 상태별 화력 배율
        fp_mult = _status_firepower_mult(attacker.status)

        # 병종 상성
        matchup = _matchup_factor(attacker.unit_type, defender.unit_type)

        # 자주포 근거리 취약성: _SPG_CLOSE_RANGE 이내에서 적이 접근하면 방어력 대폭 저하
        spg_vuln = (
            _SPG_CLOSE_MULT if defender.unit_type == "자주포" and dist < _SPG_CLOSE_RANGE
            else 1.0
        )

        atk_fp = attacker.effective_firepower()
        damage = (
            atk_fp / 100.0
            * BASE_ATTRITION_RATE
            * ef
            * elev_adv
            * (1.0 - cover)
            * accuracy_mult
            * surprise_mult
            * focus_mult
            * fp_mult
            * matchup
            * spg_vuln
            * dt_h
        ) * random.uniform(0.7, 1.3)

        _cp_before = defender.combat_power
        defender.combat_power = max(0.0, defender.combat_power - damage)
        self._check_blufor_cp_threshold(defender, _cp_before)

        # 교전 발생 시 공격자 측 인텔리전스에 피탐지 즉시 반영
        # (실제로 교전 중인 적은 위치가 노출된 것이므로)
        intel_entry = self._intelligence[attacker.side].get(defender.id)
        if intel_entry is not None and intel_entry["status"] != "detected":
            intel_entry.update({
                "status":             "detected",
                "known_x":            defender.x,
                "known_y":            defender.y,
                "unit_type":          defender.unit_type,
                "combat_power":       round(defender.combat_power, 1),
                "last_detected_tick": self.tick,
                "detected_by":        attacker.id,
                "ticks_since_lost":   0,
                "ever_detected":      True,
            })

        if surprise_mult >= 1.6 and damage >= 3.0:
            self.db.log_event(
                self.tick, self.game_time, "SURPRISE",
                f"[기습] {attacker.id}({attacker.unit_type}) → "
                f"{defender.id}({defender.unit_type}) "
                f"기습 성공! (×{surprise_mult:.1f}) -{damage:.1f}% CP "
                f"(거리{dist/1000:.1f}km)",
            )
        elif damage >= 5.0:
            tags = []
            if not focus:
                tags.append("제압사격")
            if surprise_mult > 1.0:
                tags.append(f"부분기습×{surprise_mult:.1f}")
            if atk_status != "detected":
                tags.append("개략사격" if atk_status == "approximate" else "맹목사격")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            self.db.log_event(
                self.tick, self.game_time, "COMBAT",
                f"{attacker.id}({attacker.unit_type})→"
                f"{defender.id}({defender.unit_type}): "
                f"-{damage:.1f}% CP "
                f"(거리{dist/1000:.1f}km, 고도우위{elev_adv:.2f}, "
                f"상성×{matchup:.1f}){tag_str}",
            )

    # ── 자주포 간접사격 ──────────────────────────────────────────────

    def _resolve_indirect_fire(self, dt: float):
        """
        자주포(SPG) 별도 간접사격.
        탐지 정확도에 따라 AoE 반경 가변:
          detected  → 반경 600m  (정밀 사격)
          approximate → 반경 1800m (개략 사격)
          lost      → 사격 중단
        """
        dt_h  = dt / 3600.0
        spgs  = [
            u for u in self.units
            if u.is_active() and u.unit_type == "자주포"
        ]
        if not spgs:
            return

        for spg in spgs:
            enemy_side = "OPFOR" if spg.side == "BLUFOR" else "BLUFOR"
            enemies    = [u for u in self.units if u.side == enemy_side and u.is_active()]
            intel      = self._intelligence[spg.side]
            fp_mult    = _status_firepower_mult(spg.status)
            # 포병 화력지원은 전투력이 낮을수록 초선형으로 약해진다 (피해 많이 입을수록 급감)
            spg_fire_degrade = (max(0.0, spg.combat_power) / 100.0) ** (_SPG_FIRE_DEGRADE_EXP - 1.0)

            # 탐지된 적 목표 선정 (detected or approximate)
            # 최소 사거리(_INDIRECT_MIN_RANGE) 이내 목표는 사격 불가 (자주포 특성상 근거리 사각지대)
            # 체계별 실사거리 (K9 ~40km, 곡산 ~60km 등). 미지정 시 기본값.
            spg_max_range = getattr(spg, "indirect_range", _INDIRECT_MAX_RANGE)
            targets = []
            for entry in intel.values():
                if entry["status"] == "lost":
                    continue
                dist_to_target = math.hypot(
                    spg.x - entry["known_x"],
                    spg.y - entry["known_y"],
                )
                if _INDIRECT_MIN_RANGE <= dist_to_target <= spg_max_range:
                    targets.append(entry)

            if not targets:
                continue

            # 가장 가까운 탐지 목표에 사격
            target_entry = min(
                targets,
                key=lambda e: math.hypot(spg.x - e["known_x"], spg.y - e["known_y"]),
            )
            det_status = target_entry["status"]
            if det_status == "detected":
                aoe_radius = 600.0
            else:  # approximate
                aoe_radius = 1_800.0

            cx, cy = target_entry["known_x"], target_entry["known_y"]

            # 지도 표시용 사격 현황 기록 (사격 중 매 틱 갱신, 정지 후 잔류)
            self._spg_fire_state[spg.id] = {
                "shooter_id":  spg.id,
                "side":        spg.side,
                "unit_type":   spg.unit_type,
                "shooter_x":   round(spg.x, 1),
                "shooter_y":   round(spg.y, 1),
                "target_x":    round(cx, 1),
                "target_y":    round(cy, 1),
                "radius":      aoe_radius,
                "game_time":   self.game_time,
            }

            # 대포병 탐지: 사격하는 순간 자주포 위치가 적 인텔에 노출된다
            # (기본 approximate, 확률적으로 detected). 사격을 멈추면 자연 감쇠로 lost.
            self._expose_firing_spg(spg, enemy_side, enemies)

            # AoE 내 적 피해 적용
            hit_any = False
            for enemy in enemies:
                dist = math.hypot(enemy.x - cx, enemy.y - cy)
                if dist > aoe_radius:
                    continue
                proximity = 1.0 - dist / aoe_radius
                cover     = terrain.cover_factor(enemy.x, enemy.y) * 0.4
                matchup   = _matchup_factor(spg.unit_type, enemy.unit_type)
                damage    = (
                    spg.effective_firepower() / 100.0
                    * BASE_ATTRITION_RATE
                    * proximity
                    * (1.0 - cover)
                    * matchup
                    * fp_mult
                    * spg_fire_degrade   # 포병 전투력 감소 → 화력지원 초선형 약화
                    * dt_h
                ) * random.uniform(0.6, 1.4)
                _cp_before_ind = enemy.combat_power
                enemy.combat_power = max(0.0, enemy.combat_power - damage)
                self._check_blufor_cp_threshold(enemy, _cp_before_ind)
                # 간접사격 피탄 시 공격자 측 인텔리전스에 위치 노출 반영
                ie = self._intelligence[spg.side].get(enemy.id)
                if ie is not None and ie["status"] != "detected":
                    ie.update({
                        "status": "detected", "known_x": enemy.x, "known_y": enemy.y,
                        "unit_type": enemy.unit_type, "combat_power": round(enemy.combat_power, 1),
                        "last_detected_tick": self.tick, "detected_by": spg.id,
                        "ticks_since_lost": 0, "ever_detected": True,
                    })
                if damage >= 3.0:
                    hit_any = True
                    self.db.log_event(
                        self.tick, self.game_time, "INDIRECT",
                        f"{spg.id}(자주포) 간접사격 → {enemy.id}: "
                        f"-{damage:.1f}% CP "
                        f"(AoE반경{aoe_radius:.0f}m, 정확도:{det_status})",
                    )
            if hit_any:
                pass  # 개별 로그로 충분

    def _expose_firing_spg(self, spg, enemy_side: str, enemies: list):
        """자주포가 사격하면 그 위치를 적(enemy_side) 인텔에 노출한다.

        기본은 음향표정 수준의 approximate(오차 반경 내), 확률적으로 대포병 레이더가
        정확 위치(detected)를 포착한다. 이미 detected면 approximate로 강등하지 않는다.
        사격을 멈추면 _update_intelligence의 ticks_since_lost 감쇠로 lost 처리된다.

        detected_by 는 실제로 탐지한 것으로 볼 가장 가까운 적 부대에 귀속한다
        (온톨로지 observes 엣지가 유효 부대 노드를 가리키도록).
        """
        cb = self._intelligence.get(enemy_side, {}).get(spg.id)
        if cb is None:
            return
        old_status = cb.get("status")

        # 사격 원점을 포착한 것으로 귀속할 가장 가까운 적 부대
        detector = None
        if enemies:
            detector = min(
                enemies, key=lambda u: math.hypot(u.x - spg.x, u.y - spg.y)
            )
        detected_by = detector.id if detector is not None else "대포병탐지"

        if random.random() < _COUNTER_BATTERY_DETECT_PROB:
            # 대포병 레이더 명중 — 정확 위치
            cb.update({
                "status": "detected",
                "known_x": spg.x, "known_y": spg.y,
                "unit_type": spg.unit_type,
                "combat_power": round(spg.combat_power, 1),
                "last_detected_tick": self.tick, "detected_by": detected_by,
                "ticks_since_lost": 0, "ever_detected": True,
            })
        elif old_status != "detected":
            # 음향표정 수준 — 개략 위치(원형 분포 오차). 이미 detected면 유지.
            ang = random.uniform(0.0, 2.0 * math.pi)
            rad = _COUNTER_BATTERY_APPROX_RADIUS * math.sqrt(random.random())
            cb.update({
                "status": "approximate",
                "known_x": max(0.0, min(29_999.0, spg.x + math.cos(ang) * rad)),
                "known_y": max(0.0, min(29_999.0, spg.y + math.sin(ang) * rad)),
                "unit_type": spg.unit_type,   # 사격 원점 = 포병으로 식별됨
                "last_detected_tick": self.tick, "detected_by": detected_by,
                "ticks_since_lost": 0, "ever_detected": True,
            })

        # 새로 정확 포착(detected)된 순간만 로그 (사격 지속 시 스팸 방지)
        if cb["status"] == "detected" and old_status != "detected":
            self.db.log_event(
                self.tick, self.game_time, "DETECTION",
                f"[대포병탐지] {enemy_side}가 {spg.id}(자주포) 사격 원점 포착 "
                f"({spg.x/1000:.1f}km,{spg.y/1000:.1f}km)",
            )

    # ── 공중지원 ─────────────────────────────────────────────────────

    def _resolve_air_support(self, dt: float):
        dt_h = dt / 3600.0
        # 공중지원 요청 side의 반대편 부대가 피격 대상
        side_targets = {
            "BLUFOR": [u for u in self.units if u.side == "OPFOR" and u.is_active()],
            "OPFOR":  [u for u in self.units if u.side == "BLUFOR" and u.is_active()],
        }

        for air in self.air_supports:
            if air.status == "completed":
                continue

            if air.status == "pending":
                air.elapsed += dt
                if air.elapsed < air.delay:
                    continue
                # Activated this tick — carry over excess time into active phase
                eff_dt = air.elapsed - air.delay
                air.elapsed = eff_dt
                air.status = "active"
                self.db.log_event(
                    self.tick, self.game_time, "AIR_ACTIVE",
                    f"[{air.side}] {air.call_sign} ({air.support_type}) 투입 — "
                    f"목표({air.target_x/1000:.1f}km,{air.target_y/1000:.1f}km)",
                )
                # Fall through to damage calculation (no continue)
            else:
                air.elapsed += dt
                eff_dt = dt

            # Cap effective damage window to remaining active duration
            elapsed_before = air.elapsed - eff_dt
            remaining = max(0.0, air.duration - elapsed_before)
            eff_dt = min(eff_dt, remaining)
            eff_dt_h = eff_dt / 3600.0

            targets = side_targets.get(air.side, [])
            blufor_hit_this_tick = False
            for u in targets:
                dist = math.hypot(u.x - air.target_x, u.y - air.target_y)
                if dist > air.radius:
                    continue
                proximity  = 1.0 - dist / air.radius
                cover      = terrain.cover_factor(u.x, u.y) * 0.5
                raw_damage = (
                    air.damage_rate * proximity * (1.0 - cover) * eff_dt_h
                ) * random.uniform(0.7, 1.3)
                min_damage = 30.0 * proximity * (1.0 - cover * 0.5) * (eff_dt / air.duration)
                damage = max(raw_damage, min_damage)
                _cp_before_air = u.combat_power
                u.combat_power = max(0.0, u.combat_power - damage)
                # CP 임계값 트리거 (BLUFOR 피격 시)
                self._check_blufor_cp_threshold(u, _cp_before_air)
                # OPFOR 공중지원에 BLUFOR 피격 → 별도 재계획 트리거 (1회)
                if air.side == "OPFOR" and u.side == "BLUFOR" and damage >= 5.0:
                    blufor_hit_this_tick = True
                if damage >= 3.0:
                    self.db.log_event(
                        self.tick, self.game_time, "AIR_STRIKE",
                        f"[{air.side}] {air.call_sign}→{u.id}: -{damage:.1f}% CP "
                        f"(거리{dist/1000:.1f}km)",
                    )
            # OPFOR 공중지원 피격 → 재계획 콜백 (공중지원 1회당 1번만)
            if blufor_hit_this_tick and not air.hit_reported:
                air.hit_reported = True
                if self.on_blufor_air_hit is not None:
                    # 피격 부대 중 가장 많이 피해 입은 BLUFOR 유닛 대표로 전달
                    hit_units = [
                        u for u in side_targets.get("OPFOR", [])   # OPFOR 공중지원 → BLUFOR 피격
                        if math.hypot(u.x - air.target_x, u.y - air.target_y) <= air.radius
                    ]
                    if hit_units:
                        rep = min(hit_units, key=lambda u: u.combat_power)
                        try:
                            self.on_blufor_air_hit(
                                rep.id, rep.unit_type,
                                air.call_sign, round(rep.combat_power, 1)
                            )
                        except Exception as _cb_err:
                            logger.error(f"on_blufor_air_hit 콜백 오류: {_cb_err}")

            if air.elapsed >= air.duration:
                air.status = "completed"
                self.db.log_event(
                    self.tick, self.game_time, "AIR_COMPLETE",
                    f"[{air.side}] {air.call_sign} ({air.support_type}) 임무 완료",
                )

    # ── 양측 룰 기반 AI ──────────────────────────────────────────────

    _OPFOR_AI_INTERVAL = 60.0

    def _update_opfor_ai(self, dt: float):
        self._opfor_ai_last += dt
        if self._opfor_ai_last < self._OPFOR_AI_INTERVAL:
            return
        self._opfor_ai_last = 0.0
        self._run_opfor_strategy_ai()    # 정찰→임무결정→방어/공격 상태 머신
        self._run_faction_ai("BLUFOR")   # BLUFOR AI 는 그대로 유지
        self.opfor_ai_fire_count += 1

    # ── OPFOR 전략 AI (상태 머신) ─────────────────────────────────────

    def _run_opfor_strategy_ai(self):
        """
        OPFOR 전략 AI 메인 루프.

        단계:
          1. 정찰(recon)  : 정찰부대가 BLUFOR 방향으로 기동, 나머지 경계
          2. 결정         : 탐지 BLUFOR ≥ 임계값 → 랜덤으로 방어/공격 선택
          3. 방어(defend) : 고지대에 부대 분산 배치
          4. 공격(attack) : 탐지된 BLUFOR 위치로 각 부대 기동·교전
        """
        opfor_all = [u for u in self.units if u.side == "OPFOR" and u.is_active()]
        if not opfor_all:
            return

        opfor_intel  = self._intelligence["OPFOR"]
        detected_blu = [e for e in opfor_intel.values() if e["status"] == "detected"]

        # ── OPFOR 공중지원(항공 자산 기반 CAS)은 비활성 ──────────────
        # 헬기 등 항공 자산을 통한 CAS는 아군(BLUFOR) 전용이다. OPFOR는 항공 CAS를
        # 요청하지 않으며, 화력지원은 온맵 자주포(Red5)의 간접사격으로만 수행한다.

        # ① 제압·저하 부대 우선 처리
        for u in opfor_all:
            if u.status == "suppressed":
                u.waypoints      = []
                u.current_action = "defend"
                continue
            if u.combat_power < SUPPRESSED_THRESHOLD:
                self._ai_withdraw(u, "OPFOR", "OPFOR_AI")

        active_opfor = [
            u for u in opfor_all
            if u.status not in ("suppressed", "destroyed")
            and u.combat_power >= SUPPRESSED_THRESHOLD
        ]
        if not active_opfor:
            return

        # ② 정찰 단계 → 임무 결정
        if not self._opfor_strategy_decided:
            if len(detected_blu) >= _OPFOR_DETECT_THRESHOLD:
                self._opfor_strategy         = random.choice(["defend", "attack"])
                self._opfor_strategy_decided = True
                ko = "지역 방어" if self._opfor_strategy == "defend" else "아군 공격"
                self.db.log_event(
                    self.tick, self.game_time, "OPFOR_AI",
                    f"OPFOR 임무 결정: {ko} "
                    f"(탐지된 BLUFOR {len(detected_blu)}개)"
                )
                self._execute_opfor_strategy(active_opfor, detected_blu)
            else:
                self._opfor_recon_phase(active_opfor)
        else:
            # ③ 이미 결정된 임무 재실행 (주기적 갱신)
            self._execute_opfor_strategy(active_opfor, detected_blu)

    def _blufor_centroid(self) -> Tuple[float, float]:
        """생존 BLUFOR 부대의 중심 좌표. 없으면 배치 구역 추정 중심을 반환."""
        alive = [u for u in self.units if u.side == "BLUFOR" and u.is_active()]
        if not alive:
            return _BLUFOR_APPROX_CX, _BLUFOR_APPROX_CY
        return (
            sum(u.x for u in alive) / len(alive),
            sum(u.y for u in alive) / len(alive),
        )

    # ── 은밀 기동(발각 회피) 경로 생성 ────────────────────────────────
    # LLM 임무계획의 목표 waypoint는 유지하되, 각 구간을 적 정찰 발각
    # 위험이 낮은 우회 경로로 확장한다. 위험은 엔진 탐지 모델과 동일한
    # 요소(적과의 거리·LOS 차폐·지형 엄폐)로 평가한다.

    def _opfor_threat_sources(self, observer: str = "BLUFOR") -> List[Tuple[float, float, float]]:
        """
        아군(observer)이 인지한 적 위치를 발각 위협원으로 반환.

        각 원소 = (x, y, 탐지반경). 적 정찰부대는 탐지반경이 커서
        자동으로 넓게 회피된다. detected/approximate 인텔만 사용
        (아직 탐지 못한 적은 정확히 회피 불가 — 지형 엄폐로 보완).
        """
        threats: List[Tuple[float, float, float]] = []
        for e in self._intelligence.get(observer, {}).values():
            if e.get("status") not in ("detected", "approximate"):
                continue
            rng = _DETECT_RANGE.get(e.get("unit_type", ""), 3_000.0)
            threats.append((e["known_x"], e["known_y"], rng))
        return threats

    def _point_detection_risk(
        self, px: float, py: float,
        threats: List[Tuple[float, float, float]],
    ) -> float:
        """지점 (px,py)의 발각 위험 점수 (낮을수록 은밀)."""
        try:
            cover = terrain.cover_factor(px, py)
        except Exception:
            cover = 0.0
        # 미지의 관측자 대비 기본 노출 위험 (엄폐 부족할수록 높음)
        risk = (1.0 - cover) * _STEALTH_EXPOSURE_W
        for tx, ty, rng in threats:
            d = math.hypot(px - tx, py - ty)
            if d >= rng:
                continue
            prox = 1.0 - d / rng               # 가까울수록 1에 근접
            los  = _los_quality(tx, ty, px, py)  # 시선 개방 시 발각 쉬움
            risk += prox * (0.3 + 0.7 * los)     # 차폐돼도 완전 0은 아님
        return risk

    def _segment_risk(
        self, ax: float, ay: float, bx: float, by: float,
        threats: List[Tuple[float, float, float]],
    ) -> float:
        """구간 A→B를 따라 위험을 적분(샘플 합)한 값. 경로가 길수록 누적된다."""
        seg = math.hypot(bx - ax, by - ay)
        n = max(1, int(seg / _STEALTH_STEP_M))
        total = 0.0
        for i in range(1, n + 1):
            t = i / n
            sx = ax + (bx - ax) * t
            sy = ay + (by - ay) * t
            total += self._point_detection_risk(sx, sy, threats) * (seg / n)
        return total

    def _stealth_leg(
        self, ax: float, ay: float, bx: float, by: float,
        threats: List[Tuple[float, float, float]], depth: int = 0,
    ) -> List[List[float]]:
        """
        구간 A→B를 발각 위험이 낮은 우회 경로로 확장.
        반환: A를 제외하고 B로 끝나는 중간·도착 waypoint 리스트.
        """
        straight = self._segment_risk(ax, ay, bx, by, threats)
        seg_len = math.hypot(bx - ax, by - ay)
        if depth >= _STEALTH_MAX_DEPTH or seg_len < _STEALTH_MIN_LEG_M:
            return [[bx, by]]

        # 구간에 수직인 단위벡터 (측방 우회 방향)
        ux, uy = (bx - ax) / seg_len, (by - ay) / seg_len
        perp_x, perp_y = -uy, ux
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0

        best_cost = straight
        best_point = None
        for off in _STEALTH_OFFSETS_M:
            for sign in (1.0, -1.0):
                cx = max(0.0, min(29_999.0, mx + perp_x * off * sign))
                cy = max(0.0, min(29_999.0, my + perp_y * off * sign))
                cost = (self._segment_risk(ax, ay, cx, cy, threats)
                        + self._segment_risk(cx, cy, bx, by, threats))
                if cost < best_cost:
                    best_cost = cost
                    best_point = (cx, cy)

        # 우회가 직선 대비 충분히 개선될 때만 채택 (사소한 우회는 무시)
        if best_point is None or best_cost >= straight * _STEALTH_IMPROVE_RATIO:
            return [[bx, by]]

        cx, cy = best_point
        left  = self._stealth_leg(ax, ay, cx, cy, threats, depth + 1)
        right = self._stealth_leg(cx, cy, bx, by, threats, depth + 1)
        return left + right

    def _stealth_expand_waypoints(
        self, unit: "Unit", raw_wps: List[List[float]],
    ) -> List[List[float]]:
        """
        LLM이 준 목표 waypoint 리스트를 은밀 기동 경로로 확장.
        현위치→첫 WP, 그리고 각 WP 사이 구간을 발각 회피 경로로 치환한다.
        목표 지점 자체(원본 WP)는 항상 경로에 유지된다.
        """
        try:
            threats = self._opfor_threat_sources(observer=unit.side)
            if not threats:
                return raw_wps  # 인지된 적 없음 → 원본 유지
            expanded: List[List[float]] = []
            cx, cy = unit.x, unit.y
            for wp in raw_wps:
                gx, gy = float(wp[0]), float(wp[1])
                leg = self._stealth_leg(cx, cy, gx, gy, threats)
                expanded.extend(leg)
                cx, cy = gx, gy
            return expanded if expanded else raw_wps
        except Exception as e:
            logger.warning(f"은밀 경로 확장 실패 (원본 경로 사용): {e}")
            return raw_wps

    def _opfor_recon_phase(self, active_opfor: list):
        """
        정찰 단계.
        - 정찰부대(max_speed ≥ 4.0): BLUFOR 방향으로 전진
        - 자주포: 간접사격 진지 유지
        - 그 외: 현위치 경계

        기동 목표는 생존 BLUFOR 중심 방향으로 잡되, 남은 거리 이상으로
        전진하지 않도록 제한하여 목표점 주변 왕복(오버슈트)을 방지한다.
        """
        blu_cx, blu_cy = self._blufor_centroid()

        for u in active_opfor:
            if u.unit_type == "자주포":
                self._ai_standoff(u, blu_cx, blu_cy, "OPFOR_AI")
                continue

            if u.max_speed >= 4.0:  # 정찰부대
                # 기존 경로 완주 여부 확인 후 새 경유지 부여
                if u.waypoints and u.current_action == "recon":
                    continue  # 이미 기동 중
                dist_to_blu = math.hypot(blu_cx - u.x, blu_cy - u.y)
                angle  = math.atan2(blu_cy - u.y, blu_cx - u.x)
                # BLUFOR 방향으로 전진 — 남은 거리(2km 여유)를 넘지 않도록 제한
                adv    = min(random.uniform(6_000, 10_000),
                             max(dist_to_blu - 2_000, 2_000))
                rx     = max(0, min(29_999, u.x + math.cos(angle) * adv))
                ry     = max(0, min(29_999, u.y + math.sin(angle) * adv))
                # 측방 변화를 주어 지그재그 정찰 (전진 거리에 비례해 축소)
                perp   = angle + random.choice([-1, 1]) * math.pi / 4
                zig    = min(2_000, adv * 0.25)
                rx    += math.cos(perp) * random.uniform(0, zig)
                ry    += math.sin(perp) * random.uniform(0, zig)
                rx, ry = max(0, min(29_999, rx)), max(0, min(29_999, ry))
                u.waypoints      = [[rx, ry]]
                u.current_action = "recon"
                self.db.log_event(
                    self.tick, self.game_time, "OPFOR_AI",
                    f"{u.id}(정찰) BLUFOR 방향 정찰 기동 → "
                    f"({rx/1000:.1f}km, {ry/1000:.1f}km)"
                )
            else:
                # 비정찰부대: 현위치 경계
                if not u.waypoints:
                    u.current_action = "hold"

    def _execute_opfor_strategy(self, active_opfor: list, detected_blu: list):
        """결정된 전략(방어/공격) 실행."""
        if self._opfor_strategy == "defend":
            self._opfor_defend_strategy(active_opfor)
        else:
            self._opfor_attack_strategy(active_opfor, detected_blu)

    def _find_opfor_defensive_positions(self, n: int) -> List[Tuple[float, float]]:
        """
        OPFOR 영역(북동부, x/y ≥ 14 km)에서 고지·엄폐 우수 방어 위치 n개 선정.
        각 위치는 _OPFOR_DEFEND_MIN_SEP 이상 이격.
        """
        candidates: List[Tuple[float, float, float]] = []  # (score, x, y)
        for xi in range(14, 29, 2):
            for yi in range(14, 29, 2):
                x = float(xi * 1_000)
                y = float(yi * 1_000)
                elev  = terrain.elevation(x, y)
                cover = terrain.cover_factor(x, y)
                # 고도 + 엄폐 가중합으로 방어 점수 산출
                score = elev + cover * 150.0
                candidates.append((score, x, y))

        candidates.sort(reverse=True)
        selected: List[Tuple[float, float, float]] = []
        for score, x, y in candidates:
            if all(
                math.hypot(x - sx, y - sy) >= _OPFOR_DEFEND_MIN_SEP
                for _, sx, sy in selected
            ):
                selected.append((score, x, y))
                if len(selected) >= n:
                    break
        return [(x, y) for _, x, y in selected]

    def _opfor_defend_strategy(self, active_opfor: list):
        """
        지역 방어: 고지대에 부대를 분산 배치.
        자주포는 후방 간접사격 진지, 나머지는 고지 방어.
        최초 실행 시 위치 할당 → 이후에는 재할당 없이 목표 유지.
        """
        non_spg = [u for u in active_opfor if u.unit_type != "자주포"]
        spg     = [u for u in active_opfor if u.unit_type == "자주포"]

        # 자주포: 후방 간접사격 진지 유지 (실제 BLUFOR 중심 방향 기준)
        blu_cx, blu_cy = self._blufor_centroid()
        for u in spg:
            self._ai_standoff(u, blu_cx, blu_cy, "OPFOR_AI")

        # 방어 위치 최초 할당
        if not self._opfor_defend_positions:
            positions = self._find_opfor_defensive_positions(len(non_spg))
            for i, u in enumerate(non_spg):
                pos = positions[i] if i < len(positions) else (u.x, u.y)
                self._opfor_defend_positions[u.id] = pos

        for u in non_spg:
            tx, ty = self._opfor_defend_positions.get(u.id, (u.x, u.y))
            dist   = math.hypot(u.x - tx, u.y - ty)
            if dist < 300:
                u.waypoints      = []
                u.current_action = "defend"
            else:
                u.waypoints      = [[tx, ty]]
                u.current_action = "defend"
                self.db.log_event(
                    self.tick, self.game_time, "OPFOR_AI",
                    f"{u.id} 방어 진지 이동 → "
                    f"({tx/1000:.1f}km, {ty/1000:.1f}km) "
                    f"고도 {terrain.elevation(tx, ty):.0f}m"
                )

    def _opfor_attack_strategy(self, active_opfor: list, detected_blu: list):
        """
        아군 공격: 탐지된 BLUFOR 위치로 각 부대 기동·교전.
        탐지 정보가 없으면 정찰 단계로 회귀.
        """
        if not detected_blu:
            self.db.log_event(
                self.tick, self.game_time, "OPFOR_AI",
                "공격 임무: 탐지된 BLUFOR 없음 → 정찰 재개"
            )
            self._opfor_recon_phase(active_opfor)
            return

        blu_cx = sum(e["known_x"] for e in detected_blu) / len(detected_blu)
        blu_cy = sum(e["known_y"] for e in detected_blu) / len(detected_blu)

        for u in active_opfor:
            if u.unit_type == "자주포":
                self._ai_standoff(u, blu_cx, blu_cy, "OPFOR_AI")
                continue

            # 가장 가까운 탐지 목표 선정
            target = min(
                detected_blu,
                key=lambda e: math.hypot(u.x - e["known_x"], u.y - e["known_y"])
            )
            tx, ty   = target["known_x"], target["known_y"]
            d_range  = _DIRECT_RANGE.get(u.unit_type, 1_500.0)
            dist     = math.hypot(u.x - tx, u.y - ty)

            if dist <= d_range * 0.6:
                # 교전 거리 이내: 현위치 공격
                u.waypoints      = []
                u.current_action = "attack"
            else:
                # 접근 기동: 교전 유효거리 내로 진입
                angle  = math.atan2(ty - u.y, tx - u.x)
                wp_x   = max(0, min(29_999, tx - math.cos(angle) * d_range * 0.5))
                wp_y   = max(0, min(29_999, ty - math.sin(angle) * d_range * 0.5))
                u.waypoints      = [[wp_x, wp_y]]
                u.current_action = "attack"
                self.db.log_event(
                    self.tick, self.game_time, "OPFOR_AI",
                    f"{u.id}({u.unit_type}) → BLUFOR {target['unit_id']} 공격 기동 "
                    f"({tx/1000:.1f}km, {ty/1000:.1f}km) 거리 {dist/1000:.1f}km"
                )

    def _run_faction_ai(self, side: str):
        enemy_side = "OPFOR" if side == "BLUFOR" else "BLUFOR"
        enemies    = [u for u in self.units if u.side == enemy_side and u.is_active()]
        if not enemies:
            return

        en_cx  = sum(u.x for u in enemies) / len(enemies)
        en_cy  = sum(u.y for u in enemies) / len(enemies)
        log_type = f"{side}_AI"

        for u in self.units:
            if u.side != side or not u.is_active():
                continue

            # 제압 상태: 이동 금지, 방어 전환 (임무 잠금 즉시 해제)
            if u.status == "suppressed":
                u.waypoints          = []
                u.current_action     = "defend"
                u.mission_lock_ticks = 0
                if side == "BLUFOR":
                    self._blufor_llm_units.discard(u.id)
                continue

            nearest = min(enemies, key=lambda e: u.distance_to(e))
            dist    = u.distance_to(nearest)

            # BLUFOR AI 개입 제한 (LLM 임무계획 우선)
            if side == "BLUFOR":
                # 신규 임무 발령 후 30틱 이내: AI 개입 차단
                if u.mission_lock_ticks > 0:
                    continue
                # LLM 임무 수행 중(웨이포인트 남음) 또는 표적 추격 중: AI 개입 차단
                if u.id in self._blufor_llm_units and (u.waypoints or u.pursuing):
                    continue
                # LLM 미지정 부대 + CP 충분 → 자율 행동 유지, AI 불필요
                if u.id not in self._blufor_llm_units and u.combat_power >= 30:
                    continue

            if u.combat_power < SUPPRESSED_THRESHOLD:
                self._ai_withdraw(u, side, log_type)
            elif u.max_speed >= 4.0:
                self._ai_recon(u, nearest, en_cx, en_cy, log_type)
            elif u.unit_type == "자주포":
                self._ai_standoff(u, en_cx, en_cy, log_type)
            elif u.firepower_index >= 140:
                self._ai_armor(u, nearest, dist, log_type)
            else:
                self._ai_infantry(u, nearest, dist, en_cx, en_cy, side, log_type)

    def _ai_withdraw(self, u: Unit, side: str, log_type: str):
        if side == "OPFOR":
            rally_x = max(u.x, 22_000.0)
            rally_y = max(u.y, 20_000.0)
        else:
            rally_x = min(u.x, 9_000.0)
            rally_y = min(u.y, 8_000.0)
        u.waypoints      = [[rally_x, rally_y]]
        u.current_action = "withdraw"
        self.db.log_event(self.tick, self.game_time, log_type,
                          f"{u.id} 전투력저하({u.combat_power:.0f}%) → 후퇴")

    def _ai_recon(self, u: Unit, nearest: Unit,
                  en_cx: float, en_cy: float, log_type: str):
        dist = u.distance_to(nearest)
        d_range = _DIRECT_RANGE.get(u.unit_type, 2_000.0)
        if dist <= d_range:
            angle   = math.atan2(u.y - nearest.y, u.x - nearest.x) + math.pi / 2
            flank_x = max(0, min(29_999, u.x + math.cos(angle) * 3_000))
            flank_y = max(0, min(29_999, u.y + math.sin(angle) * 3_000))
            u.waypoints = [
                [flank_x, flank_y],
                [max(0, min(29_999, nearest.x + math.cos(angle) * 1_500)),
                 max(0, min(29_999, nearest.y + math.sin(angle) * 1_500))],
            ]
            u.current_action = "flank"
        else:
            angle     = math.atan2(en_cy - u.y, en_cx - u.x)
            side_angle = angle + math.pi / 2
            u.waypoints = [[
                max(0, min(29_999, en_cx + math.cos(side_angle) * 4_000)),
                max(0, min(29_999, en_cy + math.sin(side_angle) * 4_000)),
            ]]
            u.current_action = "flank"
        self.db.log_event(self.tick, self.game_time, log_type,
                          f"{u.id}(정찰) → 측방 우회 기동")

    def _ai_standoff(self, u: Unit, en_cx: float, en_cy: float, log_type: str):
        dist_to_en = math.hypot(u.x - en_cx, u.y - en_cy)
        ideal_dist = _INDIRECT_MAX_RANGE * 0.6
        if abs(dist_to_en - ideal_dist) < 500:
            u.waypoints      = []
            u.current_action = "hold"
        else:
            angle = math.atan2(u.y - en_cy, u.x - en_cx)
            u.waypoints = [[
                max(0, min(29_999, en_cx + math.cos(angle) * ideal_dist)),
                max(0, min(29_999, en_cy + math.sin(angle) * ideal_dist)),
            ]]
            u.current_action = "move"
        self.db.log_event(self.tick, self.game_time, log_type,
                          f"{u.id}(자주포) → 간접사격 진지 유지 (거리{dist_to_en/1000:.1f}km)")

    def _ai_armor(self, u: Unit, nearest: Unit, dist: float, log_type: str):
        d_range = _DIRECT_RANGE.get(u.unit_type, 3_000.0)
        if dist <= d_range * 0.5:
            u.waypoints      = []
            u.current_action = "attack"
        else:
            angle    = math.atan2(nearest.y - u.y, nearest.x - u.x)
            target_x = nearest.x - math.cos(angle) * d_range * 0.4
            target_y = nearest.y - math.sin(angle) * d_range * 0.4
            mid_x    = (u.x + target_x) / 2
            mid_y    = (u.y + target_y) / 2
            u.waypoints = [
                [max(0, min(29_999, mid_x)),    max(0, min(29_999, mid_y))],
                [max(0, min(29_999, target_x)), max(0, min(29_999, target_y))],
            ]
            u.current_action = "attack"
        self.db.log_event(self.tick, self.game_time, log_type,
                          f"{u.id}(전차) → {nearest.id} 공격 기동 (거리{dist/1000:.1f}km)")

    def _ai_infantry(self, u: Unit, nearest: Unit, dist: float,
                     en_cx: float, en_cy: float, side: str, log_type: str):
        d_range     = _DIRECT_RANGE.get(u.unit_type, 1_500.0)
        faction_ids = [v.id for v in self.units if v.side == side]
        idx         = faction_ids.index(u.id) if u.id in faction_ids else 0
        side_sign   = 1 if idx % 2 == 0 else -1
        perp_x      = -(en_cy - u.y)
        perp_y      = en_cx - u.x
        perp_len    = math.hypot(perp_x, perp_y) or 1
        offset      = 800 * side_sign

        if dist <= d_range:
            u.waypoints      = []
            u.current_action = "attack"
        else:
            angle    = math.atan2(en_cy - u.y, en_cx - u.x)
            target_x = en_cx - math.cos(angle) * d_range * 0.8 + perp_x / perp_len * offset
            target_y = en_cy - math.sin(angle) * d_range * 0.8 + perp_y / perp_len * offset
            u.waypoints = [[
                max(0, min(29_999, target_x)),
                max(0, min(29_999, target_y)),
            ]]
            u.current_action = "attack"
        self.db.log_event(self.tick, self.game_time, log_type,
                          f"{u.id}(보병) → 정면 압박 (거리{dist/1000:.1f}km)")

    # ── 상태 갱신 (4단계) ────────────────────────────────────────────

    def _update_status(self, dt: float):
        for u in self.units:
            cp = u.combat_power

            if cp <= DESTROYED_THRESHOLD:
                if u.status != "destroyed":
                    u.status    = "destroyed"
                    u.waypoints = []
                    self._suppress_recover_at.pop(u.id, None)
                    self.db.log_event(
                        self.tick, self.game_time, "DESTROYED",
                        f"{u.id}({u.unit_type}) 전투불능 (CP {cp:.0f}%)",
                    )

            elif cp <= SUPPRESSED_THRESHOLD:
                if u.status not in ("suppressed", "destroyed"):
                    u.status = "suppressed"
                    # 회복 가능 시각: 교전 밖으로 빠진 후 _SUPPRESS_RECOVER_SEC 경과
                    self._suppress_recover_at[u.id] = self.game_time + _SUPPRESS_RECOVER_SEC
                    self.db.log_event(
                        self.tick, self.game_time, "SUPPRESSED",
                        f"{u.id}({u.unit_type}) 제압됨 (CP {cp:.0f}%)",
                    )

            elif cp <= DEGRADED_THRESHOLD:
                if u.status == "active":
                    u.status = "degraded"
                    self.db.log_event(
                        self.tick, self.game_time, "DEGRADED",
                        f"{u.id}({u.unit_type}) 전투력 저하 (CP {cp:.0f}%)",
                    )
                elif u.status == "suppressed":
                    # 제압 회복 조건: 지정 시각 도달
                    recover_at = self._suppress_recover_at.get(u.id, float("inf"))
                    if self.game_time >= recover_at:
                        u.status = "degraded"
                        self._suppress_recover_at.pop(u.id, None)
                        self.db.log_event(
                            self.tick, self.game_time, "RECOVERED",
                            f"{u.id}({u.unit_type}) 제압 해제 → 저하 상태 (CP {cp:.0f}%)",
                        )

            else:  # cp > DEGRADED_THRESHOLD
                if u.status == "suppressed":
                    recover_at = self._suppress_recover_at.get(u.id, float("inf"))
                    if self.game_time >= recover_at:
                        u.status = "active"
                        self._suppress_recover_at.pop(u.id, None)
                        self.db.log_event(
                            self.tick, self.game_time, "RECOVERED",
                            f"{u.id}({u.unit_type}) 제압 해제 → 정상 복귀 (CP {cp:.0f}%)",
                        )
                elif u.status == "degraded":
                    u.status = "active"
                    self.db.log_event(
                        self.tick, self.game_time, "RECOVERED",
                        f"{u.id}({u.unit_type}) 전투력 회복 (CP {cp:.0f}%)",
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
