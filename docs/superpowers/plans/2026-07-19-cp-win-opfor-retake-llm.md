# 통제구역 승리조건 500틱 + OPFOR 탈환 + LLM 통제구역 정보 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 통제구역 다수(≥2) 500틱 유지 또는 전멸 시 승리, BLUFOR 확보 시 OPFOR 전 부대 탈환, LLM 공격계획에 통제구역 정보 제공.

**Architecture:** 엔진의 승리 타이머를 틱 기반으로 바꾸고 `control_points` state에 radius를 추가(Task 1), OPFOR 전략 AI에 탈환 분기 추가(Task 2), 임무계획 쿼리에 통제구역 블록 주입(Task 3), 골든 재생성(Task 4), 검증(Task 5).

**Tech Stack:** Python 3.9+, 표준 라이브러리, pytest.

## Global Constraints

- import-linter 3 kept/0 broken: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`.
- 테스트: `PYTHONPATH=src python3 -m pytest <경로> -v` (시스템 python3=3.9.6; bare `python` 없음).
- 상수: `_CP_HOLD_TO_WIN_TICKS = 500` (기존 `_CP_HOLD_TO_WIN=300.0` 제거). `_CP_CAPTURE_RADIUS=2000.0`(기존).
- OPFOR 탈환: 전 기동부대 진격 + 자주포 standoff, BLUFOR가 CP 1곳 이상 확보 시 override.
- 결정성 골든은 Task 4 재생성. Task 1~3 동안 `test_engine_snapshot_is_stable` FAIL 예상(무시), `test_engine_is_deterministic_under_fixed_seed`(a==b)는 항상 통과.

---

### Task 1: 승리조건 500틱화 + control_points radius 필드

**Files:**
- Modify: `src/c2/application/simulation/engine.py`
- Modify: `tests/application/test_control_points.py`

**Interfaces:**
- Produces: `_CP_HOLD_TO_WIN_TICKS=500`; `_cp_majority_since`가 tick 저장; `get_state` control_points에 `radius` 필드.

- [ ] **Step 1: 테스트 갱신(실패 유도)**

`tests/application/test_control_points.py`를 수정: import를 `_CP_HOLD_TO_WIN` → `_CP_HOLD_TO_WIN_TICKS`로, 승리 테스트의 틱수를 `_CP_HOLD_TO_WIN_TICKS + 3`으로, 그리고 radius 필드 검증을 추가.

```python
from c2.application.simulation.engine import WargameEngine, _CP_HOLD_TO_WIN_TICKS
```

`test_holding_two_points_wins`의 `ticks = int(_CP_HOLD_TO_WIN / 30) + 3` 줄을:

```python
    ticks = _CP_HOLD_TO_WIN_TICKS + 3
```

그리고 `test_presence_majority_captures_point`에 radius 검증 추가(함수 끝에):

```python
    assert cps["통제-브라보"]["radius"] == 2000.0
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_control_points.py -v`
Expected: FAIL (`_CP_HOLD_TO_WIN_TICKS` ImportError).

- [ ] **Step 3: 상수 교체**

`engine.py`의 `_CP_HOLD_TO_WIN    = 300.0     # ≥2곳 다수 유지 승리 게임초` 를 교체:

```python
_CP_HOLD_TO_WIN_TICKS = 500     # ≥2곳 다수 연속 유지 승리 틱수
```

- [ ] **Step 4: `_update_control_points` 타이머 틱화**

`_update_control_points`의 majority 판정 블록:

```python
            if held >= 2:
                if self._cp_majority_since[side] is None:
                    self._cp_majority_since[side] = self.game_time
                elif self.game_time - self._cp_majority_since[side] >= _CP_HOLD_TO_WIN:
                    self._cp_winner = side
            else:
                self._cp_majority_since[side] = None
```

를 교체:

```python
            if held >= 2:
                if self._cp_majority_since[side] is None:
                    self._cp_majority_since[side] = self.tick
                elif self.tick - self._cp_majority_since[side] >= _CP_HOLD_TO_WIN_TICKS:
                    self._cp_winner = side
            else:
                self._cp_majority_since[side] = None
```

- [ ] **Step 5: `get_state` control_points에 radius 추가**

