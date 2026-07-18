# 현실성 튜닝 + 아군 오사 + 공격위치 툴 반영 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 워게임 기동 속도·배치 밀도·포병 사거리를 현실화하고, 포격·공중지원에 양측 대칭 아군 오사(fratricide)를 도입하며, 공격위치 추천 툴이 아군의 폭발 반경 진입 여부를 표시하게 한다.

**Architecture:** 순수 시나리오/상수 변경(`scenario.py`)과 전투 피해 로직 변경(`engine.py`), 프레젠테이션 툴 필드 추가(`wargame_attack_advisor_tool.py`)로 나뉜다. 속도·좌표·사거리·오사가 결정성 골든 스냅샷을 바꾸므로 마지막에 골든을 재생성한다.

**Tech Stack:** Python 3.9+, 표준 라이브러리, pytest.

## Global Constraints

- 대상 파일: `src/c2/application/simulation/scenario.py`, `src/c2/application/simulation/engine.py`, `src/c2/presentation/tools/wargame_attack_advisor_tool.py`, `tests/characterization/test_engine_determinism.py`, `tests/characterization/engine_900tick_seed42.json`.
- 계층 규칙 유지: import-linter 3 kept/0 broken (`PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`). presentation 툴은 `c2.domain`(AIR_SUPPORT_PRESETS) import 허용.
- 테스트 실행: `PYTHONPATH=src python3 -m pytest <경로> -v` (시스템 python3=3.9.6에 pytest 있음; bare `python` 없음).
- 신규 속도값(m/s): 전차 6.0 / 기계화보병 5.0 / 대전차 5.5 / 자주포 4.0 / 정찰 7.0.
- 신규 배치구역: BLUFOR x5_000~10_000·y5_000~10_000, OPFOR x18_000~23_000·y18_000~23_000.
- 신규 포병 indirect_range: BLUFOR 자주포 15_000, OPFOR 자주포 18_000.
- `_COUNTER_BATTERY_DETECT_PROB`: 0.35 → 0.55.
- 오사 피해 = 적 피해와 동일 공식, 양측 대칭. 아군 victim엔 `_check_blufor_cp_threshold`만 호출(적 인텔 노출·`on_blufor_air_hit`·`_indirect_accum` 적 로깅은 미적용). 로그 타입 `FRATRICIDE_AIR`/`FRATRICIDE_INDIRECT`.
- 툴 오사 위험: 방법별 반경 = AIR_SUPPORT_PRESETS radius (cas1500/strike400/artillery2500/helicopter1000). 자동 제거·전환 없음, 플래그+경고만.

## File Structure

- `scenario.py` — 속도/구역/좌표/indirect_range 값 변경 (Task 1)
- `engine.py` — 오사 피해 루프 2곳 + 대포병 확률 (Task 2)
- `wargame_attack_advisor_tool.py` — `_friendly_fire_risk` 헬퍼 + 스케줄 필드 (Task 3)
- `test_engine_determinism.py` + `engine_900tick_seed42.json` — 골든 재생성 (Task 4)

---

### Task 1: 현실성 튜닝 (속도·배치·포병 사거리)

`scenario.py`의 순수 값만 변경한다. 오사/골든과 독립적으로 검증 가능.

**Files:**
- Modify: `src/c2/application/simulation/scenario.py`
- Test: `tests/application/test_scenario_realism.py` (신규)

**Interfaces:**
- Consumes: 기존 `setup_cheorwon_bn()`, `UNIT_TYPE_SPECS`, `_BLUFOR_ZONE`, `_OPFOR_ZONE`.
- Produces: (동작 계약) 신규 속도/좌표/사거리 값 — Task 4의 `_CONTACT_PLAN` 좌표가 이 신규 OPFOR 좌표를 참조.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_scenario_realism.py` 생성:

```python
"""현실성 튜닝: 속도 상향·배치 밀도·포병 사거리 검증."""

