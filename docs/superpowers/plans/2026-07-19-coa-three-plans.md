# 공격 3-COA 생성 + 지도 프리뷰 + 클릭 실행 + 채팅 수정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 공격 임무계획 버튼이 즉시 실행 대신 3개 COA를 생성하고, 전술채팅에 COA1~3 버튼을 띄워 hover 시 지도 프리뷰·클릭 시 실행·채팅으로 수정하게 한다.

**Architecture:** 규칙기반 3-COA 생성을 결정적 백본으로 두고(LLM 가용 시 각 COA를 LLM으로 대체), 생성 시 엔진 미적용·`session.pending_coas`에 저장, 클릭 시에만 실행. 프리뷰 좌표는 백엔드가 위경도로 변환해 전달, 프론트는 전용 레이어로 렌더.

**Tech Stack:** Python 3.9+, FastAPI, HTML/Leaflet, pytest.

## Global Constraints

- 대상: `src/c2/application/simulation/session.py`, `src/c2/application/simulation/replan.py`, `src/c2/application/agent/mission_planner.py`, `src/c2/presentation/web/api.py`, `ui/dashboard/index.html`, 신규 테스트.
- 계층 규칙: import-linter 3 kept/0 broken (`PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`). application은 domain+포트만; 좌표변환은 `c2.domain.wargame.coordinates.xy_to_latlon`.
- 테스트: `PYTHONPATH=src python3 -m pytest <경로> -v` (시스템 python3=3.9.6; bare `python` 없음).
- COA 생성 시 **엔진 미적용**(즉시 실행 금지). 실행은 `execute_coa`만.
- 3개 COA는 서로 **구별**되어야 하고 각 plan은 `c2.domain.planning.mission_plan.validate_mission_plan`을 통과해야 함.
- LLM 경로(COA 대체·채팅 수정)는 에이전트 가용 시에만; 규칙기반 백본은 항상 동작.

---

### Task 1: 세션 pending_coas 상태

**Files:**
- Modify: `src/c2/application/simulation/session.py`
- Test: `tests/application/test_pending_coas.py` (신규)

**Interfaces:**
- Produces: `WargameSession.pending_coas` (list), `set_pending_coas(list)`, `clear_pending_coas()`; `reset()`에서 clear.

- [ ] **Step 1: 실패 테스트**

`tests/application/test_pending_coas.py`:

```python
"""세션 pending_coas 상태."""
from c2.composition.container import build_session


def test_pending_coas_set_get_clear():
    s = build_session()
    assert s.pending_coas == []
    s.set_pending_coas([{"id": "COA1"}, {"id": "COA2"}])
    assert len(s.pending_coas) == 2
    s.clear_pending_coas()
    assert s.pending_coas == []
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_pending_coas.py -v`
Expected: FAIL (`pending_coas` 속성 없음).

- [ ] **Step 3: 구현**

`session.py` `WargameSession.__init__`에 필드 추가(다른 상태 근처):

```python
        self._pending_coas: list = []
```

메서드 추가(클래스 내 적당한 위치, 예: `get_state` 근처):

```python
    @property
    def pending_coas(self) -> list:
        return self._pending_coas

    def set_pending_coas(self, coas: list) -> None:
        self._pending_coas = list(coas or [])

    def clear_pending_coas(self) -> None:
        self._pending_coas = []
```

`reset(self, units=None)` 본문에 clear 추가(엔진 reset 후):

```python
        self._pending_coas = []
```

- [ ] **Step 4: 통과 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_pending_coas.py -v`
Expected: PASS.

```bash
git add src/c2/application/simulation/session.py tests/application/test_pending_coas.py
git commit -m "feat(session): pending_coas 상태(set/get/clear/reset)"
```

---

### Task 2: COA 프리뷰 빌더 (순수 함수)

**Files:**
- Modify: `src/c2/application/simulation/replan.py`
- Test: `tests/application/test_coa_preview.py` (신규)

**Interfaces:**
- Produces: `build_coa_preview(plan: dict, state: dict) -> dict` — `{"routes":[{"unit_id","color","latlon":[[lat,lon],...]}], "air_support":[{"call_sign","support_type","target":[lat,lon],"radius"}]}`.

- [ ] **Step 1: 실패 테스트**

`tests/application/test_coa_preview.py`:

```python
"""COA 프리뷰: 계획(미터) → 위경도 routes/air."""
from c2.application.simulation.replan import build_coa_preview


def _state():
    return {"units": [
        {"id": "보병1중대", "side": "BLUFOR", "x": 8000, "y": 8000, "color": "#1E88E5"},
    ]}


def test_build_coa_preview_converts_to_latlon():
    plan = {
        "mission_plans": [
            {"company_id": "보병1중대", "mission_type": "attack",
             "waypoints": [[12000, 12000], [15000, 15000]]},
        ],
        "air_support_plans": [
            {"call_sign": "EAGLE-1", "support_type": "cas",
             "target": [15000, 15000], "radius": 1500},
        ],
    }
    pv = build_coa_preview(plan, _state())
    assert len(pv["routes"]) == 1
    r = pv["routes"][0]
    assert r["unit_id"] == "보병1중대"
    # 현위치(8000,8000) + waypoint 2개 = 3점, 각 [lat,lon]
    assert len(r["latlon"]) == 3
    assert all(len(p) == 2 for p in r["latlon"])
    assert pv["air_support"][0]["call_sign"] == "EAGLE-1"
    assert len(pv["air_support"][0]["target"]) == 2
    assert pv["air_support"][0]["radius"] == 1500


