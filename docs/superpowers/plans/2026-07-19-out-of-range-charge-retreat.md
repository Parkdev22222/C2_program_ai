# 사거리 밖 일방 피격 시 돌입/이탈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 부대가 자기 직사 사거리 밖의 적에게 일방적으로 피격당하면 CP 기준으로 적 사거리 안으로 돌입해 반격하거나 엄폐·후방으로 이탈한다(양측).

**Architecture:** `engine.py`의 `_move_units` 교전 분기에서, 접촉한 최근접 적이 내 사거리 밖이면 `_combat_out_of_range_response`(신규)로 돌입/이탈 기동한다. 내 사거리 안이면 기존(BLUFOR 고지 기동/OPFOR 정지) 유지.

**Tech Stack:** Python 3.9+, 표준 라이브러리, pytest.

## Global Constraints

- 대상 파일: `src/c2/application/simulation/engine.py`, 신규 테스트 `tests/application/test_out_of_range_response.py`, 골든 `tests/characterization/engine_900tick_seed42.json`.
- import-linter 3 kept/0 broken: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`.
- 테스트: `PYTHONPATH=src python3 -m pytest <경로> -v` (시스템 python3=3.9.6; bare `python` 없음).
- 상수: `_CHARGE_CP_THRESHOLD=50.0`, `_ESCAPE_MOVE_MULT=0.8`. 기존 `_COMBAT_REPOS_RADIUS=300.0` 재사용.
- `_engagement_factor(attacker_type, dist)`는 engine.py에 import됨. `terrain.cover_factor/movement_speed_factor` 사용 가능. `u.distance_to(e)`, `math` 사용 가능.
- 결정성 골든은 Task 2 재생성. Task 1 동안 `test_engine_snapshot_is_stable` FAIL 예상(무시), `test_engine_is_deterministic_under_fixed_seed`(a==b)는 항상 통과.
- 기존 `_blufor_combat_reposition`/`_in_direct_combat`은 변경 금지(호출만).

---

### Task 1: 돌입/이탈 응답 + `_move_units` 분기 연결

**Files:**
- Modify: `src/c2/application/simulation/engine.py`
- Test: `tests/application/test_out_of_range_response.py` (신규)

**Interfaces:**
- Consumes: 기존 `_in_direct_combat`, `_blufor_combat_reposition`, `_engagement_factor`, `terrain`.
- Produces: `_combat_out_of_range_response(u, enemy, dt)`; `_move_units` 교전 분기가 사거리 밖 접촉 시 이 메서드로 라우팅(양측).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_out_of_range_response.py`:

```python
"""사거리 밖 일방 피격: CP 기준 돌입(반격)/이탈(엄폐·후방) — 양측."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _engine(units):
    return WargameEngine(units, db=WargameDB(db_path=Path(tempfile.mkdtemp()) / "oor.db"))


def _mk(id, side, ut, x, y, cp=100.0, **kw):
    return Unit(id=id, side=side, unit_type=ut, x=x, y=y, combat_power=cp,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=kw.get("wp", []), current_action=kw.get("act", "hold"))


def test_healthy_unit_charges_into_range():
    # 기계화보병(사거리~2.5km)이 3km 밖 대전차(4km 사거리)에게 일방 피격 + CP 건전 → 돌입(거리 감소)
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000, cp=100.0)
    o = _mk("적대전차중대", "OPFOR", "대전차", 13000, 10000, cp=100.0)
    eng = _engine([b, o])
    d0 = b.distance_to(o)
    eng._move_units(30.0)
    assert b.distance_to(o) < d0, "건전한 부대는 적 사거리로 돌입(거리 감소)해야 함"


def test_damaged_unit_retreats():
    # 동일 상황, CP 손상 → 이탈(거리 증가)
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000, cp=40.0)
    o = _mk("적대전차중대", "OPFOR", "대전차", 13000, 10000, cp=100.0)
    eng = _engine([b, o])
    d0 = b.distance_to(o)
    eng._move_units(30.0)
    assert b.distance_to(o) > d0, "손상된 부대는 이탈(거리 증가)해야 함"


def test_opfor_also_responds():
    # 양측 적용: OPFOR 기계화보병이 사거리 밖 BLUFOR 대전차에게 일방 피격 + CP 건전 → 돌입
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 13000, 13000, cp=100.0)
    b = _mk("대전차중대", "BLUFOR", "대전차", 10000, 13000, cp=100.0)
    eng = _engine([o, b])
    d0 = o.distance_to(b)
    eng._move_units(30.0)
    assert o.distance_to(b) < d0, "OPFOR도 CP 건전 시 돌입해야 함(양측 적용)"


def test_in_range_does_not_trigger_charge_retreat():
    # 사거리 내 교전이면 out-of-range 응답 미발동 — BLUFOR는 고지 기동(거리 급변 없음)
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000, cp=100.0)
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 10800, 10000, cp=100.0)  # 800m 사거리 내
    eng = _engine([b, o])
    d0 = b.distance_to(o)
    eng._move_units(30.0)
    # 고지 기동은 사거리 유지(교전 이탈 방지) → 거리가 크게 벌어지거나 좁혀지지 않음
    assert abs(b.distance_to(o) - d0) < 300.0
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_out_of_range_response.py -v`
Expected: FAIL — 현재 사거리 밖이면 응답 없음(BLUFOR는 고수, OPFOR halt) → 돌입/이탈 안 함.

- [ ] **Step 3: 상수 추가**

`engine.py`의 `_COMBAT_COVER_WEIGHT` 근처(기존 `_COMBAT_*` 상수 아래)에 추가:

```python
_CHARGE_CP_THRESHOLD = 50.0   # 이 CP 이상이면 돌입(반격), 미만이면 이탈(엄폐/후방)
_ESCAPE_MOVE_MULT    = 0.8     # 돌입/이탈 기동 속도 배율 (긴급 기동)
```

- [ ] **Step 4: `_combat_out_of_range_response` 메서드 추가**

`_blufor_combat_reposition` 아래에 추가:

```python
    def _combat_out_of_range_response(self, u: "Unit", enemy: "Unit", dt: float):
        """사거리 밖 일방 피격 시(양측): CP 건전하면 적 사거리로 돌입, 손상되면 엄폐·후방 이탈.
        돌입=적에 접근(거리 감소), 이탈=적에서 이격(거리 증가)하는 후보 중 엄폐 좋은 방향으로 이동."""
        charging = u.combat_power >= _CHARGE_CP_THRESHOLD
        cur_d = u.distance_to(enemy)
        best = None
        best_cover = -1.0
        for i in range(8):
            ang = math.radians(i * 45)
            cx = max(0.0, min(29_999.0, u.x + math.cos(ang) * _COMBAT_REPOS_RADIUS))
            cy = max(0.0, min(29_999.0, u.y + math.sin(ang) * _COMBAT_REPOS_RADIUS))
            nd = math.hypot(cx - enemy.x, cy - enemy.y)
            # 돌입=거리 감소 후보만 / 이탈=거리 증가 후보만
            if charging and nd >= cur_d:
                continue
            if not charging and nd <= cur_d:
                continue
            cov = terrain.cover_factor(cx, cy)
            if cov > best_cover:
                best_cover = cov
                best = (cx, cy)
        if best is None:
            # 후보 없음(경계 등) → 돌입=적 방향, 이탈=적 반대 방향 직진
            if charging:
                bx, by = enemy.x, enemy.y
            else:
                bx, by = 2.0 * u.x - enemy.x, 2.0 * u.y - enemy.y
            best = (max(0.0, min(29_999.0, bx)), max(0.0, min(29_999.0, by)))
        tx, ty = best
        dx, dy = tx - u.x, ty - u.y
        dist = math.hypot(dx, dy)
        if dist <= 0:
            return
        step = min(
            u.max_speed * _ESCAPE_MOVE_MULT * terrain.movement_speed_factor(u.x, u.y) * dt,
            dist,
        )
        u.x += dx / dist * step
        u.y += dy / dist * step
```

- [ ] **Step 5: `_move_units` 교전 분기에 사거리 밖 라우팅 추가**

기존 교전 분기(현재):

```python
            # ── 기동 중 직사 교전 접촉 → 정지·교전 (waypoint 보존, 종료 시 자동 재개) ──
            _contact = self._in_direct_combat(u)
            if _contact is not None:
                if u.side == "BLUFOR":
                    self._blufor_combat_reposition(u, _contact, dt)  # 고지 기동하며 교전
                # OPFOR: 그 자리 정지·교전 (이동 없음)
                continue
```

를 교체:

```python
            # ── 기동 중 직사 교전 접촉 → 정지·교전 (waypoint 보존, 종료 시 자동 재개) ──
            _contact = self._in_direct_combat(u)
            if _contact is not None:
                if _engagement_factor(u.unit_type, u.distance_to(_contact)) <= 0:
                    # 적 사거리 밖 일방 피격 → CP 기준 돌입/이탈 (양측)
                    self._combat_out_of_range_response(u, _contact, dt)
                elif u.side == "BLUFOR":
                    self._blufor_combat_reposition(u, _contact, dt)  # 사거리 내 → 고지 기동
                # else OPFOR 사거리 내 → 그 자리 정지·교전
                continue
```

- [ ] **Step 6: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_out_of_range_response.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: 결정성(a==b) 확인 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py::test_engine_is_deterministic_under_fixed_seed -v`
Expected: PASS.

```bash
git add src/c2/application/simulation/engine.py tests/application/test_out_of_range_response.py
git commit -m "feat(engine): 사거리 밖 일방 피격 시 CP 기준 돌입/이탈 (양측)"
```

---

### Task 2: 결정성 골든 재생성

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
git commit -m "test(characterization): 사거리 밖 돌입/이탈 반영해 골든 재생성"
```

---

### Task 3: 통합 검증

**Files:** (검증 전용)

- [ ] **Step 1: 신규 + 인접 테스트**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_out_of_range_response.py tests/application/test_contact_halt.py tests/application/test_highground_reposition.py tests/characterization/test_engine_determinism.py -v`
Expected: 전부 PASS.

- [ ] **Step 2: 전체 스위트 회귀**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: 신규 실패 없음.

- [ ] **Step 3: import-linter**

Run: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`
Expected: `Contracts: 3 kept, 0 broken.`

---

## Self-Review

**1. Spec coverage:** 돌입/이탈 응답(CP 기준·양측)→Task 1. `_move_units` 분기(사거리 밖 라우팅)→Task 1 Step 5. 골든→Task 2. 검증→Task 3. 사거리 내 회귀(고지 기동/정지 유지)→Task 1 `test_in_range_does_not_trigger_charge_retreat`.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드/명령/기대출력. "TBD/적절히" 없음.

**3. Type consistency:** `_combat_out_of_range_response(u, enemy, dt)` 시그니처가 정의·호출·테스트 문맥에서 일치. 상수명(`_CHARGE_CP_THRESHOLD`/`_ESCAPE_MOVE_MULT`/`_COMBAT_REPOS_RADIUS`)이 engine 내 일치. `_engagement_factor(unit_type, dist)` 준수. 기존 `_blufor_combat_reposition`/`_in_direct_combat` 시그니처 불변.
