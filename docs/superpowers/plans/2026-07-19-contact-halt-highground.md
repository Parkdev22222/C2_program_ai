# 기동 중 교전 시 정지·교전 + BLUFOR 고지 기동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 부대가 기동 중 직사 교전에 접촉하면 정지해 교전하고(양측), BLUFOR는 교전을 지속하며 주변 고지로 룰 기반 소폭 기동하며, 교전 종료 시 원래 임무를 재개한다.

**Architecture:** `engine.py`의 `_move_units`에서 waypoint 전진 직전에 교전 접촉을 판정해, 접촉 시 waypoint를 보존한 채 전진을 멈춘다(자동 재개). BLUFOR는 별도 룰(`_blufor_combat_reposition`)로 사거리를 유지하며 고도+엄폐 유리 지점으로 이동한다.

**Tech Stack:** Python 3.9+, 표준 라이브러리, pytest.

## Global Constraints

- 대상 파일: `src/c2/application/simulation/engine.py`, 신규 테스트 `tests/application/test_contact_halt.py`, `tests/application/test_highground_reposition.py`, 골든 `tests/characterization/engine_900tick_seed42.json`.
- import-linter 3 kept/0 broken: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`.
- 테스트: `PYTHONPATH=src python3 -m pytest <경로> -v` (시스템 python3=3.9.6; bare `python` 없음).
- 상수: `_COMBAT_REPOS_RADIUS=300.0`, `_COMBAT_MOVE_MULT=0.4`, `_COMBAT_COVER_WEIGHT=150.0`.
- 교전 판정: 상호 직사 사거리(`_engagement_factor(u.unit_type,d)>0` 또는 `_engagement_factor(e.unit_type,d)>0`). 자주포(u/적)는 제외.
- `_engagement_factor(attacker_type, dist)`는 engine.py에 이미 import됨. `terrain.elevation/cover_factor/movement_speed_factor` 사용 가능. `u.distance_to(e)` 존재.
- 결정성 골든은 Task 3 재생성. Task 1~2 동안 `test_engine_snapshot_is_stable` FAIL 예상(무시), `test_engine_is_deterministic_under_fixed_seed`(a==b)는 항상 통과.

## File Structure

- `engine.py` — `_in_direct_combat`(Task 1), `_move_units` 정지 분기(Task 1/2), `_ground_score`+`_blufor_combat_reposition`(Task 2)
- 골든 재생성(Task 3), 통합 검증(Task 4)

---

### Task 1: 교전 접촉 판정 + 정지(양측 halt) + 재개

**Files:**
- Modify: `src/c2/application/simulation/engine.py`
- Test: `tests/application/test_contact_halt.py` (신규)

**Interfaces:**
- Produces: `_in_direct_combat(u) -> Unit | None`; `_move_units`가 접촉 시 waypoint 전진을 스킵(정지)하고 waypoint를 보존.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_contact_halt.py`:

```python
"""기동 중 직사 교전 접촉 시 정지·교전 + 종료 후 재개."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.domain.wargame.combat import _engagement_factor
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _engine(units):
    return WargameEngine(units, db=WargameDB(db_path=Path(tempfile.mkdtemp()) / "ch.db"))


def _mk(id, side, ut, x, y, **kw):
    return Unit(id=id, side=side, unit_type=ut, x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=kw.get("wp", []), current_action=kw.get("act", "hold"))


def test_in_direct_combat_detection():
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000)
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 10800, 10000)  # 800m < 기계화보병 1500
    eng = _engine([b, o])
    assert eng._in_direct_combat(b) is o
    o.x = 22000  # 12km 밖 → 사거리 밖
    assert eng._in_direct_combat(b) is None


def test_artillery_not_in_direct_combat():
    spg = _mk("자주포중대", "BLUFOR", "자주포", 10000, 10000)
    o   = _mk("적보병1중대", "OPFOR", "기계화보병", 10500, 10000)
    eng = _engine([spg, o])
    assert eng._in_direct_combat(spg) is None   # 자주포는 직사 교전 없음
    b   = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000)
    o2  = _mk("적자주포중대", "OPFOR", "자주포", 10500, 10000)
    eng2 = _engine([b, o2])
    assert eng2._in_direct_combat(b) is None     # 적 자주포는 직사 위협 아님(제외)


def test_unit_halts_on_contact_and_resumes():
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000, wp=[[10000, 25000]], act="attack")
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 10800, 10000)  # 800m 접촉
    eng = _engine([b, o])
    y0 = b.y
    eng._move_units(30.0)
    # 정지 — waypoint 방향(y+) 전진 안 함, waypoint 보존
    assert b.y == y0
    assert b.waypoints == [[10000, 25000]]
    # 적 격멸 → 다음 틱부터 waypoint 방향 전진 재개
    o.status = "destroyed"
    eng._move_units(30.0)
    assert b.y > y0
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_contact_halt.py -v`
Expected: FAIL (`_in_direct_combat` 미정의).