def test_build_coa_preview_empty():
    pv = build_coa_preview({"mission_plans": []}, _state())
    assert pv["routes"] == [] and pv["air_support"] == []
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_coa_preview.py -v`
Expected: FAIL (`build_coa_preview` 미정의).

- [ ] **Step 3: 구현**

`replan.py` 상단 import에 추가:

```python
from c2.domain.wargame.coordinates import xy_to_latlon
```

모듈 함수 추가(파일 내 적당한 위치):

```python
def build_coa_preview(plan: dict, state: dict) -> dict:
    """COA plan(미터 좌표) → 지도 프리뷰용 위경도 데이터.
    routes: 각 부대 현재위치+waypoints, air_support: 공중지원 목표/반경. 순수 함수."""
    units_by_id = {u["id"]: u for u in state.get("units", [])}
    routes = []
    for mp in plan.get("mission_plans", []):
        uid = mp.get("company_id")
        u = units_by_id.get(uid)
        latlon = []
        if u is not None:
            latlon.append(list(xy_to_latlon(u.get("x", 0), u.get("y", 0))))
        for wp in mp.get("waypoints", []):
            if isinstance(wp, (list, tuple)) and len(wp) >= 2:
                latlon.append(list(xy_to_latlon(wp[0], wp[1])))
            elif isinstance(wp, dict):
                latlon.append(list(xy_to_latlon(wp.get("x", 0), wp.get("y", 0))))
        routes.append({
            "unit_id": uid,
            "color": (u.get("color") if u else None) or "#40aaff",
            "latlon": latlon,
        })
    air = []
    for sp in plan.get("air_support_plans", []):
        tgt = sp.get("target", [0, 0])
        if isinstance(tgt, dict):
            tx, ty = tgt.get("x", 0), tgt.get("y", 0)
        else:
            tx, ty = tgt[0], tgt[1]
        alat, alon = xy_to_latlon(tx, ty)
        air.append({
            "call_sign": sp.get("call_sign", ""),
            "support_type": sp.get("support_type", "cas"),
            "target": [alat, alon],
            "radius": sp.get("radius", 1500),
        })
    return {"routes": routes, "air_support": air}
```

- [ ] **Step 4: 통과 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_coa_preview.py -v`
Expected: PASS (2 passed).

```bash
git add src/c2/application/simulation/replan.py tests/application/test_coa_preview.py
git commit -m "feat(replan): COA 지도 프리뷰 빌더(미터→위경도 routes/air)"
```

---

### Task 3: 규칙기반 3-COA 생성

**Files:**
- Modify: `src/c2/application/agent/mission_planner.py`
- Test: `tests/application/test_rule_based_coas.py` (신규)

**Interfaces:**
- Produces: `build_rule_based_coas(state: dict) -> list[dict]` — 3개 `{"id","label","doctrine","summary","plan"}`, 서로 다른 plan, 각 plan validate 통과.

- [ ] **Step 1: 실패 테스트**

`tests/application/test_rule_based_coas.py`:

```python
"""규칙기반 3-COA: 구별되는 3개 + validate 통과."""
from c2.application.agent.mission_planner import build_rule_based_coas
from c2.domain.planning.mission_plan import validate_mission_plan


def _state():
    units = [
        {"id": "보병1중대", "side": "BLUFOR", "unit_type": "기계화보병", "x": 7000, "y": 6000,
         "combat_power": 100.0, "status": "active", "color": "#1E88E5"},
        {"id": "전차중대", "side": "BLUFOR", "unit_type": "전차", "x": 6000, "y": 7000,
         "combat_power": 100.0, "status": "active", "color": "#00BCD4"},
        {"id": "적보병1중대", "side": "OPFOR", "unit_type": "기계화보병", "x": 20000, "y": 19000,
         "combat_power": 100.0, "status": "active", "color": "#E53935"},
    ]
    return {
        "units": units,
        "intelligence": {"BLUFOR": [
            {"unit_id": "적보병1중대", "status": "detected", "known_x": 20000, "known_y": 19000,
             "unit_type": "기계화보병", "combat_power": 100.0, "detected_by": "보병1중대"}]},
        "control_points": [
            {"id": "통제-알파", "x": 12000, "y": 14000, "owner": None},
            {"id": "통제-브라보", "x": 15000, "y": 15000, "owner": None},
            {"id": "통제-찰리", "x": 14000, "y": 12000, "owner": None}],
        "air_use_count": {"BLUFOR": 0}, "air_use_limit": 5,
    }


def test_three_distinct_valid_coas():
    coas = build_rule_based_coas(_state())
    assert len(coas) == 3
    ids = [c["id"] for c in coas]
    assert ids == ["COA1", "COA2", "COA3"]
    # 각 plan validate 통과
    for c in coas:
        validate_mission_plan(c["plan"])
        assert c["plan"]["mission_plans"], f"{c['id']} 비어있음"
    # 서로 다른 계획(최소 waypoint 목표가 다름)
    sig = [str(c["plan"]["mission_plans"]) for c in coas]
    assert len(set(sig)) == 3, "3개 COA가 서로 달라야 함"


def test_coas_have_labels_and_summary():
    coas = build_rule_based_coas(_state())
    for c in coas:
        assert c["label"] and c["doctrine"] and c["summary"]
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_rule_based_coas.py -v`
Expected: FAIL (`build_rule_based_coas` 미정의).

