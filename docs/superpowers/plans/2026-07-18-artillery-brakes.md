# 포병 과보상 완화 — 4개 브레이크 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 무제한·전지적·정적 포병 스팸 지배전략을 제거한다 — 탄약 예산, shoot-and-scoot 대포병 압박, 간접포 격멸 상한(CP15%), 통제구역 3곳 다수유지 승리조건.

**Architecture:** 브레이크 ①②③은 `engine.py`의 `_resolve_indirect_fire` 국소 변경. ④는 도메인 값객체 `ControlPoint` + 엔진 추적/승리 + `get_state` + 웹 API lat/lon 변환 + 대시보드 마커. 모든 변경이 900틱 결정성 골든을 바꾸므로 마지막에 재생성한다.

**Tech Stack:** Python 3.9+, 표준 라이브러리, pytest, HTML/Leaflet(대시보드).

## Global Constraints

- 계층 규칙 유지: import-linter 3 kept/0 broken (`PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`). domain은 표준 라이브러리만; engine(application)은 domain import 허용; web/dashboard(presentation).
- 테스트: `PYTHONPATH=src python3 -m pytest <경로> -v` (시스템 python3=3.9.6; bare `python` 없음).
- 상수값: `_SPG_FIRE_BUDGET=300.0`, `_SPG_RESUPPLY_COOLDOWN=240.0`, `_CB_EXPOSURE_DELAY=120.0`, `_CB_DAMAGE_RATE=80.0`, `_CB_RAMP=180.0`, `_CB_MOVE_RESET=300.0`, `_INDIRECT_CP_FLOOR=15.0`, `_CP_CAPTURE_RADIUS=2000.0`, `_CP_HOLD_TO_WIN=300.0`.
- 통제구역 3곳: 통제-알파(12000,14000), 통제-브라보(15000,15000), 통제-찰리(14000,12000).
- 브레이크 ③(격멸상한)은 간접포(`_resolve_indirect_fire`의 적·아군 오사 피해)에만; 공중지원 미적용.
- 결정성 골든은 Task 6에서 재생성. Task 1~5 동안 `test_engine_determinism.py::test_engine_snapshot_is_stable` FAIL은 예상(무시), 단 `test_engine_is_deterministic_under_fixed_seed`(a==b)는 항상 통과해야 함.

## File Structure

- `engine.py` — 브레이크 ①②③ (Task 1-3), 통제구역 추적/승리/state (Task 4)
- `control_point.py`(신규 domain) — `ControlPoint` + `default_control_points()` (Task 4)
- `api.py`(web) + `ui/dashboard/index.html` — 통제구역 렌더 (Task 5)
- `test_engine_determinism.py` + `engine_900tick_seed42.json` — 골든 (Task 6)

---

### Task 1: 탄약 — 지속사격 예산 + 재보급 쿨다운

**Files:**
- Modify: `src/c2/application/simulation/engine.py`
- Test: `tests/application/test_spg_ammo.py` (신규)

**Interfaces:**
- Produces: 엔진 상태 `_spg_ammo`/`_spg_resupply_until`, 상수 `_SPG_FIRE_BUDGET`/`_SPG_RESUPPLY_COOLDOWN`; `_resolve_indirect_fire`가 쿨다운 중 사격 스킵·소진 시 재보급 진입.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_spg_ammo.py`:

```python
"""자주포 탄약: 지속사격 예산 소진 → 재보급 동안 사격 정지."""
import random, tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine, _SPG_FIRE_BUDGET
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, utype, x, y, **kw):
    return Unit(id=id, side=side, unit_type=utype, x=x, y=y, combat_power=100.0,
                firepower_index=130.0 if utype == "자주포" else 100.0,
                max_speed=4.0, status="active", waypoints=[], current_action="hold", **kw)