- [ ] **Step 3: `_in_direct_combat` 메서드 추가**

`engine.py`의 `_move_units` 정의 **앞**(또는 `_resolve_combat` 근처)에 추가:

```python
    def _in_direct_combat(self, u: "Unit"):
        """u가 상호 직사 사거리 내 적과 교전 접촉 중이면 가장 가까운 적을 반환, 아니면 None.
        자주포(u/적)는 직사 교전 대상이 아니므로 제외한다(기존 _resolve_combat 규칙과 동일)."""
        if u.unit_type == "자주포":
            return None
        enemy_side = "OPFOR" if u.side == "BLUFOR" else "BLUFOR"
        nearest = None
        nearest_d = float("inf")
        for e in self.units:
            if e.side != enemy_side or not e.is_active() or e.unit_type == "자주포":
                continue
            d = u.distance_to(e)
            if _engagement_factor(u.unit_type, d) > 0 or _engagement_factor(e.unit_type, d) > 0:
                if d < nearest_d:
                    nearest_d = d
                    nearest = e
        return nearest
```

- [ ] **Step 4: `_move_units`에 정지 분기 삽입**

`_move_units`의 `if spd_mult <= 0: continue` **다음 줄**(추격/waypoint 처리 앞)에 삽입:

```python
            # ── 기동 중 직사 교전 접촉 → 정지·교전 (waypoint 보존, 종료 시 자동 재개) ──
            if self._in_direct_combat(u) is not None:
                continue   # 그 자리 정지·교전 (이동 없음). BLUFOR 고지 기동은 Task 2에서 추가.
```

- [ ] **Step 5: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_contact_halt.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: 결정성(a==b) 확인 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py::test_engine_is_deterministic_under_fixed_seed -v`
Expected: PASS.

```bash
git add src/c2/application/simulation/engine.py tests/application/test_contact_halt.py
git commit -m "feat(engine): 기동 중 직사 교전 접촉 시 정지·교전 (waypoint 보존·자동 재개)"
```

---

### Task 2: BLUFOR 고지 기동 (교전 중 고도+엄폐 유리 지점으로)

**Files:**
- Modify: `src/c2/application/simulation/engine.py`
- Test: `tests/application/test_highground_reposition.py` (신규)

**Interfaces:**
- Consumes: Task 1의 `_in_direct_combat`, `_move_units` 정지 분기.
- Produces: `_ground_score(x, y) -> float`; `_blufor_combat_reposition(u, enemy, dt)`; `_move_units` 분기에서 BLUFOR는 정지 대신 고지 기동.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_highground_reposition.py`:

```python
"""교전 중 BLUFOR 고지 기동: 사거리 유지하며 고도+엄폐 유리 지점으로."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.domain.wargame.combat import _engagement_factor
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _engine(units):
    return WargameEngine(units, db=WargameDB(db_path=Path(tempfile.mkdtemp()) / "hg.db"))


def _mk(id, side, ut, x, y, **kw):
    return Unit(id=id, side=side, unit_type=ut, x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=kw.get("wp", []), current_action=kw.get("act", "hold"))


def test_ground_score_prefers_high_and_covered():
    eng = _engine([_mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000)])
    # 점수는 고도 + 엄폐×가중치 — 실수 반환(호출 가능성·형만 검증)
    s = eng._ground_score(12000.0, 12000.0)
    assert isinstance(s, float)


def test_blufor_repositions_keeping_range_and_waypoint():
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 12000, 12000, wp=[[12000, 26000]], act="attack")
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 12700, 12000)  # 700m 접촉
    eng = _engine([b, o])
    s0 = eng._ground_score(b.x, b.y)
    for _ in range(10):
        eng._move_units(30.0)
    s1 = eng._ground_score(b.x, b.y)
    # 고지 기동: 지형 점수 비감소(더 나은 곳으로만 이동)
    assert s1 >= s0
    # 교전 유지: 적이 여전히 내 직사 사거리 내
    assert _engagement_factor("기계화보병", b.distance_to(o)) > 0
    # waypoint 보존(전진 재개용) — 원 waypoint 방향(먼 북쪽)으로 돌진하지 않음
    assert b.waypoints == [[12000, 26000]]
    assert b.y < 14000   # 사거리 유지로 북쪽 waypoint로 이탈하지 않음


def test_opfor_still_halts_only():
    # OPFOR는 고지 기동 없이 정지만 (Task 1 동작 유지)
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 12000, 12000, wp=[[12000, 4000]], act="attack")
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 12700, 12000)
    eng = _engine([o, b])
    x0, y0 = o.x, o.y
    eng._move_units(30.0)
    assert (o.x, o.y) == (x0, y0)   # OPFOR 완전 정지
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_highground_reposition.py -v`
Expected: FAIL (`_ground_score`/`_blufor_combat_reposition` 미정의; BLUFOR가 아직 정지만 함).

- [ ] **Step 3: 상수 추가**

`engine.py`의 전투 관련 상수 근처(`_SPG_CLOSE_MULT` 등 부근)에 추가:

```python
_COMBAT_REPOS_RADIUS = 300.0   # 교전 중 고지 후보 평가 반경 (m)
_COMBAT_MOVE_MULT    = 0.4     # 교전 기동 속도 배율 (전투 속보)
_COMBAT_COVER_WEIGHT = 150.0   # 지형 점수 엄폐 가중치
```

- [ ] **Step 4: `_ground_score` + `_blufor_combat_reposition` 추가**

`_in_direct_combat` 메서드 근처에 추가:

```python
    def _ground_score(self, x: float, y: float) -> float:
        """지형 유리도 점수 = 고도 + 엄폐 × 가중치 (높을수록 유리)."""
        return terrain.elevation(x, y) + terrain.cover_factor(x, y) * _COMBAT_COVER_WEIGHT

    def _blufor_combat_reposition(self, u: "Unit", enemy: "Unit", dt: float):
        """교전 중 BLUFOR가 적을 직사 사거리에 유지하며 주변 고지(고도+엄폐)로 소폭 기동.
        현 위치보다 유리하고 사거리를 유지하는 후보로만 이동한다(없으면 현 위치 고수)."""
        cur_score = self._ground_score(u.x, u.y)
        best = None
        best_score = cur_score
        for i in range(8):
            ang = math.radians(i * 45)
            cx = max(0.0, min(29_999.0, u.x + math.cos(ang) * _COMBAT_REPOS_RADIUS))
            cy = max(0.0, min(29_999.0, u.y + math.sin(ang) * _COMBAT_REPOS_RADIUS))
            # 이동 후에도 적을 내 직사 사거리에 유지(교전 이탈 방지)
            d_enemy = math.hypot(cx - enemy.x, cy - enemy.y)
            if _engagement_factor(u.unit_type, d_enemy) <= 0:
                continue
            s = self._ground_score(cx, cy)
            if s > best_score:
                best_score = s
                best = (cx, cy)
        if best is None:
            return   # 더 나은 고지 없음 → 현 위치 고수(계속 교전)
        tx, ty = best
        dx, dy = tx - u.x, ty - u.y
        dist = math.hypot(dx, dy)
        if dist <= 0:
            return
        step = min(
            u.max_speed * _COMBAT_MOVE_MULT * terrain.movement_speed_factor(u.x, u.y) * dt,
            dist,
        )
        u.x += dx / dist * step
        u.y += dy / dist * step
```