- [ ] **Step 3: 구현**

`mission_planner.py`에 모듈 함수 추가(파일 하단, `MissionPlanner` 클래스 밖):

```python
def _coa_air_plans(state: dict, max_n: int) -> list:
    """탐지 OPFOR 상위 max_n개에 공중지원(규칙기반). max_n<=0이면 빈 리스트."""
    if max_n <= 0:
        return []
    air_use = state.get("air_use_count", {})
    air_limit = state.get("air_use_limit", 5)
    remaining = min(max_n, max(0, air_limit - air_use.get("BLUFOR", 0)))
    detected = [e for e in state.get("intelligence", {}).get("BLUFOR", [])
                if e.get("status") == "detected"]
    detected.sort(key=lambda e: e.get("combat_power") or 0, reverse=True)
    call_signs = ["EAGLE-1", "EAGLE-2", "EAGLE-3", "VIPER-1", "VIPER-2"]
    plans = []
    for idx, enemy in enumerate(detected[:remaining]):
        cs = call_signs[idx] if idx < len(call_signs) else f"STRIKE-{idx+1}"
        ut = enemy.get("unit_type", "")
        if any(k in ut for k in ("전차", "장갑", "armor")):
            s_type, radius, delay = "helicopter", 1000, 60
        else:
            s_type, radius, delay = "cas", 1500, 6
        plans.append({"call_sign": cs, "support_type": s_type,
                      "target": [int(enemy["known_x"]), int(enemy["known_y"])],
                      "radius": radius, "delay": delay})
    return plans


def _coa_targets(state: dict):
    """통제구역 목록(없으면 OPFOR 집결점 기반 3점) 반환 — (알파, 브라보, 찰리) 대응."""
    cps = state.get("control_points", [])
    if len(cps) >= 3:
        return [(c["x"], c["y"]) for c in cps[:3]]
    opfor = [u for u in state["units"] if u["side"] == "OPFOR" and u["status"] != "destroyed"]
    if opfor:
        cx = sum(u["x"] for u in opfor) / len(opfor)
        cy = sum(u["y"] for u in opfor) / len(opfor)
    else:
        cx, cy = 15000, 15000
    return [(cx - 2000, cy), (cx, cy), (cx + 2000, cy)]


def build_rule_based_coas(state: dict) -> list:
    """규칙기반 3개 COA(정면 집중 / 측방 기동 / 화력 우선) 생성. 서로 구별됨.
    엔진 미적용 — plan JSON만 반환."""
    blufor = [u for u in state["units"]
              if u["side"] == "BLUFOR" and u["status"] != "destroyed"]
    targets = _coa_targets(state)          # [알파, 브라보, 찰리]
    alpha, bravo, charlie = targets[0], targets[1], targets[2]

    def _attack_plan(u, dst, mid_offset=0.0):
        """부대 u가 dst로 진격하는 mission_plan(중간 waypoint 1 + 목표)."""
        mx = round(u["x"] + (dst[0] - u["x"]) * 0.55)
        my = round(u["y"] + (dst[1] - u["y"]) * 0.55 + mid_offset)
        return {"company_id": u["id"], "mission_type": "attack",
                "waypoints": [[mx, my], [round(dst[0]), round(dst[1])]],
                "objective": f"통제구역 확보 ({int(dst[0])},{int(dst[1])})"}

    def _mk(u, dst, off):
        cp = u["combat_power"]
        if cp < 30:
            return {"company_id": u["id"], "mission_type": "defend",
                    "waypoints": [[round(u["x"]), round(u["y"])]], "objective": "현위치 방어"}
        return _attack_plan(u, dst, off)

    # COA1 정면 집중: 전원 중앙(브라보)로
    coa1 = {"reasoning": "정면 집중 — 통제구역 중앙(브라보) 확보 우선, 기동부대 밀집 진격.",
            "mission_plans": [_mk(u, bravo, 0.0) for u in blufor if u["combat_power"] > 5],
            "air_support_plans": _coa_air_plans(state, 2)}
    # COA2 측방 기동: 절반은 알파(좌), 절반은 찰리(우) — 측방 우회 offset
    plans2 = []
    for i, u in enumerate(blufor):
        if u["combat_power"] <= 5:
            continue
        dst = alpha if i % 2 == 0 else charlie
        plans2.append(_mk(u, dst, 600 if i % 2 == 0 else -600))
    coa2 = {"reasoning": "측방 기동 — 통제구역 측면(알파·찰리)을 좌우로 나눠 우회 확보.",
            "mission_plans": plans2, "air_support_plans": _coa_air_plans(state, 1)}
    # COA3 화력 우선: 공중지원 최대, 기동부대는 중앙으로(보수적 접근)
    coa3 = {"reasoning": "화력 우선 — 공중지원·포병 최대 투사 후 통제구역 진격.",
            "mission_plans": [_mk(u, bravo, 0.0) for u in blufor if u["combat_power"] > 5],
            "air_support_plans": _coa_air_plans(state, 5)}

    return [
        {"id": "COA1", "label": "COA1 · 정면 집중", "doctrine": "frontal",
         "summary": "통제구역 중앙 집중 확보 + 공중지원 2", "plan": coa1},
        {"id": "COA2", "label": "COA2 · 측방 기동", "doctrine": "flank",
         "summary": "좌우 측방 우회로 통제구역 확보 + 공중지원 1", "plan": coa2},
        {"id": "COA3", "label": "COA3 · 화력 우선", "doctrine": "fires",
         "summary": "공중지원 최대 투사 후 중앙 진격", "plan": coa3},
    ]
```