def test_spg_stops_firing_during_resupply():
    random.seed(3)
    spg   = _mk("자주포중대", "BLUFOR", "자주포", 8_000.0, 8_000.0, indirect_range=30_000.0)
    enemy = _mk("적보병1중대", "OPFOR", "기계화보병", 16_000.0, 16_000.0)
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "ammo.db")
    eng = WargameEngine([spg, enemy], db=db)
    eng.full_recon = True
    # 예산(_SPG_FIRE_BUDGET 게임초)을 넘겨 사격 → 재보급 진입 유도
    # dt = 0.5*60 = 30게임초/틱 → budget/30 틱 + 여유
    ticks_to_deplete = int(_SPG_FIRE_BUDGET / 30) + 2
    for _ in range(ticks_to_deplete):
        eng._tick()
    assert eng.game_time < eng._spg_resupply_until.get("자주포중대", 0.0), "재보급 대기에 진입해야 함"
    cp_at_resupply = enemy.combat_power
    # 재보급 동안(쿨다운 내) 적 CP가 더 안 깎여야 함 (사격 정지)
    for _ in range(3):
        eng._tick()
    assert enemy.combat_power == cp_at_resupply, "재보급 중에는 사격이 멈춰 적 피해가 없어야 함"
    events = db.get_recent_events(n=300)
    assert any(e["event_type"] == "AMMO_RESUPPLY" for e in events)
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_spg_ammo.py -v`
Expected: FAIL (`_spg_resupply_until` 미존재 AttributeError / import 실패).

- [ ] **Step 3: 상수 추가**

`engine.py`의 자주포 관련 상수 근처(`_SPG_CLOSE_RANGE` 아래)에 추가:

```python
_SPG_FIRE_BUDGET       = 300.0   # 자주포 연속사격 가능 게임초 (소진 시 재보급)
_SPG_RESUPPLY_COOLDOWN = 240.0   # 재보급 대기 게임초 (이 동안 사격 불가)
```

- [ ] **Step 4: 엔진 상태 필드 추가**

`__init__`의 `self._spg_fire_state: Dict[str, dict] = {}` 근처에 추가:

```python
        self._spg_ammo: Dict[str, float] = {}
        self._spg_resupply_until: Dict[str, float] = {}
```

`reset()`의 `self._spg_fire_state   = {}` 근처에 추가:

```python
            self._spg_ammo           = {}
            self._spg_resupply_until = {}
```

- [ ] **Step 5: `_resolve_indirect_fire`에 탄약 게이트 삽입**

(a) `for spg in spgs:` 루프 본문 맨 앞(`enemy_side = ...` 줄 **앞**)에 재보급 쿨다운 게이트:

```python
            # 재보급 쿨다운 중이면 이번 틱 사격 불가
            if self.game_time < self._spg_resupply_until.get(spg.id, 0.0):
                continue
```

(b) 표적 확정 후 `cx, cy = target_entry["known_x"], target_entry["known_y"]` 줄 **아래**에 탄약 소모:

```python
            # 탄약 소모 (사격 시) — 소진 시 재보급 진입(이번 틱 사격 스킵)
            self._spg_ammo[spg.id] = self._spg_ammo.get(spg.id, _SPG_FIRE_BUDGET) - dt
            if self._spg_ammo[spg.id] <= 0.0:
                self._spg_ammo[spg.id] = _SPG_FIRE_BUDGET
                self._spg_resupply_until[spg.id] = self.game_time + _SPG_RESUPPLY_COOLDOWN
                self.db.log_event(
                    self.tick, self.game_time, "AMMO_RESUPPLY",
                    f"{spg.id}(자주포) 탄약 소진 — 재보급 {_SPG_RESUPPLY_COOLDOWN:.0f}s",
                )
                continue
```

- [ ] **Step 6: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_spg_ammo.py -v`
Expected: PASS (1 passed).

- [ ] **Step 7: 결정성(a==b) 유지 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py::test_engine_is_deterministic_under_fixed_seed -v`
Expected: PASS.

- [ ] **Step 8: 커밋**

```bash
git add src/c2/application/simulation/engine.py tests/application/test_spg_ammo.py
git commit -m "feat(engine): 자주포 탄약 예산+재보급 쿨다운 (무제한 포격 차단)"
```

---

### Task 2: 대포병 — shoot-and-scoot 압박

**Files:**
- Modify: `src/c2/application/simulation/engine.py`
- Test: `tests/application/test_counter_battery.py` (신규)

**Interfaces:**
- Consumes: Task 1의 탄약 게이트(같은 SPG 사격 경로).
- Produces: 상수 `_CB_*`, 상태 `_spg_static_fire`/`_spg_last_pos`; 정적 사격 SPG에 대포병 피해 + `COUNTER_BATTERY` 로그.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_counter_battery.py`:

```python
"""대포병 shoot-and-scoot: 정적 사격 지속 시 자주포 자신이 피해."""
import random, tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine, _CB_EXPOSURE_DELAY
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, utype, x, y, **kw):
    return Unit(id=id, side=side, unit_type=utype, x=x, y=y, combat_power=100.0,
                firepower_index=130.0 if utype == "자주포" else 100.0,
                max_speed=4.0, status="active", waypoints=[], current_action="hold", **kw)


def test_static_firing_spg_takes_counter_battery_damage():
    random.seed(4)
    spg   = _mk("자주포중대", "BLUFOR", "자주포", 8_000.0, 8_000.0, indirect_range=30_000.0)
    enemy = _mk("적보병1중대", "OPFOR", "기계화보병", 16_000.0, 16_000.0)
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cb.db")
    eng = WargameEngine([spg, enemy], db=db)
    eng.full_recon = True
    # _CB_EXPOSURE_DELAY(게임초) 넘게 같은 자리서 사격 → 대포병 피해
    ticks = int(_CB_EXPOSURE_DELAY / 30) + 4
    for _ in range(ticks):
        eng._tick()
    assert spg.combat_power < 100.0, "정적 사격 자주포는 대포병 피해를 입어야 함"
    assert eng._spg_static_fire.get("자주포중대", 0.0) > _CB_EXPOSURE_DELAY
    events = db.get_recent_events(n=400)
    assert any(e["event_type"] == "COUNTER_BATTERY" for e in events)


def test_moving_resets_static_timer():
    random.seed(4)
    spg = _mk("자주포중대", "BLUFOR", "자주포", 8_000.0, 8_000.0, indirect_range=30_000.0)
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cb2.db")
    eng = WargameEngine([spg], db=db)
    eng.full_recon = True
    # 표적 없음 → 사격 안 함 → 정적 타이머 누적 안 됨
    for _ in range(6):
        eng._tick()
    assert eng._spg_static_fire.get("자주포중대", 0.0) == 0.0
    assert spg.combat_power == 100.0
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_counter_battery.py -v`
Expected: FAIL (`_CB_EXPOSURE_DELAY`/`_spg_static_fire` 미존재).

- [ ] **Step 3: 상수 추가**

`engine.py` Task 1 상수 아래에 추가:

```python
_CB_EXPOSURE_DELAY = 120.0   # 정적 사격 후 대포병 개시 게임초
_CB_DAMAGE_RATE    = 80.0    # 대포병 피해율 %/h (최대 램프)
_CB_RAMP           = 180.0   # 피해 램프 게임초
_CB_MOVE_RESET     = 300.0   # 이 거리 이상 이동 시 정적 타이머 리셋 (m)
```

- [ ] **Step 4: 엔진 상태 필드 추가**

`__init__`(Task 1 필드 근처)에 추가:

```python
        self._spg_static_fire: Dict[str, float] = {}
        self._spg_last_pos: Dict[str, tuple] = {}
```

`reset()`(Task 1 필드 근처)에 추가:

```python
            self._spg_static_fire = {}
            self._spg_last_pos    = {}
```

- [ ] **Step 5: `_resolve_indirect_fire`에 대포병 압박 삽입**

Task 1 (b)의 탄약 소모 블록 **아래**(즉 실제 사격이 확정된 뒤), 사격현황 기록(`self._spg_fire_state[spg.id] = {...}`) **앞**에 삽입:

```python
            # ── shoot-and-scoot 대포병 압박: 같은 자리서 계속 쏘면 피해 누적 ──
            _last = self._spg_last_pos.get(spg.id)
            if _last is not None and math.hypot(spg.x - _last[0], spg.y - _last[1]) > _CB_MOVE_RESET:
                self._spg_static_fire[spg.id] = 0.0
            self._spg_last_pos[spg.id] = (spg.x, spg.y)
            self._spg_static_fire[spg.id] = self._spg_static_fire.get(spg.id, 0.0) + dt
            _cb_over = self._spg_static_fire[spg.id] - _CB_EXPOSURE_DELAY
            if _cb_over > 0:
                _cb_ramp = min(1.0, _cb_over / _CB_RAMP)
                _cb_dmg  = _CB_DAMAGE_RATE * _cb_ramp * (dt / 3600.0)
                if _cb_dmg > 0:
                    _cb_before = spg.combat_power
                    spg.combat_power = max(0.0, spg.combat_power - _cb_dmg)
                    self._check_blufor_cp_threshold(spg, _cb_before)
                    _cb_key = ("CB", spg.id)
                    _cb_acc = self._indirect_accum.get(_cb_key, 0.0) + _cb_dmg
                    if _cb_acc >= _INDIRECT_LOG_THRESHOLD:
                        self._indirect_accum[_cb_key] = 0.0
                        self.db.log_event(
                            self.tick, self.game_time, "COUNTER_BATTERY",
                            f"{spg.id}(자주포) 대포병 피해 -{_cb_acc:.1f}% CP 누적 "
                            f"(정적사격 {self._spg_static_fire[spg.id]:.0f}s)",
                        )
                    else:
                        self._indirect_accum[_cb_key] = _cb_acc
```