- [ ] **Step 5: `_move_units` 분기에 BLUFOR 고지 기동 연결**

Task 1에서 넣은 정지 분기를 교체:

```python
            # ── 기동 중 직사 교전 접촉 → 정지·교전 (waypoint 보존, 종료 시 자동 재개) ──
            _contact = self._in_direct_combat(u)
            if _contact is not None:
                if u.side == "BLUFOR":
                    self._blufor_combat_reposition(u, _contact, dt)  # 고지 기동하며 교전
                # OPFOR: 그 자리 정지·교전 (이동 없음)
                continue
```

- [ ] **Step 6: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_highground_reposition.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: 결정성(a==b) 확인 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py::test_engine_is_deterministic_under_fixed_seed -v`
Expected: PASS.

```bash
git add src/c2/application/simulation/engine.py tests/application/test_highground_reposition.py
git commit -m "feat(engine): 교전 중 BLUFOR 고지 기동 (사거리 유지·고도+엄폐 룰기반)"
```

---

### Task 3: 결정성 골든 재생성

**Files:**
- Regenerate: `tests/characterization/engine_900tick_seed42.json`

- [ ] **Step 1: 기존 골든 삭제**

Run: `rm tests/characterization/engine_900tick_seed42.json`

- [ ] **Step 2: 재생성(1회)**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py -v`
Expected: PASS (파일 없으면 새로 기록, a==b 통과).

- [ ] **Step 3: 재확인(비교 경로)**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py -v`
Expected: PASS (2 passed).

- [ ] **Step 4: 교전 발생 sanity**

Run: `PYTHONPATH=src python3 -c "import json; d=json.load(open('tests/characterization/engine_900tick_seed42.json')); cps=[r[3] for r in d]; print('CP범위:', min(cps), '~', max(cps), '| 교전:', any(c<100 for c in cps))"`
Expected: `교전: True`.

- [ ] **Step 5: 커밋**

```bash
git add tests/characterization/engine_900tick_seed42.json
git commit -m "test(characterization): 교전 접촉 정지·BLUFOR 고지 기동 반영해 골든 재생성"
```

---

### Task 4: 통합 검증

**Files:** (검증 전용)

- [ ] **Step 1: 신규 테스트 전체**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_contact_halt.py tests/application/test_highground_reposition.py tests/characterization/test_engine_determinism.py -v`
Expected: 전부 PASS.

- [ ] **Step 2: 전체 스위트 회귀**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: 신규 실패 없음. (교전 정지로 인해 궤적 의존 특성화 테스트가 있으면 확인 — 골든 외에는 좌표 하드코딩 최소.)

- [ ] **Step 3: import-linter**

Run: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`
Expected: `Contracts: 3 kept, 0 broken.`

---

## Self-Review

**1. Spec coverage:** 정지·교전(양측)→Task 1. BLUFOR 고지 기동→Task 2. 종료 후 재개(waypoint 보존)→Task 1 재개 테스트 + Task 2 waypoint 보존 테스트. 골든→Task 3. 검증→Task 4.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드/명령/기대출력. "TBD/적절히" 없음.

**3. Type consistency:** `_in_direct_combat(u)`가 Task 1 정의·Task 2 사용에서 일치. `_ground_score(x,y)`/`_blufor_combat_reposition(u,enemy,dt)` 시그니처가 정의·호출·테스트에서 일치. 상수명(`_COMBAT_REPOS_RADIUS`/`_COMBAT_MOVE_MULT`/`_COMBAT_COVER_WEIGHT`)이 engine·테스트 문맥에서 일치. `_engagement_factor(unit_type, dist)` 시그니처 준수.