> 주: COA1과 COA3의 mission_plans가 동일해질 수 있으므로(둘 다 bravo), COA3의 첫 부대 목표에 미세 offset을 주어 서명이 달라지게 한다 — COA3의 `_mk` 호출에 `off`를 `100`으로: 위 코드에서 COA3 라인을 `[_mk(u, bravo, 100.0 if i == 0 else 0.0) for i, u in enumerate(...)]` 형태로 바꿔 3개가 반드시 구별되게 하라(테스트 `len(set(sig))==3` 통과 목적). 구현 시 아래로 대체:

```python
    coa3_plans = []
    for i, u in enumerate(blufor):
        if u["combat_power"] <= 5:
            continue
        coa3_plans.append(_mk(u, bravo, 100.0 if i == 0 else 0.0))
    coa3 = {"reasoning": "화력 우선 — 공중지원·포병 최대 투사 후 통제구역 진격.",
            "mission_plans": coa3_plans, "air_support_plans": _coa_air_plans(state, 5)}
```

- [ ] **Step 4: 통과 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_rule_based_coas.py -v`
Expected: PASS (2 passed).

```bash
git add src/c2/application/agent/mission_planner.py tests/application/test_rule_based_coas.py
git commit -m "feat(planner): 규칙기반 3-COA(정면/측방/화력) 생성"
```

---

### Task 4: COA 생성/실행 오케스트레이션

**Files:**
- Modify: `src/c2/application/simulation/replan.py`
- Test: `tests/application/test_coa_orchestration.py` (신규)

**Interfaces:**
- Consumes: Task 1 세션 메서드, Task 2 `build_coa_preview`, Task 3 `build_rule_based_coas`.
- Produces: `generate_attack_coas(session) -> dict` (`{"coas":[...], "history":[...]}`, 엔진 미적용); `execute_coa(session, index) -> dict`.

- [ ] **Step 1: 실패 테스트**

`tests/application/test_coa_orchestration.py`:

```python
"""COA 생성(미적용) + 실행."""
from c2.composition.container import build_session
from c2.application.simulation.replan import generate_attack_coas, execute_coa


def _session_started():
    s = build_session()   # agent=None → 규칙기반 백본
    s.ensure_engine()
    return s


def test_generate_stores_three_coas_without_applying():
    s = _session_started()
    eng = s.ensure_engine()
    # 생성 전 부대 waypoints 비어있음
    before = {u.id: list(u.waypoints) for u in eng.units if u.side == "BLUFOR"}
    res = generate_attack_coas(s)
    assert len(res["coas"]) == 3
    assert all("preview" in c for c in res["coas"])
    assert len(s.pending_coas) == 3
    # 엔진 미적용: BLUFOR waypoints 변화 없음
    after = {u.id: list(u.waypoints) for u in eng.units if u.side == "BLUFOR"}
    assert before == after, "생성 단계에서 엔진에 적용되면 안 됨"


def test_execute_coa_applies_selected():
    s = _session_started()
    eng = s.ensure_engine()
    generate_attack_coas(s)
    res = execute_coa(s, 0)
    assert res["ok"] is True
    # 실행 후 최소 1개 BLUFOR 부대가 waypoints/attack 갱신
    changed = [u for u in eng.units if u.side == "BLUFOR" and (u.waypoints or u.current_action == "attack")]
    assert changed, "COA 실행 시 엔진에 적용돼야 함"
    assert s.pending_coas == [], "실행 후 pending 비움"


def test_execute_coa_bad_index():
    s = _session_started()
    generate_attack_coas(s)
    assert execute_coa(s, 9)["ok"] is False
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_coa_orchestration.py -v`
Expected: FAIL (`generate_attack_coas`/`execute_coa` 미정의).

- [ ] **Step 3: 구현**

`replan.py`에 추가(상단에 `from c2.application.agent.mission_planner import build_rule_based_coas` 필요 시; 이미 `build_mission_query` import 있음):

```python
from c2.application.agent.mission_planner import build_rule_based_coas
```

함수 추가:

```python
_COA_DOCTRINE_HINT = {
    "frontal": "이 COA는 '정면 집중' 교리다. 통제구역 중앙을 최단·집중 확보하도록 공격부대 waypoint를 중앙 통제구역으로 지향하라.",
    "flank":   "이 COA는 '측방 기동' 교리다. 통제구역 좌우 측면을 우회로 나눠 확보하도록 부대를 좌/우로 분리해 측방 waypoint를 구성하라.",
    "fires":   "이 COA는 '화력 우선' 교리다. 공중지원·포병을 최대한 활용하고, 기동부대는 화력 투사 후 통제구역으로 진격하라.",
}