- [ ] **Step 6: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_counter_battery.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: 결정성(a==b) 확인 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py::test_engine_is_deterministic_under_fixed_seed -v`
Expected: PASS.

```bash
git add src/c2/application/simulation/engine.py tests/application/test_counter_battery.py
git commit -m "feat(engine): shoot-and-scoot 대포병 압박 (정적 포격 시 자주포 피해)"
```

---

### Task 3: 격멸 상한 — 간접포만 CP 15% 바닥

**Files:**
- Modify: `src/c2/application/simulation/engine.py`
- Test: `tests/application/test_indirect_floor.py` (신규)

**Interfaces:**
- Consumes: `_resolve_indirect_fire`의 적 피해 라인 + 아군 오사(fratricide) 피해 라인.
- Produces: 상수 `_INDIRECT_CP_FLOOR`; 간접포가 CP를 15% 미만으로 못 내림.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_indirect_floor.py`:

```python
"""간접포 격멸 상한: 자주포 간접사격은 CP 15% 바닥까지만."""
import random, tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine, _INDIRECT_CP_FLOOR
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, utype, x, y, **kw):
    return Unit(id=id, side=side, unit_type=utype, x=x, y=y, combat_power=100.0,
                firepower_index=130.0 if utype == "자주포" else 100.0,
                max_speed=4.0, status="active", waypoints=[], current_action="hold", **kw)


def test_indirect_fire_cannot_kill_below_floor():
    random.seed(5)
    spg   = _mk("자주포중대", "BLUFOR", "자주포", 8_000.0, 8_000.0, indirect_range=30_000.0)
    enemy = _mk("적보병1중대", "OPFOR", "기계화보병", 16_000.0, 16_000.0)
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "floor.db")
    eng = WargameEngine([spg, enemy], db=db)
    eng.full_recon = True
    # 충분히 오래 간접사격 (재보급 사이클 포함) — 적은 격멸되지 않고 바닥에서 멈춰야 함
    for _ in range(60):
        eng._tick()
    assert enemy.status != "destroyed", "간접포 단독으로는 격멸되면 안 됨"
    assert enemy.combat_power >= _INDIRECT_CP_FLOOR - 0.01, f"CP가 바닥({_INDIRECT_CP_FLOOR}) 밑으로 내려가면 안 됨: {enemy.combat_power}"
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_indirect_floor.py -v`
Expected: FAIL (`_INDIRECT_CP_FLOOR` 미존재, 또는 적이 격멸되어 assert 실패).

- [ ] **Step 3: 상수 추가**

`engine.py` Task 2 상수 아래에 추가:

```python
_INDIRECT_CP_FLOOR = 15.0   # 간접포는 이 CP 이하로 격멸 불가 (제압까지만)
```

- [ ] **Step 4: 적 피해 라인에 바닥 적용**

`_resolve_indirect_fire`의 적 피해 라인
`enemy.combat_power = max(0.0, enemy.combat_power - damage)` 를 교체:

```python
                if enemy.combat_power > _INDIRECT_CP_FLOOR:
                    enemy.combat_power = max(_INDIRECT_CP_FLOOR, enemy.combat_power - damage)
                # else: 이미 바닥 이하(직사 저하) → 간접포 무피해
```

- [ ] **Step 5: 아군 오사(fratricide) 라인에 바닥 적용**

`_resolve_indirect_fire`의 아군 오사 피해 라인
`f.combat_power = max(0.0, f.combat_power - fdmg)` 를 교체:

```python
                if f.combat_power > _INDIRECT_CP_FLOOR:
                    f.combat_power = max(_INDIRECT_CP_FLOOR, f.combat_power - fdmg)
                # else: 이미 바닥 이하 → 간접포 무피해
```

- [ ] **Step 6: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_indirect_floor.py -v`
Expected: PASS (1 passed).

- [ ] **Step 7: 결정성 확인 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py::test_engine_is_deterministic_under_fixed_seed -v`
Expected: PASS.