`get_state`의 control_points 항목 dict에 `"radius"` 키 추가(각 항목, `"id": cp.id,` 근처):

```python
                        "id": cp.id, "x": round(cp.x, 1), "y": round(cp.y, 1),
                        "radius": _CP_CAPTURE_RADIUS,
                        "owner": self._cp_owner.get(cp.id),
```

- [ ] **Step 6: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_control_points.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: 결정성(a==b) 확인 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py::test_engine_is_deterministic_under_fixed_seed -v`
Expected: PASS.

```bash
git add src/c2/application/simulation/engine.py tests/application/test_control_points.py
git commit -m "feat(engine): 통제구역 승리 500틱화 + control_points radius 노출"
```

---

### Task 2: OPFOR 전 부대 탈환 공격

**Files:**
- Modify: `src/c2/application/simulation/engine.py`
- Test: `tests/application/test_opfor_retake.py` (신규)

**Interfaces:**
- Consumes: `_control_points`/`_cp_owner`(Task 1 이전부터 존재), `_ai_standoff`, `_CP_CAPTURE_RADIUS`.
- Produces: `_opfor_retaking` 상태; `_opfor_retake_strategy()`; `_run_opfor_strategy_ai`의 탈환 override.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_opfor_retake.py`:

```python
"""OPFOR 탈환: BLUFOR가 통제구역 확보 시 OPFOR 전 부대가 탈환 기동."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, x, y, utype="기계화보병"):
    return Unit(id=id, side=side, unit_type=utype, x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=[], current_action="hold")


def test_opfor_retakes_when_blufor_holds_cp():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "retake.db")
    # BLUFOR 부대가 통제-브라보(15000,15000) 확보, OPFOR 부대는 북동부에서 대기
    blu = _mk("보병1중대", "BLUFOR", 15_000.0, 15_000.0)
    opf = _mk("적보병1중대", "OPFOR", 22_000.0, 22_000.0)
    eng = WargameEngine([blu, opf], db=db)
    eng.full_recon = True
    # 통제구역 점령 반영(1틱) + OPFOR AI 주기(60게임초=2틱) 이상 실행
    for _ in range(5):
        eng._tick()
    assert eng._opfor_retaking is True, "BLUFOR 확보 시 OPFOR 탈환 상태여야 함"
    # OPFOR 부대가 확보 CP(브라보) 방향으로 기동 지시받아야 함
    assert opf.current_action == "attack"
    assert opf.waypoints and opf.waypoints[0] == [15_000.0, 15_000.0]


def test_opfor_retaking_resets_when_cp_lost():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "retake2.db")
    blu = _mk("보병1중대", "BLUFOR", 15_000.0, 15_000.0)
    opf = _mk("적보병1중대", "OPFOR", 22_000.0, 22_000.0)
    eng = WargameEngine([blu, opf], db=db)
    eng.full_recon = True
    for _ in range(5):
        eng._tick()
    assert eng._opfor_retaking is True
    # BLUFOR 격멸 → CP 확보 해제 → 탈환 상태 리셋
    blu.status = "destroyed"
    for _ in range(5):
        eng._tick()
    assert eng._opfor_retaking is False, "BLUFOR가 CP를 내주면 탈환 상태 해제"
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_opfor_retake.py -v`
Expected: FAIL (`_opfor_retaking` 미존재).

- [ ] **Step 3: `_opfor_retaking` 상태 필드 추가**

`__init__`의 `self._opfor_strategy_decided: bool = False` 근처에 추가:

```python
        self._opfor_retaking: bool = False
```

`reset()`의 `self._opfor_strategy_decided  = False` 근처에 추가:

```python
            self._opfor_retaking          = False
```

- [ ] **Step 4: `_opfor_retake_strategy` 메서드 추가**

`_opfor_defend_strategy` 아래(또는 `_opfor_attack_strategy` 앞)에 추가:

```python
    def _opfor_retake_strategy(self, active_opfor: list, held_positions: list):
        """BLUFOR 확보 통제구역을 전 부대 총력으로 탈환.
        기동부대는 가장 가까운 확보 CP로 진격, 자주포는 그 방향 화력지원."""
        non_spg = [u for u in active_opfor if u.unit_type != "자주포"]
        spg     = [u for u in active_opfor if u.unit_type == "자주포"]
        for u in non_spg:
            tx, ty = min(held_positions, key=lambda c: math.hypot(u.x - c[0], u.y - c[1]))
            if math.hypot(u.x - tx, u.y - ty) < _CP_CAPTURE_RADIUS * 0.5:
                u.waypoints = []
            else:
                u.waypoints = [[tx, ty]]
            u.current_action = "attack"
        if spg and held_positions:
            cx = sum(c[0] for c in held_positions) / len(held_positions)
            cy = sum(c[1] for c in held_positions) / len(held_positions)
            for u in spg:
                self._ai_standoff(u, cx, cy, "OPFOR_AI")
```

- [ ] **Step 5: `_run_opfor_strategy_ai`에 탈환 override 삽입**

`_run_opfor_strategy_ai`에서 `active_opfor`를 계산하고 `if not active_opfor: return` **바로 다음**에 삽입:

```python
        # ── BLUFOR가 통제구역을 확보하면 전 부대 총력 탈환 (방어/공격 override) ──
        blufor_held = [
            (cp.x, cp.y) for cp in self._control_points
            if self._cp_owner.get(cp.id) == "BLUFOR"
        ]
        if blufor_held:
            if not self._opfor_retaking:
                self._opfor_retaking = True
                self.db.log_event(
                    self.tick, self.game_time, "OPFOR_AI",
                    f"OPFOR 탈환 공격 개시 — BLUFOR 확보 통제구역 {len(blufor_held)}곳"
                )
            self._opfor_retake_strategy(active_opfor, blufor_held)
            return
        else:
            self._opfor_retaking = False
```

- [ ] **Step 6: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_opfor_retake.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: 결정성(a==b) 확인 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py::test_engine_is_deterministic_under_fixed_seed -v`
Expected: PASS.

```bash
git add src/c2/application/simulation/engine.py tests/application/test_opfor_retake.py
git commit -m "feat(engine): OPFOR 전 부대 통제구역 탈환 공격"
```

---

### Task 3: LLM 공격계획에 통제구역 정보 블록

**Files:**
- Modify: `src/c2/application/agent/mission_planner.py`
- Test: `tests/application/test_cp_query_block.py` (신규)

**Interfaces:**
- Consumes: Task 1의 `state["control_points"]`(id/x/y/radius/owner/near).
- Produces: `_build_control_point_block(state) -> str`; `build_mission_query`가 이 블록을 양쪽 프롬프트 분기에 주입.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_cp_query_block.py`:

```python
"""공격계획 쿼리: 통제구역 목표 블록 구성."""
from c2.application.agent.mission_planner import _build_control_point_block


def test_control_point_block_contains_cp_info():
    state = {
        "control_points": [
            {"id": "통제-브라보", "x": 15000.0, "y": 15000.0, "radius": 2000.0,
             "owner": "BLUFOR", "blufor_near": 1, "opfor_near": 0},
        ],
    }
    block = _build_control_point_block(state)
    assert "통제구역" in block
    assert "통제-브라보" in block
    assert "15000" in block
    assert "2000" in block
    assert "확보" in block


def test_control_point_block_empty_when_no_cps():
    assert _build_control_point_block({"control_points": []}) == ""
    assert _build_control_point_block({}) == ""
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_cp_query_block.py -v`
Expected: FAIL (`_build_control_point_block` 미정의).

- [ ] **Step 3: `_build_control_point_block` 헬퍼 추가**

`mission_planner.py`의 `build_mission_query` 함수 **정의 앞**(모듈 함수)에 추가:

```python
def _build_control_point_block(state: dict) -> str:
    """공격 임무계획에 제공할 통제구역 목표 블록. control_points 없으면 빈 문자열."""
    cps = state.get("control_points", [])
    if not cps:
        return ""
    lines = "\n".join(
        f'  - {c.get("id")}: 좌표[{int(c.get("x", 0))}, {int(c.get("y", 0))}] '
        f'반경{int(c.get("radius", 2000))}m 현재소유={c.get("owner") or "중립"} '
        f'(아군{c.get("blufor_near", 0)}/적{c.get("opfor_near", 0)})'
        for c in cps
    )
    return (
        "[통제구역 목표 — control_points]\n"
        "승리조건: 통제구역 3곳 중 ≥2곳을 500틱 연속 확보하거나 적 전멸.\n"
        "공격부대 waypoint를 통제구역 좌표(미터)로 지향해 ≥2곳을 확보·유지하라 "
        "(반경 내 아군 다수 시 확보).\n"
        f"{lines}"
    )
```