def generate_attack_coas(session) -> dict:
    """공격 COA 3개 생성(엔진 미적용). 규칙기반 백본 + (에이전트 있으면) LLM 대체.
    반환: {"coas": [...], "history": [...]}."""
    history = []
    eng = session.ensure_engine()
    if eng is None:
        return {"coas": [], "history": [("⚔️ COA 생성", "엔진 없음")]}
    # 생성 중 시뮬 일시정지(적용은 안 함)
    was_running = eng.running
    if was_running:
        eng.stop()
    state = eng.get_state()
    coas = build_rule_based_coas(state)   # 결정적 백본

    agent = session.agent
    planner = session.planner
    if agent is not None and planner is not None:
        for coa in coas:
            try:
                query = (build_mission_query(state)
                         + "\n\n" + _COA_DOCTRINE_HINT.get(coa["doctrine"], "")
                         + "\n\n⚠️ 계획(mission_plans/air_support_plans) JSON만 출력하라. "
                           "apply/적용 툴을 호출하지 말 것(엔진 적용 금지, 생성만).")
                agent.reset_memory()
                raw = agent.agent.run(query, reset=True)
                p = planner._parse_json(str(raw))
                if p and p.get("mission_plans"):
                    coa["plan"] = p   # LLM 결과로 대체(미적용)
            except Exception as _e:
                logger.warning("[COA] LLM 생성 실패(%s) → 규칙기반 유지: %s", coa["id"], _e)

    # 프리뷰 부착
    for coa in coas:
        coa["preview"] = build_coa_preview(coa["plan"], state)

    session.set_pending_coas(coas)
    history.append(("⚔️ 공격 COA 3개 생성", f"COA1~3 생성 완료 (엔진 미적용, 버튼 클릭 시 실행)"))
    return {"coas": coas, "history": history}


def execute_coa(session, index: int) -> dict:
    """선택 COA를 엔진에 적용(실행). 성공 시 pending 비움·시뮬 재개."""
    coas = session.pending_coas
    if not coas or index < 0 or index >= len(coas):
        return {"ok": False, "error": "유효하지 않은 COA 인덱스"}
    eng = session.ensure_engine()
    if eng is None:
        return {"ok": False, "error": "엔진 없음"}
    plan = coas[index].get("plan", {})
    try:
        eng.apply_mission_plan(plan)
        if plan.get("air_support_plans"):
            eng.apply_air_support_plan(plan)
        eng.start()   # 시뮬 재개
        label = coas[index].get("id", f"COA{index+1}")
        session.clear_pending_coas()
        return {"ok": True, "executed": label}
    except Exception as e:
        logger.exception("execute_coa 오류")
        return {"ok": False, "error": str(e)}
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_coa_orchestration.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: import-linter + 커밋**

Run: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`
Expected: `Contracts: 3 kept, 0 broken.`

```bash
git add src/c2/application/simulation/replan.py tests/application/test_coa_orchestration.py
git commit -m "feat(replan): COA 3개 생성(미적용·LLM 대체) + 선택 COA 실행"
```

---

### Task 5: 세션 위임 + API 엔드포인트

**Files:**
- Modify: `src/c2/application/simulation/session.py` (위임 메서드)
- Modify: `src/c2/presentation/web/api.py`
- Test: `tests/presentation/test_coa_api.py` (신규)

**Interfaces:**
- Consumes: Task 4 함수.
- Produces: `WargameSession.generate_attack_coas()`/`execute_coa(index)` 위임; `POST /api/mission/attack`(→COA 생성 잡), `POST /api/mission/coa/execute`.

- [ ] **Step 1: 실패 테스트**

`tests/presentation/test_coa_api.py`:

```python
"""COA API 계약."""
from fastapi.testclient import TestClient
from c2.presentation.web.api import create_app


def _client():
    return TestClient(create_app())


def test_coa_execute_endpoint_exists_and_validates():
    c = _client()
    # pending 없을 때 실행 → ok False (404 아님)
    r = c.post("/api/mission/coa/execute", json={"index": 0})
    assert r.status_code == 200
    assert r.json().get("ok") is False


def test_attack_returns_job():
    c = _client()
    r = c.post("/api/mission/attack")
    assert r.status_code == 200
    assert "job_id" in r.json()
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/presentation/test_coa_api.py -v`
Expected: FAIL (`/api/mission/coa/execute` 없음 → 404).

- [ ] **Step 3: 세션 위임 메서드**

`session.py`에 추가(다른 위임 메서드 근처):

```python
    def generate_attack_coas(self) -> dict:
        from c2.application.simulation.replan import generate_attack_coas as _impl
        return _impl(self)

    def execute_coa(self, index: int) -> dict:
        from c2.application.simulation.replan import execute_coa as _impl
        return _impl(self, index)
```

- [ ] **Step 4: API — attack 잡을 COA 생성으로 교체 + execute 엔드포인트**

`api.py`의 `_run_attack_job`을 COA 생성으로 교체(기존 함수 본문 교체):

```python
    def _run_attack_job(jid: str):
        _job_set(jid, "running")
        _job_log(jid, "공격 COA 3개 생성 시작...")
        try:
            result = _get_session().generate_attack_coas()
            coas = result.get("coas", [])
            _job_log(jid, f"COA {len(coas)}개 생성 완료")
            _job_set(jid, "done", {
                "coas": coas,
                "message": f"COA {len(coas)}개 생성 완료 — 버튼 hover로 미리보기, 클릭 시 실행",
                "type": "attack_coa",
            })
        except Exception as e:
            _job_log(jid, f"오류: {e}")
            _job_log(jid, traceback.format_exc())
            _job_set(jid, "error", {"error": str(e)})
```

`ScenarioApplyRequest` 등 모델 근처에 요청 모델 추가:

```python
    class CoaExecuteRequest(BaseModel):
        index: int
```

엔드포인트 추가(`/api/mission/attack` 근처):

```python
    @app.post("/api/mission/coa/execute")
    async def api_coa_execute(req: "CoaExecuteRequest"):
        try:
            return JSONResponse(_get_session().execute_coa(req.index))
        except Exception as e:
            logger.exception("api_coa_execute 오류")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