```bash
git add src/c2/application/simulation/engine.py tests/application/test_indirect_floor.py
git commit -m "feat(engine): 간접포 격멸 상한 CP15% (최종 격멸은 직사 몫)"
```

---

### Task 4: 통제구역 — 도메인 + 엔진 승리조건 + state

**Files:**
- Create: `src/c2/domain/wargame/control_point.py`
- Modify: `src/c2/application/simulation/engine.py`
- Test: `tests/application/test_control_points.py` (신규)

**Interfaces:**
- Produces: `ControlPoint`(frozen dataclass) + `default_control_points() -> list[ControlPoint]`; 엔진 `_update_control_points(dt)`, `_cp_owner`/`_cp_majority_since`/`_cp_winner`; `_check_winner`가 CP승리 반영; `get_state()`에 `control_points` 키.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_control_points.py`:

```python
"""통제구역: 반경 내 다수 점령 → 2곳 이상 유지 시 승리."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.domain.wargame.control_point import ControlPoint, default_control_points
from c2.application.simulation.engine import WargameEngine, _CP_HOLD_TO_WIN
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, x, y):
    return Unit(id=id, side=side, unit_type="기계화보병", x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=[], current_action="hold")


def test_default_control_points_are_three():
    cps = default_control_points()
    assert len(cps) == 3
    assert all(isinstance(c, ControlPoint) for c in cps)


def test_presence_majority_captures_point():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cp.db")
    # BLUFOR 부대를 통제-브라보(15000,15000) 위에 배치
    blu = _mk("보병1중대", "BLUFOR", 15_000.0, 15_000.0)
    eng = WargameEngine([blu], db=db)
    eng._tick()
    state = eng.get_state()
    cps = {c["id"]: c for c in state["control_points"]}
    assert cps["통제-브라보"]["owner"] == "BLUFOR"
    assert cps["통제-브라보"]["blufor_near"] >= 1


def test_holding_two_points_wins():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cpwin.db")
    # BLUFOR 2부대를 통제-알파(12000,14000)·통제-브라보(15000,15000) 위에 배치, OPFOR 없음
    b1 = _mk("보병1중대", "BLUFOR", 12_000.0, 14_000.0)
    b2 = _mk("보병2중대", "BLUFOR", 15_000.0, 15_000.0)
    eng = WargameEngine([b1, b2], db=db)
    # _CP_HOLD_TO_WIN 게임초 이상 유지 (dt=30/틱)
    ticks = int(_CP_HOLD_TO_WIN / 30) + 3
    for _ in range(ticks):
        eng._tick()
    assert eng._check_winner() == "BLUFOR", "2곳을 유지시간 이상 점령하면 승리"
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_control_points.py -v`
Expected: FAIL (`control_point` 모듈/`_CP_HOLD_TO_WIN` 미존재).

- [ ] **Step 3: 도메인 `ControlPoint` 생성**

`src/c2/domain/wargame/control_point.py` 생성:

```python
"""통제구역(control point) 값 객체 — 순수 도메인.

점령·승리 판정 로직은 엔진(application)에 있고, 여기서는 위치 값 객체와
기본 배치만 정의한다. 표준 라이브러리만 의존한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ControlPoint:
    id: str
    x: float
    y: float


def default_control_points() -> List[ControlPoint]:
    """철원 시나리오 기본 통제구역 3곳 (경합지대)."""
    return [
        ControlPoint("통제-알파", 12_000.0, 14_000.0),
        ControlPoint("통제-브라보", 15_000.0, 15_000.0),
        ControlPoint("통제-찰리", 14_000.0, 12_000.0),
    ]
```

- [ ] **Step 4: 상수 + import 추가 (engine.py)**

`engine.py` 상단 import(도메인 import 근처, `from c2.domain.wargame.unit import ...` 아래)에 추가:

```python
from c2.domain.wargame.control_point import ControlPoint, default_control_points
```

Task 3 상수 아래에 추가:

```python
_CP_CAPTURE_RADIUS = 2_000.0   # 통제구역 점령 판정 반경 (m)
_CP_HOLD_TO_WIN    = 300.0     # ≥2곳 다수 유지 승리 게임초
```

- [ ] **Step 5: 엔진 상태 필드 추가**

`__init__`(다른 상태 근처)에 추가:

```python
        self._control_points = default_control_points()
        self._cp_owner: Dict[str, Optional[str]] = {cp.id: None for cp in self._control_points}
        self._cp_majority_since: Dict[str, Optional[float]] = {"BLUFOR": None, "OPFOR": None}
        self._cp_winner: Optional[str] = None