from c2.application.simulation.scenario import (
    setup_cheorwon_bn, UNIT_TYPE_SPECS, _BLUFOR_ZONE, _OPFOR_ZONE,
)

_EXPECTED_SPEED = {"전차": 6.0, "기계화보병": 5.0, "대전차": 5.5, "자주포": 4.0}


def test_unit_type_specs_speeds_raised():
    assert UNIT_TYPE_SPECS["전차"]["max_speed"] == 6.0
    assert UNIT_TYPE_SPECS["기계화보병"]["max_speed"] == 5.0
    assert UNIT_TYPE_SPECS["대전차"]["max_speed"] == 5.5
    assert UNIT_TYPE_SPECS["자주포"]["max_speed"] == 4.0
    assert UNIT_TYPE_SPECS["정찰"]["max_speed"] == 7.0


def test_scenario_unit_speeds_match_type():
    for u in setup_cheorwon_bn():
        assert u.max_speed == _EXPECTED_SPEED[u.unit_type], u.id


def test_zones_shrunk_to_battalion_frontage():
    assert _BLUFOR_ZONE == dict(x_min=5_000, x_max=10_000, y_min=5_000, y_max=10_000)
    assert _OPFOR_ZONE == dict(x_min=18_000, x_max=23_000, y_min=18_000, y_max=23_000)


def test_units_start_inside_new_zones():
    for u in setup_cheorwon_bn():
        z = _BLUFOR_ZONE if u.side == "BLUFOR" else _OPFOR_ZONE
        assert z["x_min"] <= u.x <= z["x_max"], u.id
        assert z["y_min"] <= u.y <= z["y_max"], u.id