```

- [ ] **Step 5: 통과 + import-linter + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/presentation/test_coa_api.py -v`
Expected: PASS (2 passed).

Run: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`
Expected: `Contracts: 3 kept, 0 broken.`

```bash
git add src/c2/application/simulation/session.py src/c2/presentation/web/api.py tests/presentation/test_coa_api.py
git commit -m "feat(api): 공격 버튼→COA 3개 생성 잡 + COA 실행 엔드포인트"
```

---

### Task 6: 채팅 COA 수정 (컨텍스트 주입 + coas 응답)

**Files:**
- Modify: `src/c2/application/simulation/replan.py` (`chat_send`)
- Modify: `src/c2/presentation/web/api.py` (`/api/chat` 응답에 coas)
- Test: `tests/application/test_chat_coa_context.py` (신규)

**Interfaces:**
- Consumes: `session.pending_coas`, `build_coa_preview`.
- Produces: `chat_send`가 pending COA 있으면 컨텍스트 주입·수정 반영, 반환 dict에 변경 시 `"coas"` 포함; `/api/chat` 응답이 이를 전달.

- [ ] **Step 1: 실패 테스트**

`tests/application/test_chat_coa_context.py`:

```python
"""채팅 COA 수정 컨텍스트: pending COA가 있으면 응답에 coas 전달(무변경 시 미포함)."""
from c2.application.simulation.replan import _coa_chat_context


def test_coa_chat_context_lists_pending():
    coas = [{"id": "COA1", "label": "COA1 · 정면 집중", "summary": "s1",
             "plan": {"mission_plans": [{"company_id": "보병1중대"}]}}]
    ctx = _coa_chat_context(coas)
    assert "COA1" in ctx and "정면 집중" in ctx
    assert "수정" in ctx  # 수정 지시 포함


def test_coa_chat_context_empty():
    assert _coa_chat_context([]) == ""
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_chat_coa_context.py -v`
Expected: FAIL (`_coa_chat_context` 미정의).

- [ ] **Step 3: 구현 — 컨텍스트 헬퍼 + chat_send 반영**

`replan.py`에 헬퍼 추가:

```python
def _coa_chat_context(coas: list) -> str:
    """pending COA를 채팅 컨텍스트로 직렬화(수정 지시 포함). 없으면 빈 문자열."""
    if not coas:
        return ""
    import json as _j
    lines = []
    for c in coas:
        lines.append(f"- {c.get('id')} ({c.get('label','')}): {c.get('summary','')}\n"
                     f"  plan={_j.dumps(c.get('plan', {}), ensure_ascii=False)}")
    body = "\n".join(lines)
    return (
        "\n\n[현재 생성된 공격 COA 3개 — 사용자가 수정 요청 가능]\n"
        f"{body}\n"
        "사용자가 특정 COA(COA1/COA2/COA3) 수정을 요청하면, 수정된 전체 mission_plans/"
        "air_support_plans JSON을 코드블록으로 출력하고 어느 COA인지 명시하라. "
        "수정이 아니면 일반 전술 답변만 하라.\n"
    )
```

`chat_send`(replan.py:187~)의 구조는: `context` 구성(196~217) → `full_query = context + message`(223) → `response = agent.run(full_query, reset=False)`(224) → `resp_str = str(response)`(225) → `apply_chat_plan_if_any(eng, session.planner, resp_str)`(227, **이미 채팅 계획을 엔진에 즉시 적용**) → `history[-1] = (message, resp_str)` → `return {"history": history}`.

⚠️ **이중적용 방지**: pending COA 수정은 엔진에 적용하면 안 되므로(클릭 시에만 실행), COA 수정으로 처리한 경우 `apply_chat_plan_if_any`를 **건너뛴다**.

(a) **컨텍스트 주입** — `context` 구성 마지막(217행 이후, `history.append` 앞)에 추가:

```python
        context += _coa_chat_context(session.pending_coas)
```

(b) **응답 처리부 교체** — 226~234행의 `apply_chat_plan_if_any`~`return` 블록을 아래로 교체:

```python
        # pending COA가 있고 응답에 mission_plans JSON이 있으면 → COA 수정(엔진 미적용)
        coas = session.pending_coas
        updated_coas = None
        handled_as_coa_edit = False
        if coas:
            try:
                from c2.application.agent.mission_planner import MissionPlanner as _MP
                parsed = _MP()._parse_json(resp_str)
                if parsed and parsed.get("mission_plans"):
                    idx = 0
                    for k, tag in enumerate(("COA1", "COA2", "COA3")):
                        if tag in resp_str:
                            idx = k
                            break
                    if 0 <= idx < len(coas):
                        coas[idx]["plan"] = parsed
                        coas[idx]["preview"] = build_coa_preview(parsed, eng.get_state())
                        session.set_pending_coas(coas)
                        updated_coas = coas
                        handled_as_coa_edit = True
                        resp_str = resp_str + f"\n\n✏️ {coas[idx]['id']} 수정 반영됨 (버튼 클릭 시 실행)"
            except Exception as _e:
                logger.warning("[COA채팅수정] 파싱 실패(무시): %s", _e)
        # COA 수정이 아니면 기존대로 채팅 계획 즉시 적용
        if not handled_as_coa_edit:
            applied_note = apply_chat_plan_if_any(eng, session.planner, resp_str)
            if applied_note:
                resp_str = resp_str + "\n\n" + applied_note
        history[-1] = (message, resp_str)
    except Exception as e:
        logger.error(f"WG chat error: {e}", exc_info=True)
        history[-1] = (message, f"오류: {e}")
    result = {"history": history}
    if 'updated_coas' in dir() and updated_coas is not None:
        result["coas"] = updated_coas
    return result