```

`reset()`(다른 상태 초기화 근처)에 추가(통제구역 목록은 유지, 소유/타이머만 초기화):

```python
            self._cp_owner = {cp.id: None for cp in self._control_points}
            self._cp_majority_since = {"BLUFOR": None, "OPFOR": None}
            self._cp_winner = None
```

- [ ] **Step 6: `_update_control_points` 추가 + `_tick`에서 호출**

`_check_winner` 근처(또는 `_resolve_air_support` 아래)에 메서드 추가:

```python
    def _update_control_points(self, dt: float):
        """통제구역 점령 갱신 + 다수(≥2) 유지 승리 판정. 매 틱 호출."""
        for cp in self._control_points:
            blu = sum(1 for u in self.units if u.side == "BLUFOR" and u.is_active()
                      and math.hypot(u.x - cp.x, u.y - cp.y) <= _CP_CAPTURE_RADIUS)
            opf = sum(1 for u in self.units if u.side == "OPFOR" and u.is_active()
                      and math.hypot(u.x - cp.x, u.y - cp.y) <= _CP_CAPTURE_RADIUS)
            new_owner = self._cp_owner.get(cp.id)
            if blu > opf:
                new_owner = "BLUFOR"
            elif opf > blu:
                new_owner = "OPFOR"
            # 동수/무부대 → 이전 소유 유지
            if new_owner != self._cp_owner.get(cp.id):
                self._cp_owner[cp.id] = new_owner
                self.db.log_event(self.tick, self.game_time, "CP_CAPTURE",
                                  f"{cp.id} 통제 → {new_owner}")
        for side in ("BLUFOR", "OPFOR"):
            held = sum(1 for o in self._cp_owner.values() if o == side)
            if held >= 2:
                if self._cp_majority_since[side] is None:
                    self._cp_majority_since[side] = self.game_time
                elif self.game_time - self._cp_majority_since[side] >= _CP_HOLD_TO_WIN:
                    self._cp_winner = side
            else:
                self._cp_majority_since[side] = None
```

`_tick`의 `self._resolve_air_support(dt)` 줄 **아래**에 호출 추가:

```python
        self._update_control_points(dt)
```

- [ ] **Step 7: `_check_winner`에 CP승리 반영**

`_check_winner`의 `return None` 을 교체:

```python
        if self._cp_winner:
            return self._cp_winner
        return None
```

(전멸 판정 3줄은 그대로 위에 유지 — 전멸 우선, 없으면 CP승리.)

- [ ] **Step 8: `get_state`에 `control_points` 추가**

`get_state`의 반환 dict에 `"winner": self._check_winner(),` 근처(같은 dict 안)에 키 추가:

```python
                "control_points": [
                    {
                        "id": cp.id, "x": round(cp.x, 1), "y": round(cp.y, 1),
                        "owner": self._cp_owner.get(cp.id),
                        "blufor_near": sum(
                            1 for u in self.units if u.side == "BLUFOR" and u.is_active()
                            and math.hypot(u.x - cp.x, u.y - cp.y) <= _CP_CAPTURE_RADIUS),
                        "opfor_near": sum(
                            1 for u in self.units if u.side == "OPFOR" and u.is_active()
                            and math.hypot(u.x - cp.x, u.y - cp.y) <= _CP_CAPTURE_RADIUS),
                    }
                    for cp in self._control_points
                ],
```

- [ ] **Step 9: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_control_points.py -v`
Expected: PASS (3 passed).

- [ ] **Step 10: 결정성 확인 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py::test_engine_is_deterministic_under_fixed_seed -v`
Expected: PASS.

```bash
git add src/c2/domain/wargame/control_point.py src/c2/application/simulation/engine.py tests/application/test_control_points.py
git commit -m "feat(engine): 통제구역 3곳 다수유지 승리조건 + get_state 노출"
```

---

### Task 5: 통제구역 UI — 웹 API lat/lon + 대시보드 마커

**Files:**
- Modify: `src/c2/presentation/web/api.py`
- Modify: `ui/dashboard/index.html`
- Test: `tests/presentation/test_api_control_points.py` (신규)

**Interfaces:**
- Consumes: Task 4의 `get_state()` `control_points`(x/y/owner/near).
- Produces: `_convert_state_to_api`가 각 control_point에 `lat`/`lon` 부여; 대시보드가 마커+반경 렌더.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/presentation/test_api_control_points.py`:

```python
"""웹 API state 변환: control_points에 lat/lon 부여."""
from c2.presentation.web.api import _convert_state_to_api


def test_control_points_get_latlon():
    state = {
        "tick": 1, "game_time": 30.0, "game_time_str": "00:00:30",
        "units": [], "running": True, "winner": None,
        "intelligence": {"BLUFOR": [], "OPFOR": []}, "air_supports": [],
        "control_points": [
            {"id": "통제-브라보", "x": 15000.0, "y": 15000.0,
             "owner": "BLUFOR", "blufor_near": 1, "opfor_near": 0},
        ],
    }
    api = _convert_state_to_api(state)
    cps = api.get("control_points", [])
    assert len(cps) == 1
    assert "lat" in cps[0] and "lon" in cps[0]
    assert cps[0]["id"] == "통제-브라보"
    assert cps[0]["owner"] == "BLUFOR"
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/presentation/test_api_control_points.py -v`
Expected: FAIL (api 결과에 control_points 없음 → KeyError/빈 리스트).

- [ ] **Step 3: `_convert_state_to_api`에 control_points 변환 추가**

`api.py` `_convert_state_to_api`는 `result = { ... "units": [], "intelligence": {...}, "air_supports": [] }` dict를 초기화한 뒤 각 리스트에 append하고 `return result` 한다.

(a) 초기 `result` dict의 `"air_supports": [],` 줄 **아래**에 키 추가:

```python
        "air_supports": [],
        "control_points": [],
```

(b) 함수 끝의 `return result` **앞**에 변환 루프 추가(기존 append 패턴과 동일):

```python
    # 통제구역 좌표 변환
    for cp in state.get("control_points", []):
        clat, clon = _xy_to_latlon(cp.get("x", 0), cp.get("y", 0))
        result["control_points"].append({**cp, "lat": clat, "lon": clon})
```

기존 키·블록은 절대 제거하지 않는다.

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/presentation/test_api_control_points.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: 대시보드 마커 렌더 추가**

`ui/dashboard/index.html`의 마커 캐시 선언부(현재 `const unitMarkers = {}; const unitWpLines = {}; const airCircles = {}; const intelMarkers = {};` 블록, 대략 533~536행) **아래**에 캐시 추가:

```javascript
const cpLayers     = {};   // cp.id → {circle, marker}
```

그리고 `updateMap(state)` 함수 안, 유닛/인텔 렌더 뒤(함수 끝나기 전 적절한 위치)에 아래 렌더 블록을 삽입:

```javascript
  // ── 통제구역(control points) ─────────────────────────────
  for (const cp of state.control_points || []) {
    const latlng = [cp.lat, cp.lon];
    const color = cp.owner === 'BLUFOR' ? '#1E88E5'
                : cp.owner === 'OPFOR'  ? '#E53935' : '#888';
    const label = `${cp.id} (${cp.owner || '중립'})  아군${cp.blufor_near}/적${cp.opfor_near}`;
    if (cpLayers[cp.id]) {
      cpLayers[cp.id].circle.setLatLng(latlng).setStyle({color, fillColor: color});
      cpLayers[cp.id].marker.setLatLng(latlng);
      cpLayers[cp.id].marker.setIcon(L.divIcon({
        html: `<div style="white-space:nowrap;font-size:11px;font-weight:700;color:${color};text-shadow:0 0 3px #000;">◆ ${label}</div>`,
        className: '', iconSize: [10, 10], iconAnchor: [5, 5],
      }));
    } else {
      const circle = L.circle(latlng, {
        radius: 2000, color, weight: 1.5, fillColor: color, fillOpacity: 0.10,
        dashArray: '6,5',
      }).addTo(map);
      const marker = L.marker(latlng, {
        icon: L.divIcon({
          html: `<div style="white-space:nowrap;font-size:11px;font-weight:700;color:${color};text-shadow:0 0 3px #000;">◆ ${label}</div>`,
          className: '', iconSize: [10, 10], iconAnchor: [5, 5],
        }),
        zIndexOffset: 5,
      }).addTo(map);
      cpLayers[cp.id] = { circle, marker };
    }
  }
```

- [ ] **Step 6: 대시보드 스모크 확인 (렌더 문법 오류 없음)**