- [ ] **Step 4: `build_mission_query`에서 블록 생성 + 양쪽 분기 주입**

`build_mission_query` 내부, `attack_pos_block = (...)` 정의 뒤에 블록 생성 추가:

```python
    cp_block = _build_control_point_block(state)
```

smolagents 분기의 `[제공 데이터]\n{recon_block}\n{attack_pos_block}` 를:

```python
[제공 데이터]
{recon_block}
{attack_pos_block}
{cp_block}
```

langgraph 분기 호출 `_build_mission_query_funccall(...)`에 `cp_block`을 인자로 전달(마지막 인자로 추가):

```python
        return _build_mission_query_funccall(
            state, recon_block, attack_pos_block, elev_section, air_limit_line,
            fire_priority_block, cp_block,
        )
```

- [ ] **Step 5: `_build_mission_query_funccall` 시그니처·본문에 cp_block 주입**

`_build_mission_query_funccall` 정의의 파라미터 끝에 `cp_block: str = ""` 추가하고, 함수가 구성하는 `[제공 데이터]` 영역(recon_block/attack_pos_block을 넣는 곳)에 `{cp_block}`을 함께 삽입한다.

> 주: `_build_mission_query_funccall`의 정확한 프롬프트 문자열 구조는 파일을 열어 확인하고, `attack_pos_block`이 삽입되는 위치 바로 뒤에 `cp_block`을 같은 방식으로 추가한다. 기존 내용은 제거하지 않는다.

- [ ] **Step 6: 통과 확인 + 회귀**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_cp_query_block.py -v`
Expected: PASS (2 passed).

기존 mission_planner 관련 테스트 회귀:
Run: `PYTHONPATH=src python3 -m pytest tests/application -k "mission or planner or query" -q`
Expected: 신규 실패 없음.

- [ ] **Step 7: 커밋**

```bash
git add src/c2/application/agent/mission_planner.py tests/application/test_cp_query_block.py
git commit -m "feat(planner): 공격 임무계획 쿼리에 통제구역 목표 블록 주입"
```

---

### Task 4: 결정성 골든 재생성

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

Run: `PYTHONPATH=src python3 -c "import json; d=json.load(open('tests/characterization/engine_900tick_seed42.json')); cps=[r[3] for r in d]; print('CP 범위:', min(cps), '~', max(cps), '| 100미만(교전):', any(c<100 for c in cps))"`
Expected: `100미만(교전): True`.

- [ ] **Step 5: 커밋**

```bash
git add tests/characterization/engine_900tick_seed42.json
git commit -m "test(characterization): CP승리 500틱·OPFOR 탈환 반영해 골든 재생성"
```

---

### Task 5: 통합 검증

**Files:** (검증 전용)

- [ ] **Step 1: 신규 테스트 전체**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_control_points.py tests/application/test_opfor_retake.py tests/application/test_cp_query_block.py tests/characterization/test_engine_determinism.py -v`
Expected: 전부 PASS.

- [ ] **Step 2: 전체 스위트 회귀**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: 신규 실패 없음.

- [ ] **Step 3: import-linter**

Run: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`
Expected: `Contracts: 3 kept, 0 broken.`

---

## Self-Review

**1. Spec coverage:** A(승리 500틱+radius)→Task 1. B(OPFOR 탈환)→Task 2. C(LLM 쿼리 블록)→Task 3. D(골든)→Task 4. 검증→Task 5.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드/명령/기대출력. Task 5 Step 5의 funccall 주입 주석은 위치 안내(플레이스홀더 아님, cp_block 삽입 코드 제공).

**3. Type consistency:** `_CP_HOLD_TO_WIN_TICKS`가 engine·test에서 일치. `_opfor_retaking`/`_opfor_retake_strategy` 명칭 일치. `_build_control_point_block(state)` 시그니처가 정의·호출·테스트에서 일치. `radius` state 키가 engine(Task1)·헬퍼(Task3)·테스트에서 일치.