```

> 주: `updated_coas`는 `try` 블록 안에서 정의되므로, 예외 시 미정의를 피하려면 `try` 앞(라인 222 근처)에 `updated_coas = None`을 먼저 선언하고, 마지막 `if updated_coas is not None: result["coas"]=updated_coas`로 단순화하라. `mission_plans`만 COA 수정 신호로 본다(포격 지시처럼 air_support_plans만 있는 경우는 기존 즉시적용 유지).

- [ ] **Step 4: `/api/chat` 응답에 coas 전달**

`api.py`의 `/api/chat` 핸들러에서 `chat_send` 결과의 `coas`를 응답에 포함(기존 응답 dict에 조건부 추가):

```python
            result = _get_session().chat_send(req.message, [])
            history = result.get("history", [])
            resp = {"response": history[-1][1] if history else ""}
            if result.get("coas") is not None:
                resp["coas"] = result["coas"]
            return JSONResponse(resp)
```

> 주: 기존 `/api/chat` 핸들러 구조를 파일에서 확인하고 위 형태로 `coas`를 조건부 포함한다. 기존 응답 키는 유지.

- [ ] **Step 5: 통과 + 회귀 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_chat_coa_context.py -v`
Expected: PASS (2 passed).

Run: `PYTHONPATH=src python3 -m pytest tests/application -k "chat or replan" -q`
Expected: 신규 실패 없음.

```bash
git add src/c2/application/simulation/replan.py src/c2/presentation/web/api.py tests/application/test_chat_coa_context.py
git commit -m "feat(chat): pending COA 컨텍스트 주입 + 채팅 수정 반영(coas 응답)"
```

---

### Task 7: 대시보드 — COA 버튼 + hover 프리뷰 + 클릭 실행 + 채팅 갱신

**Files:**
- Modify: `ui/dashboard/index.html`

**Interfaces:**
- Consumes: 잡 결과 `coas`(위경도 프리뷰 포함), `/api/mission/coa/execute`, `/api/chat` 응답 `coas`.

- [ ] **Step 1: 마커 캐시 + renderCoaButtons/Preview 추가**

`ui/dashboard/index.html`의 마커 캐시 선언부(`const cpLayers = {};` 근처)에 추가:

```javascript
let coaPreviewLayers = [];   // COA hover 프리뷰 임시 레이어
let coaButtonsData = [];     // 현재 COA 데이터(프리뷰 포함)
```

채팅 함수 근처에 추가:

```javascript
function clearCoaPreview() {
  for (const l of coaPreviewLayers) { try { map.removeLayer(l); } catch(_){} }
  coaPreviewLayers = [];
}

function renderCoaPreview(preview) {
  clearCoaPreview();
  if (!preview) return;
  for (const r of (preview.routes || [])) {
    if (!r.latlon || r.latlon.length < 2) continue;
    const line = L.polyline(r.latlon, {color: '#ffb300', weight: 3, opacity: 0.9, dashArray: '8,5'}).addTo(map);
    coaPreviewLayers.push(line);
    const end = r.latlon[r.latlon.length - 1];
    const lbl = L.marker(end, {icon: L.divIcon({
      html: `<div style="white-space:nowrap;font-size:10px;font-weight:700;color:#ffb300;text-shadow:0 0 3px #000">▶ ${r.unit_id}</div>`,
      className:'', iconSize:[8,8], iconAnchor:[4,4]})}).addTo(map);
    coaPreviewLayers.push(lbl);
  }
  for (const a of (preview.air_support || [])) {
    const c = L.circle(a.target, {radius: a.radius || 1500, color: '#ff7043', weight: 2,
      fillColor: '#ff7043', fillOpacity: 0.12, dashArray: '4,4'}).addTo(map);
    coaPreviewLayers.push(c);
    const dot = L.marker(a.target, {icon: L.divIcon({
      html: `<div style="white-space:nowrap;font-size:10px;font-weight:700;color:#ff7043;text-shadow:0 0 3px #000">✜ ${a.call_sign} (${a.support_type})</div>`,
      className:'', iconSize:[8,8], iconAnchor:[4,4]})}).addTo(map);
    coaPreviewLayers.push(dot);
  }
}

function renderCoaButtons(coas) {
  coaButtonsData = coas || [];
  clearCoaPreview();
  const box = document.getElementById('chat-messages');
  const wrap = document.createElement('div');
  wrap.className = 'coa-btn-wrap';
  wrap.style.cssText = 'display:flex;flex-direction:column;gap:10px;margin:8px 0';
  coas.forEach((coa, i) => {
    const btn = document.createElement('button');
    btn.className = 'coa-btn';
    btn.style.cssText = 'display:flex;align-items:center;gap:12px;padding:16px 20px;'
      + 'background:#12314f;border:1px solid #2f6ea5;border-radius:8px;color:#cfe8ff;'
      + 'font-size:18px;font-weight:700;cursor:pointer;text-align:left';
    btn.innerHTML = `<span style="font-size:20px">▷</span><span>${coa.label || coa.id}</span>`
      + `<span style="margin-left:auto;font-size:11px;color:#8fb7dd;font-weight:400">${coa.summary||''}</span>`;
    btn.addEventListener('mouseenter', () => renderCoaPreview(coa.preview));
    btn.addEventListener('mouseleave', () => clearCoaPreview());
    btn.addEventListener('click', () => executeCoa(i, coa.label || coa.id, wrap));
    wrap.appendChild(btn);
  });
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
}

async function executeCoa(index, label, wrapEl) {
  clearCoaPreview();
  try {
    const r = await fetch('/api/mission/coa/execute', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({index}),
    });
    const d = await r.json();
    if (d.ok) {
      addChatMessage('bot', `✅ ${label} 실행 — 공격 개시`);
      if (wrapEl) wrapEl.remove();
      coaButtonsData = [];
      await poll(); await fetchStatusText(); await fetchEvents();
    } else {
      addChatMessage('err', `❌ 실행 실패: ${d.error || ''}`);
    }
  } catch(e) {
    addChatMessage('err', `❌ 실행 요청 실패: ${e.message}`);
  }
}
```