Run: `PYTHONPATH=src python3 -c "
import re
html = open('ui/dashboard/index.html', encoding='utf-8').read()
assert 'control_points' in html, 'control_points 렌더 블록이 있어야 함'
assert 'cpLayers' in html, 'cpLayers 캐시가 있어야 함'
print('대시보드 통제구역 렌더 블록 삽입 확인 OK')
"`
Expected: `대시보드 통제구역 렌더 블록 삽입 확인 OK`.

- [ ] **Step 7: 커밋**

```bash
git add src/c2/presentation/web/api.py ui/dashboard/index.html tests/presentation/test_api_control_points.py
git commit -m "feat(ui): 통제구역 lat/lon 변환 + 대시보드 마커 렌더"
```

---

### Task 6: 결정성 골든 재생성

**Files:**
- Regenerate: `tests/characterization/engine_900tick_seed42.json`

**Interfaces:**
- Consumes: Task 1~4의 엔진 변경(궤적·결과 변화). `_CONTACT_PLAN` 좌표는 불변(직전 기능의 OPFOR 좌표 그대로).

- [ ] **Step 1: 기존 골든 삭제**

Run: `rm tests/characterization/engine_900tick_seed42.json`

- [ ] **Step 2: 골든 재생성 (1회 실행)**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py -v`
Expected: PASS (snapshot 파일 없으면 새로 기록, a==b 통과).

- [ ] **Step 3: 재확인 (비교 경로)**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py -v`
Expected: PASS (2 passed) — 재생성 골든과 일치.

- [ ] **Step 4: 교전 발생 sanity 확인**

Run: `PYTHONPATH=src python3 -c "import json; d=json.load(open('tests/characterization/engine_900tick_seed42.json')); cps=[r[3] for r in d]; print('CP 범위:', min(cps), '~', max(cps), '| 100미만(교전발생):', any(c<100 for c in cps))"`
Expected: `100미만(교전발생): True`.

- [ ] **Step 5: 커밋**

```bash
git add tests/characterization/engine_900tick_seed42.json
git commit -m "test(characterization): 4개 포병 브레이크 반영해 900틱 골든 재생성"
```

---

### Task 7: 통합 검증 (전체 회귀 + import-linter)

**Files:** (검증 전용)

- [ ] **Step 1: 신규 테스트 전체**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_spg_ammo.py tests/application/test_counter_battery.py tests/application/test_indirect_floor.py tests/application/test_control_points.py tests/presentation/test_api_control_points.py tests/characterization/test_engine_determinism.py -v`
Expected: 전부 PASS.

- [ ] **Step 2: 전체 스위트 회귀**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: 신규 실패 없음. 기존 fratricide/scenario/session/harness 테스트 통과. (간접포 격멸상한으로 fratricide 테스트가 CP0을 기대했다면 확인 — 기존 `test_engine_fratricide`는 `< 100.0`만 검증하므로 영향 없음.)

- [ ] **Step 3: import-linter 계약**

Run: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`
Expected: `Contracts: 3 kept, 0 broken.`

- [ ] **Step 4: (검증만 통과 시 커밋 불필요)**

회귀 수정이 필요했다면 그 수정만 별도 커밋.

---

## Self-Review

**1. Spec coverage:**
- 스펙 ②탄약 → Task 1. ②대포병 shoot-and-scoot → Task 2. ③격멸상한(간접포만, 적+아군오사) → Task 3. ④통제구역(도메인+엔진+승리+state) → Task 4, (웹API lat/lon+대시보드) → Task 5. ⑤골든 → Task 6. 테스트계획 → 각 Task + Task 7.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드/명령/기대출력. Task 5 Step 3의 "파일 열어 확인" 주석은 반환 dict 위치 안내(플레이스홀더 아님) — 삽입 코드 자체는 완전 제공.

**3. Type consistency:** 상수명(`_SPG_FIRE_BUDGET`/`_SPG_RESUPPLY_COOLDOWN`/`_CB_EXPOSURE_DELAY`/`_CB_DAMAGE_RATE`/`_CB_RAMP`/`_CB_MOVE_RESET`/`_INDIRECT_CP_FLOOR`/`_CP_CAPTURE_RADIUS`/`_CP_HOLD_TO_WIN`)이 engine·테스트에서 일치. `ControlPoint`/`default_control_points` 시그니처가 domain 정의(Task 4 Step 3)와 engine import·테스트에서 일치. `control_points` state 키가 engine(Task 4 Step 8)·web api(Task 5 Step 3)·대시보드(Task 5 Step 5)·테스트에서 일치. 통제구역 id("통제-알파/브라보/찰리")가 domain·테스트에서 일치.