def test_artillery_indirect_range_map_scaled():
    units = {u.id: u for u in setup_cheorwon_bn()}
    assert units["자주포중대"].indirect_range == 15_000.0
    assert units["적자주포중대"].indirect_range == 18_000.0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_scenario_realism.py -v`
Expected: FAIL (기존 속도 2.5 등, 구역 2000~13000 등이라 assert 불일치).

- [ ] **Step 3: 구현 — `UNIT_TYPE_SPECS` 속도 상향**

`scenario.py`의 `UNIT_TYPE_SPECS`를 교체:

```python
UNIT_TYPE_SPECS: dict = {
    "기계화보병": {"firepower_index": 100.0, "max_speed": 5.0},
    "전차":       {"firepower_index": 160.0, "max_speed": 6.0},
    "정찰":       {"firepower_index":  45.0, "max_speed": 7.0},
    "대전차":     {"firepower_index":  90.0, "max_speed": 5.5},
    "자주포":     {"firepower_index": 130.0, "max_speed": 4.0},
}
```

- [ ] **Step 4: 구현 — 배치 구역 축소**

`scenario.py`의 구역 상수를 교체:

```python
# BLUFOR: 남서부 분지 (대대 정면 ~5km)
_BLUFOR_ZONE = dict(x_min=5_000, x_max=10_000, y_min=5_000, y_max=10_000)
# OPFOR : 북동부 고원 (대대 정면 ~5km)
_OPFOR_ZONE  = dict(x_min=18_000, x_max=23_000, y_min=18_000, y_max=23_000)
```

- [ ] **Step 5: 구현 — `setup_cheorwon_bn()` 좌표·속도·사거리 재배치**

`setup_cheorwon_bn()`의 `return [...]` 부분을 아래로 교체(색상 문자열은 기존 값 유지):

```python
    return [
        # ── BLUFOR (대한민국) — 남서부 (대대 정면 ~5km) ──────────────
        Unit(id="보병1중대", side="BLUFOR", unit_type="기계화보병",
             x=7_000.0, y=6_000.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#1E88E5"),
        Unit(id="보병2중대", side="BLUFOR", unit_type="기계화보병",
             x=8_000.0, y=7_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#42A5F5"),
        Unit(id="보병3중대", side="BLUFOR", unit_type="기계화보병",
             x=9_500.0, y=9_000.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#26C6DA"),
        Unit(id="전차중대", side="BLUFOR", unit_type="전차",
             x=6_000.0, y=7_000.0,
             combat_power=100.0, firepower_index=160.0, max_speed=6.0,
             status="active", waypoints=[], current_action="hold", color="#00BCD4"),
        Unit(id="대전차중대", side="BLUFOR", unit_type="대전차",
             x=9_000.0, y=6_000.0,
             combat_power=100.0, firepower_index=90.0, max_speed=5.5,
             status="active", waypoints=[], current_action="hold", color="#B3E5FC"),
        Unit(id="자주포중대", side="BLUFOR", unit_type="자주포",     # K9A1(실제 40km) — 게임 유효 15km
             x=5_500.0, y=5_500.0,
             combat_power=100.0, firepower_index=130.0, max_speed=4.0,
             indirect_range=15_000.0,
             status="active", waypoints=[], current_action="hold", color="#4DD0E1"),

        # ── OPFOR (북한) — 북동부 (대대 정면 ~5km) ──────────────────
        Unit(id="적보병1중대", side="OPFOR", unit_type="기계화보병",
             x=20_000.0, y=19_000.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#E53935"),
        Unit(id="적보병2중대", side="OPFOR", unit_type="기계화보병",
             x=19_000.0, y=20_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#EF5350"),
        Unit(id="적보병3중대", side="OPFOR", unit_type="기계화보병",
             x=18_500.0, y=18_500.0,
             combat_power=100.0, firepower_index=100.0, max_speed=5.0,
             status="active", waypoints=[], current_action="hold", color="#FF8A65"),
        Unit(id="적전차중대", side="OPFOR", unit_type="전차",
             x=21_000.0, y=20_000.0,
             combat_power=100.0, firepower_index=155.0, max_speed=6.0,
             status="active", waypoints=[], current_action="hold", color="#FF7043"),
        Unit(id="적대전차중대", side="OPFOR", unit_type="대전차",
             x=21_500.0, y=21_500.0,
             combat_power=100.0, firepower_index=85.0, max_speed=5.5,
             status="active", waypoints=[], current_action="hold", color="#FFAB91"),
        Unit(id="적자주포중대", side="OPFOR", unit_type="자주포",         # M1978 곡산(실제 60km) — 게임 유효 18km
             x=22_500.0, y=22_500.0,
             combat_power=100.0, firepower_index=130.0, max_speed=4.0,
             indirect_range=18_000.0,
             status="active", waypoints=[], current_action="hold", color="#FFCCBC"),
    ]
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_scenario_realism.py -v`
Expected: PASS (5 passed).

- [ ] **Step 7: 커밋**

```bash
git add src/c2/application/simulation/scenario.py tests/application/test_scenario_realism.py
git commit -m "feat(scenario): 현실성 튜닝 — 속도 2.5x·대대 정면 5km·포병 사거리 지도스케일"
```

---

### Task 2: 엔진 아군 오사(fratricide) + 대포병 탐지 강화

`engine.py`의 공중지원·간접사격에 같은 편 피해를 추가하고 대포병 탐지확률을 올린다.

**Files:**
- Modify: `src/c2/application/simulation/engine.py`
- Test: `tests/application/test_engine_fratricide.py` (신규)

**Interfaces:**
- Consumes: 기존 `_resolve_air_support`, `_resolve_indirect_fire`, `apply_air_support_plan`, `_matchup_factor`, `BASE_ATTRITION_RATE`, `_status_firepower_mult`, `terrain`, `_INDIRECT_LOG_THRESHOLD`, `_check_blufor_cp_threshold`.
- Produces: (동작 계약) 반경 내 아군 CP 감소 + `FRATRICIDE_AIR`/`FRATRICIDE_INDIRECT` 이벤트.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_engine_fratricide.py` 생성:

```python
"""아군 오사(fratricide): 공중지원·간접사격 반경 내 아군도 피해."""

import random
import tempfile
from pathlib import Path

from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _engine(units):
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "ff.db")
    return WargameEngine(units, db=db), db


def _mk(id, side, utype, x, y, **kw):
    return Unit(id=id, side=side, unit_type=utype, x=x, y=y,
                combat_power=100.0, firepower_index=100.0, max_speed=5.0,
                status="active", waypoints=[], current_action="hold", **kw)


def test_air_support_damages_friendly_in_blast():
    random.seed(1)
    # BLUFOR 공중지원이 (10000,10000)에 투입 — 같은 편 아군이 그 반경 안에 있음
    friendly = _mk("보병1중대", "BLUFOR", "기계화보병", 10_000.0, 10_000.0)
    enemy    = _mk("적보병1중대", "OPFOR", "기계화보병", 25_000.0, 25_000.0)
    eng, db = _engine([friendly, enemy])
    eng.apply_air_support_plan({
        "air_support_plans": [{
            "call_sign": "EAGLE-1", "support_type": "cas",
            "target": [10_000, 10_000], "radius": 1_500, "delay": 0,
        }],
    })
    for _ in range(30):
        eng._tick()
    assert friendly.combat_power < 100.0, "반경 내 아군이 오사 피해를 입어야 함"
    events = db.get_recent_events(n=200)
    assert any(e["event_type"] == "FRATRICIDE_AIR" for e in events)


def test_air_support_spares_friendly_outside_blast():
    random.seed(1)
    friendly = _mk("보병1중대", "BLUFOR", "기계화보병", 3_000.0, 3_000.0)  # 반경 밖
    enemy    = _mk("적보병1중대", "OPFOR", "기계화보병", 25_000.0, 25_000.0)
    eng, db = _engine([friendly, enemy])
    eng.apply_air_support_plan({
        "air_support_plans": [{
            "call_sign": "EAGLE-1", "support_type": "strike",
            "target": [10_000, 10_000], "radius": 400, "delay": 0,
        }],
    })
    for _ in range(30):
        eng._tick()
    assert friendly.combat_power == 100.0, "반경 밖 아군은 무피해여야 함"


def test_indirect_fire_damages_friendly_in_aoe():
    random.seed(2)
    # BLUFOR 자주포가 detected 적을 사격 — 같은 편 아군이 표적 AoE 안에 있음
    spg      = _mk("자주포중대", "BLUFOR", "자주포", 8_000.0, 8_000.0, indirect_range=30_000.0)
    enemy    = _mk("적보병1중대", "OPFOR", "기계화보병", 16_000.0, 16_000.0)
    friendly = _mk("보병1중대", "BLUFOR", "기계화보병", 16_050.0, 16_050.0)  # 적 표적 바로 옆
    eng, db = _engine([spg, enemy, friendly])
    eng.full_recon = True
    for _ in range(60):
        eng._tick()
    assert friendly.combat_power < 100.0, "표적 AoE 내 아군이 오사 피해를 입어야 함"
    events = db.get_recent_events(n=300)
    assert any(e["event_type"] == "FRATRICIDE_INDIRECT" for e in events)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_engine_fratricide.py -v`
Expected: FAIL (현재 아군 무피해, FRATRICIDE 이벤트 없음).

- [ ] **Step 3: 구현 — 공중지원 아군 오사**

`engine.py` `_resolve_air_support`에서 적 대상 루프 `for u in targets:` **바로 다음**(즉 `# OPFOR 공중지원 피격 → 재계획 콜백` 주석/`if blufor_hit_this_tick ...` 앞)에 삽입:

```python
            # ── 아군 오사(fratricide): 같은 편 부대도 반경 내면 동일 피해 ──
            for f in [u2 for u2 in self.units if u2.side == air.side and u2.is_active()]:
                fdist = math.hypot(f.x - air.target_x, f.y - air.target_y)
                if fdist > air.radius:
                    continue
                fprox  = 1.0 - fdist / air.radius
                fcover = terrain.cover_factor(f.x, f.y) * 0.5
                fraw   = (air.damage_rate * fprox * (1.0 - fcover) * eff_dt_h) * random.uniform(0.7, 1.3)
                fmin   = 30.0 * fprox * (1.0 - fcover * 0.5) * (eff_dt / air.duration)
                fdmg   = max(fraw, fmin)
                _cp_before_ff = f.combat_power
                f.combat_power = max(0.0, f.combat_power - fdmg)
                self._check_blufor_cp_threshold(f, _cp_before_ff)
                if fdmg >= 3.0:
                    self.db.log_event(
                        self.tick, self.game_time, "FRATRICIDE_AIR",
                        f"[{air.side}] {air.call_sign}⚠아군오사→{f.id}: -{fdmg:.1f}% CP "
                        f"(거리{fdist/1000:.1f}km)",
                    )
```

- [ ] **Step 4: 구현 — 간접사격 아군 오사**

`engine.py` `_resolve_indirect_fire`에서 적 대상 루프 `for enemy in enemies:` 종료 직후,
`if hit_any:` 줄 **앞**에 삽입:

```python
            # ── 아군 오사(fratricide): 같은 편 부대도 AoE 내면 동일 피해 (자기 자신 제외) ──
            for f in [u2 for u2 in self.units
                      if u2.side == spg.side and u2.is_active() and u2.id != spg.id]:
                fdist = math.hypot(f.x - cx, f.y - cy)
                if fdist > aoe_radius:
                    continue
                fprox  = 1.0 - fdist / aoe_radius
                fcover = terrain.cover_factor(f.x, f.y) * 0.4
                fmatch = _matchup_factor(spg.unit_type, f.unit_type)
                fdmg   = (
                    spg.effective_firepower() / 100.0
                    * BASE_ATTRITION_RATE
                    * fprox * (1.0 - fcover) * fmatch * fp_mult * spg_fire_degrade * dt_h
                ) * random.uniform(0.6, 1.4)
                _cp_before_ff = f.combat_power
                f.combat_power = max(0.0, f.combat_power - fdmg)
                self._check_blufor_cp_threshold(f, _cp_before_ff)
                ff_key = (spg.id, f.id)
                ff_acc = self._indirect_accum.get(ff_key, 0.0) + fdmg
                if ff_acc >= _INDIRECT_LOG_THRESHOLD:
                    self._indirect_accum[ff_key] = 0.0
                    self.db.log_event(
                        self.tick, self.game_time, "FRATRICIDE_INDIRECT",
                        f"{spg.id}(자주포)⚠아군오사→{f.id}({f.unit_type}): -{ff_acc:.1f}% CP 누적 "
                        f"(AoE반경{aoe_radius:.0f}m)",
                    )
                elif fdmg > 0:
                    self._indirect_accum[ff_key] = ff_acc
```

- [ ] **Step 5: 구현 — 대포병 탐지확률 상향**

`engine.py`의 `_COUNTER_BATTERY_DETECT_PROB = 0.35` 를 `0.55` 로 변경:

```python
_COUNTER_BATTERY_DETECT_PROB   = 0.55   # 사격 시 정확 위치(detected) 포착 확률(틱당)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_engine_fratricide.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: 커밋**

```bash
git add src/c2/application/simulation/engine.py tests/application/test_engine_fratricide.py
git commit -m "feat(engine): 아군 오사(공중지원·간접사격 반경 내 아군 피해) + 대포병 탐지 강화"
```

---

### Task 3: 공격위치 툴 — 아군 오사 위험 플래그

**Files:**
- Modify: `src/c2/presentation/tools/wargame_attack_advisor_tool.py`
- Test: `tests/presentation/test_attack_advisor_friendly_fire.py` (신규)

**Interfaces:**
- Consumes: 기존 `get_optimal_attack_positions`, `air_support_schedule`/`artillery_support_schedule` 빌드부, `blufor_active`(state units dict list).
- Produces: `_friendly_fire_risk(method, tx, ty, blufor_active) -> dict`; 각 스케줄 항목의 `friendly_fire_risk` 필드.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/presentation/test_attack_advisor_friendly_fire.py` 생성:

```python
"""공격위치 툴: 아군 오사 위험 플래그 헬퍼."""

from c2.presentation.tools.wargame_attack_advisor_tool import _friendly_fire_risk


def test_friendly_fire_risk_in_blast():
    blufor = [{"id": "보병1중대", "x": 10_000, "y": 10_000},
              {"id": "전차중대",  "x": 13_000, "y": 10_000}]
    # cas 반경 1500 → 보병1중대(500m) 위험, 전차중대(2500m) 안전
    r = _friendly_fire_risk("cas", 10_500, 10_000, blufor)
    assert r["blast_radius_m"] == 1_500
    assert r["in_blast"] is True
    ids = [e["unit_id"] for e in r["endangered_units"]]
    assert "보병1중대" in ids and "전차중대" not in ids
    assert r["endangered_units"][0]["dist_m"] == 500


def test_friendly_fire_risk_clear():
    blufor = [{"id": "보병1중대", "x": 10_000, "y": 10_000}]
    r = _friendly_fire_risk("strike", 15_000, 15_000, blufor)  # 반경 400, 멀리
    assert r["in_blast"] is False
    assert r["endangered_units"] == []


def test_friendly_fire_risk_artillery_radius():
    blufor = [{"id": "보병1중대", "x": 10_000, "y": 10_000}]
    # artillery 반경 2500 → 2000m 떨어진 아군 위험
    r = _friendly_fire_risk("artillery", 12_000, 10_000, blufor)
    assert r["blast_radius_m"] == 2_500
    assert r["in_blast"] is True
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/presentation/test_attack_advisor_friendly_fire.py -v`
Expected: FAIL (`_friendly_fire_risk` 미정의 ImportError).

- [ ] **Step 3: 구현 — import + 헬퍼 추가**

`wargame_attack_advisor_tool.py` 상단 import에 추가(기존 `from c2.domain.wargame.coordinates import xy_to_latlon` 아래):

```python
from c2.domain.wargame.coordinates import xy_to_latlon
from c2.domain.wargame.unit import AIR_SUPPORT_PRESETS
```

모듈 함수로 헬퍼 추가(`register_wargame_engine` 아래, `_engagement_factor` 앞 등 적당한 위치):

```python
def _friendly_fire_risk(method: str, tx: float, ty: float, blufor_active: list) -> dict:
    """표적(tx,ty)에 method 화력 투사 시 폭발 반경 내 활성 BLUFOR 부대를 산출.

    blast_radius_m = AIR_SUPPORT_PRESETS[method].radius (cas1500/strike400/artillery2500/helicopter1000).
    """
    radius = AIR_SUPPORT_PRESETS.get(method, AIR_SUPPORT_PRESETS["cas"])["radius"]
    endangered = []
    for u in blufor_active:
        d = math.hypot(float(u.get("x", 0)) - tx, float(u.get("y", 0)) - ty)
        if d <= radius:
            endangered.append({"unit_id": u["id"], "dist_m": int(d)})
    endangered.sort(key=lambda e: e["dist_m"])
    return {
        "blast_radius_m": int(radius),
        "in_blast": bool(endangered),
        "endangered_units": endangered,
    }
```

- [ ] **Step 4: 구현 — 공중지원 스케줄에 위험 필드 부가**

`get_optimal_attack_positions`의 `air_support_schedule.append({...})` 블록을 교체(위 `method`/`reason` 계산 직후):

```python
            ff = _friendly_fire_risk(method, tx, ty, blufor_active)
            if ff["in_blast"]:
                reason = f"⚠️ 아군 {len(ff['endangered_units'])}개 오사 위험 — " + reason
            air_support_schedule.append({
                "priority": i,
                "target_unit_id": tgt["unit_id"],
                "target_type": tgt.get("unit_type") or "미확인",
                "target": [t_lat, t_lon],
                "method": method,
                "reason": reason,
                "friendly_fire_risk": ff,
            })
```

- [ ] **Step 5: 구현 — 포병 스케줄에 위험 필드 부가**

`artillery_support_schedule.append({...})` 블록을 교체:

```python
            ff = _friendly_fire_risk("artillery", tgt["known_x"], tgt["known_y"], blufor_active)
            arty_reason = (f"위협도 상위 {i} — 공중지원과 동일 좌표 동시 포병 투사(화력 집중), "
                           f"전투력 {tgt.get('combat_power')}")
            if ff["in_blast"]:
                arty_reason = f"⚠️ 아군 {len(ff['endangered_units'])}개 오사 위험 — " + arty_reason
            artillery_support_schedule.append({
                "priority": i,
                "target_unit_id": tgt["unit_id"],
                "target_type": tgt.get("unit_type") or "미확인",
                "target": [t_lat, t_lon],
                "method": "artillery",
                "concurrent_with_air": True,
                "reason": arty_reason,
                "friendly_fire_risk": ff,
            })
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/presentation/test_attack_advisor_friendly_fire.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: 커밋**

```bash
git add src/c2/presentation/tools/wargame_attack_advisor_tool.py tests/presentation/test_attack_advisor_friendly_fire.py
git commit -m "feat(tool): 공격위치 추천에 아군 오사 위험(friendly_fire_risk) 반영"
```

---

### Task 4: 결정성 골든 재생성

Task 1의 좌표 변경으로 `_CONTACT_PLAN`(구 OPFOR 좌표)이 무의미해지고, Task 1~2 변경으로 골든이 깨진다. 접적 좌표를 신규 OPFOR 좌표로 갱신하고 골든을 재생성한다.

**Files:**
- Modify: `tests/characterization/test_engine_determinism.py`
- Regenerate: `tests/characterization/engine_900tick_seed42.json`

**Interfaces:**
- Consumes: Task 1의 신규 OPFOR 좌표, Task 2의 오사 로직.

- [ ] **Step 1: `_CONTACT_PLAN` 좌표를 신규 OPFOR 좌표로 갱신**

`test_engine_determinism.py`의 `_CONTACT_PLAN`을 교체(각 표적의 신규 좌표):

```python
_CONTACT_PLAN = {
    "mission_plans": [
        {"company_id": "전차중대", "mission_type": "attack", "target_unit_id": "적전차중대",
         "waypoints": [[21000, 20000]]},
        {"company_id": "보병1중대", "mission_type": "attack", "target_unit_id": "적보병1중대",
         "waypoints": [[20000, 19000]]},
        {"company_id": "보병2중대", "mission_type": "attack", "target_unit_id": "적보병2중대",
         "waypoints": [[19000, 20500]]},
        {"company_id": "보병3중대", "mission_type": "attack", "target_unit_id": "적보병3중대",
         "waypoints": [[18500, 18500]]},
        {"company_id": "대전차중대", "mission_type": "attack", "target_unit_id": "적대전차중대",
         "waypoints": [[21500, 21500]]},
    ],
}
```

- [ ] **Step 2: 기존 골든 삭제**

Run: `rm tests/characterization/engine_900tick_seed42.json`

- [ ] **Step 3: 골든 재생성 (테스트 1회 실행 시 자동 생성)**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py -v`
Expected: PASS — `test_engine_snapshot_is_stable`이 파일 없으면 새로 기록(early return), `test_engine_is_deterministic_under_fixed_seed`는 a==b 통과. 골든 파일이 생성됨.

- [ ] **Step 4: 재생성된 골든으로 재확인 (이제 비교 경로)**

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py -v`
Expected: PASS (2 passed) — 이번엔 골든이 존재하므로 실제 비교. 신규 골든과 일치.

- [ ] **Step 5: 골든이 실제 교전을 반영하는지 sanity 확인**

Run: `PYTHONPATH=src python3 -c "import json; d=json.load(open('tests/characterization/engine_900tick_seed42.json')); cps=[r[3] for r in d]; print('CP 범위:', min(cps), '~', max(cps), '| 100미만(교전발생):', any(c<100 for c in cps))"`
Expected: `100미만(교전발생): True` — 900틱 동안 실제 피해가 누적됨(속도 상향으로 접적이 더 잘 일어남). 만약 False면 접적 실패 — Task 1 좌표/속도 재확인.

- [ ] **Step 6: 커밋**

```bash
git add tests/characterization/test_engine_determinism.py tests/characterization/engine_900tick_seed42.json
git commit -m "test(characterization): 신규 배치·속도·오사 반영해 900틱 골든 재생성"
```

---

### Task 5: 통합 검증 (전체 회귀 + import-linter)

**Files:** (검증 전용, 코드 변경 없음)

- [ ] **Step 1: 신규 테스트 전체 실행**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_scenario_realism.py tests/application/test_engine_fratricide.py tests/presentation/test_attack_advisor_friendly_fire.py tests/characterization/test_engine_determinism.py -v`
Expected: 전부 PASS.

- [ ] **Step 2: 전체 스위트 회귀**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: 신규 실패 없음. `test_mission_apply`/`test_web_api_contract`/session/harness 등 기존 테스트 통과. 만약 좌표·속도에 의존하던 기존 테스트가 깨지면, 그 테스트가 특정 좌표를 하드코딩했는지 확인 후 신규 값으로 갱신(단, 계약 의미는 보존).

- [ ] **Step 3: import-linter 계약 확인**

Run: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`
Expected: `Contracts: 3 kept, 0 broken.`

- [ ] **Step 4: (검증만 통과 시 커밋 불필요)**

회귀 수정이 필요했다면 그 수정만 별도 커밋.

---

## Self-Review

**1. Spec coverage:**
- 스펙 A-1(속도) → Task 1 Step 3/5. A-2(구역·좌표) → Task 1 Step 4/5. A-3(포병 사거리·대포병) → Task 1 Step 5(indirect_range) + Task 2 Step 5(대포병 확률).
- 스펙 B-1(공중지원 오사) → Task 2 Step 3. B-2(간접사격 오사) → Task 2 Step 4. 로그 타입/threshold/미적용 항목 모두 반영.
- 스펙 C(툴 friendly_fire_risk) → Task 3. 반경 프리셋·경고 부가·자동전환 없음 반영.
- 스펙 D(골든 재생성) → Task 4. `_CONTACT_PLAN` 좌표 갱신 포함.
- 스펙 6(테스트 계획) → 각 Task 테스트 + Task 5 회귀.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드/명령/기대출력 포함. "TBD/적절히" 없음.

**3. Type consistency:** `_friendly_fire_risk(method, tx, ty, blufor_active)` 시그니처가 Task 3 Step 3 정의와 Step 4/5 호출에서 일치. `FRATRICIDE_AIR`/`FRATRICIDE_INDIRECT` 이벤트 타입이 engine(Task 2)·테스트(Task 2 Step 1)에서 일치. indirect_range 값(15_000/18_000)이 scenario(Task 1)·test_scenario_realism(Task 1 Step 1)에서 일치. `_CONTACT_PLAN` 신규 좌표가 Task 1 OPFOR 좌표와 일치.