- [ ] **Step 2: 잡 완료 시 COA 버튼 렌더**

`pollJob`의 `job.status === 'done'` 처리부(그리고 백그라운드 `bgCheck`의 done 처리부)에서, `res.coas`가 있으면 버튼 렌더. `const res = job.result || {};` 다음, `addChatMessage('bot', ...)` 근처에 추가:

```javascript
        if (res.coas && res.coas.length) {
          addChatMessage('bot', `✅ ${label} — COA ${res.coas.length}개 생성. 버튼에 마우스를 올리면 지도 미리보기, 클릭하면 실행됩니다.`);
          renderCoaButtons(res.coas);
        } else {
          addChatMessage('bot', `✅ ${label} 완료\n\n${msg}`);
        }
```

> 주: 기존 `addChatMessage('bot', ...)` 한 줄을 위 조건 분기로 대체한다(포그라운드 `check`와 백그라운드 `bgCheck` 양쪽).

- [ ] **Step 3: 채팅 응답에 coas 있으면 버튼 갱신**

`sendChat`에서 응답 처리부(`lastBot.textContent = d.response ...`)에 추가:

```javascript
    lastBot.textContent = d.response || '(응답 없음)';
    if (d.coas && d.coas.length) { renderCoaButtons(d.coas); }
```

- [ ] **Step 4: 스모크 확인 + 커밋**

Run: `PYTHONPATH=src python3 -c "
html = open('ui/dashboard/index.html', encoding='utf-8').read()
for s in ['renderCoaButtons', 'renderCoaPreview', 'clearCoaPreview', 'executeCoa', 'coaPreviewLayers', '/api/mission/coa/execute']:
    assert s in html, s
print('COA 대시보드 배선 확인 OK')
"`
Expected: `COA 대시보드 배선 확인 OK`.

```bash
git add ui/dashboard/index.html
git commit -m "feat(ui): COA 버튼 + hover 지도 프리뷰 + 클릭 실행 + 채팅 갱신"
```

---

### Task 8: 통합 검증

**Files:** (검증 전용)

- [ ] **Step 1: 신규 테스트 전체**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_pending_coas.py tests/application/test_coa_preview.py tests/application/test_rule_based_coas.py tests/application/test_coa_orchestration.py tests/presentation/test_coa_api.py tests/application/test_chat_coa_context.py -v`
Expected: 전부 PASS.

- [ ] **Step 2: 전체 스위트 회귀**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: 신규 실패 없음.

- [ ] **Step 3: import-linter**

Run: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`
Expected: `Contracts: 3 kept, 0 broken.`

- [ ] **Step 4: 앱 기동 스모크(선택)**

Run: `PYTHONPATH=src python3 -c "
import os; os.environ.setdefault('C2_AGENT_BACKEND','langgraph')
from fastapi.testclient import TestClient
from c2.presentation.web.api import create_app
c = TestClient(create_app())
r = c.post('/api/mission/attack'); jid = r.json()['job_id']
import time
for _ in range(30):
    j = c.get(f'/api/job/{jid}').json()
    if j['status'] in ('done','error'): break
    time.sleep(0.3)
print('job status:', j['status'])
coas = (j.get('result') or {}).get('coas', [])
print('coas:', len(coas), [c2.get('id') for c2 in coas])
assert len(coas) == 3
# 실행
print('execute:', c.post('/api/mission/coa/execute', json={'index':0}).json())
"`
Expected: `coas: 3 ['COA1','COA2','COA3']` + execute ok.

---

## Self-Review

**1. Spec coverage:** 3-COA 생성(미적용)→Task 3,4. COA 버튼→Task 7. hover 프리뷰/leave 복귀→Task 2(빌더)+7(렌더). 클릭 실행→Task 4(execute)+5(API)+7(UI). 채팅 수정→Task 6. 세션 상태→Task 1.

**2. Placeholder scan:** 코드 스텝에 실제 코드/명령. Task 6의 "파일 열어 확인" 주석은 chat_send/`/api/chat`의 정확한 삽입점 안내(삽입 코드 자체는 제공).

**3. Type consistency:** `build_coa_preview(plan,state)`/`build_rule_based_coas(state)`/`generate_attack_coas(session)`/`execute_coa(session,index)`/`_coa_chat_context(coas)` 시그니처가 정의·호출·테스트에서 일치. COA dict 키(`id/label/doctrine/summary/plan/preview`) 일관. API `coas` 응답 키가 잡·채팅·프론트에서 일치. `xy_to_latlon` 도메인 함수 사용(계층 준수).
