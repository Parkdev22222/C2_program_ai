# 이벤트 자동재계획 COA 버튼화 + 프리뷰/실행 경로 완전일치 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 이벤트 자동 재계획을 COA1/2/3 버튼 방식으로 바꾸고, COA hover 프리뷰(노란선)와 실제 실행 경로(파란 점선)를 완전히 일치시킨다.

**Architecture:** (B) `_stealth_expand_waypoints`가 결정적이고 COA 선택 중 시뮬이 정지이므로, COA 생성 시 waypoint를 미리 확장해 저장하고 실행 시 재확장 없이 적용해 프리뷰=실행을 일치. (A) 자동 재계획이 `generate_attack_coas`를 호출해 COA를 만들고 `auto_plan_status`로 노출, 프론트가 버튼 렌더.

**Tech Stack:** Python 3.9+, FastAPI, HTML/Leaflet, pytest.

## Global Constraints

- 대상: `engine.py`, `replan.py`, `session.py`, `api.py`, `ui/dashboard/index.html`, 신규 테스트.
- import-linter 3 kept/0 broken: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`.
- 테스트: `PYTHONPATH=src python3 -m pytest <경로> -v` (시스템 python3=3.9.6; bare `python` 없음).
- `apply_mission_plan(plan, stealth_expand=True)` 기본값 True → 기존 호출 동작 불변(결정성 골든 영향 없음, 재생성 불필요). `test_engine_is_deterministic_under_fixed_seed`(a==b) + snapshot 은 그대로 통과해야 함.
- COA 선택 중 시뮬은 정지 유지 — 실행(`execute_coa`)만 재개.

---

### Task 1: 엔진 — stealth_expand 파라미터 + expand_plan_waypoints

**Files:**
- Modify: `src/c2/application/simulation/engine.py`
- Test: `tests/application/test_stealth_expand_toggle.py` (신규)

**Interfaces:**
- Produces: `apply_mission_plan(self, plan, stealth_expand: bool = True)`; `expand_plan_waypoints(self, plan) -> dict`.

- [ ] **Step 1: 실패 테스트**

`tests/application/test_stealth_expand_toggle.py`:

```python
"""apply_mission_plan stealth_expand 토글 + expand_plan_waypoints."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _eng(units):
    return WargameEngine(units, db=WargameDB(db_path=Path(tempfile.mkdtemp()) / "s.db"))


def _mk(id, side, x, y):
    return Unit(id=id, side=side, unit_type="기계화보병", x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=[], current_action="hold")


def test_stealth_expand_false_keeps_raw_waypoints():
    b = _mk("보병1중대", "BLUFOR", 8000, 8000)
    # 적을 두어 은밀확장이 실제로 일어날 수 있는 상황(위협원 존재)
    o = _mk("적보병1중대", "OPFOR", 16000, 16000)
    eng = _eng([b, o])
    eng.full_recon = True
    eng._update_intelligence()
    plan = {"mission_plans": [{"company_id": "보병1중대", "mission_type": "attack",
                               "waypoints": [[15000, 15000]]}]}
    eng.apply_mission_plan(plan, stealth_expand=False)
    # 재확장 없이 원본 그대로
    assert b.waypoints == [[15000.0, 15000.0]]


def test_expand_plan_waypoints_does_not_mutate_engine_and_returns_expanded():
    b = _mk("보병1중대", "BLUFOR", 8000, 8000)
    o = _mk("적보병1중대", "OPFOR", 16000, 16000)
    eng = _eng([b, o])
    eng.full_recon = True
    eng._update_intelligence()
    plan = {"mission_plans": [{"company_id": "보병1중대", "mission_type": "attack",
                               "waypoints": [[15000, 15000]]}]}
    before_wp = list(b.waypoints)
    out = eng.expand_plan_waypoints(plan)
    # 엔진 상태 불변(부대 waypoints 변화 없음)
    assert list(b.waypoints) == before_wp
    # 반환 plan의 waypoints는 확장 경로(마지막 WP는 목표 유지)
    ewps = out["mission_plans"][0]["waypoints"]
    assert ewps, "확장 waypoints가 있어야 함"
    assert [round(ewps[-1][0]), round(ewps[-1][1])] == [15000, 15000]  # 목표 유지
    # 원본 plan 불변(deepcopy)
    assert plan["mission_plans"][0]["waypoints"] == [[15000, 15000]]


def test_expand_then_apply_no_reexpand_matches():
    b = _mk("보병1중대", "BLUFOR", 8000, 8000)
    o = _mk("적보병1중대", "OPFOR", 16000, 16000)
    eng = _eng([b, o])
    eng.full_recon = True
    eng._update_intelligence()
    plan = {"mission_plans": [{"company_id": "보병1중대", "mission_type": "attack",
                               "waypoints": [[15000, 15000]]}]}
    expanded = eng.expand_plan_waypoints(plan)
    eng.apply_mission_plan(expanded, stealth_expand=False)
    # 적용된 부대 waypoints == 확장 plan의 waypoints (프리뷰와 실행 일치의 근거)
    applied = [[round(p[0]), round(p[1])] for p in b.waypoints]
    expected = [[round(p[0]), round(p[1])] for p in expanded["mission_plans"][0]["waypoints"]]
    assert applied == expected
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_stealth_expand_toggle.py -v`
Expected: FAIL (`stealth_expand` 인자/`expand_plan_waypoints` 미존재).

- [ ] **Step 3: `apply_mission_plan`에 stealth_expand 파라미터**

`engine.py:404`의 시그니처를 `def apply_mission_plan(self, plan: dict, stealth_expand: bool = True):` 로 변경하고, 확장 라인(424-425)을 조건부로:

```python
                if stealth_expand and u.side == "BLUFOR" and wps:
                    wps = self._stealth_expand_waypoints(u, wps)
```

(`_objective` 캡처·나머지 로직은 그대로 유지.)

- [ ] **Step 4: `expand_plan_waypoints` 메서드 추가**

`apply_mission_plan` 아래에 추가:

```python
    def expand_plan_waypoints(self, plan: dict) -> dict:
        """plan의 BLUFOR mission_plans waypoint를 은밀기동 확장한 새 plan을 반환한다(엔진 상태 불변).
        COA 프리뷰=실행 경로 일치용: 이 결과를 저장해 apply_mission_plan(stealth_expand=False)로 적용하면
        재확장 없이 프리뷰와 동일 경로가 실행된다."""
        import copy
        out = copy.deepcopy(plan)
        with self._lock:
            id_map = {u.id: u for u in self.units}
            for mp in out.get("mission_plans", []):
                u = id_map.get(mp.get("company_id"))
                if u is None or u.side != "BLUFOR":
                    continue
                try:
                    wps = [[float(p[0]), float(p[1])] for p in mp.get("waypoints", [])]
                except Exception:
                    continue
                if wps:
                    mp["waypoints"] = self._stealth_expand_waypoints(u, wps)
        return out
```

- [ ] **Step 5: 통과 + 결정성(a==b) + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_stealth_expand_toggle.py -v`
Expected: PASS (3 passed).

Run: `PYTHONPATH=src python3 -m pytest tests/characterization/test_engine_determinism.py -q`
Expected: PASS (2 passed) — 기본값 True라 골든 불변.

```bash
git add src/c2/application/simulation/engine.py tests/application/test_stealth_expand_toggle.py
git commit -m "feat(engine): apply_mission_plan stealth_expand 토글 + expand_plan_waypoints"
```

---

### Task 2: COA 생성 시 경로 확장 + 실행 시 재확장 없음 + context_hint

**Files:**
- Modify: `src/c2/application/simulation/replan.py`
- Test: `tests/application/test_coa_exact_preview.py` (신규)

**Interfaces:**
- Consumes: Task 1의 `expand_plan_waypoints`/`apply_mission_plan(stealth_expand=False)`.
- Produces: `generate_attack_coas(session, context_hint="")` (COA plan을 확장 저장); `execute_coa`가 재확장 없이 적용 + auto_plan_status coas 비움.

- [ ] **Step 1: 실패 테스트**

`tests/application/test_coa_exact_preview.py`:

```python
"""COA 프리뷰 경로 == 실행 경로 (완전 일치)."""
from c2.composition.container import build_session
from c2.application.simulation.replan import generate_attack_coas, execute_coa
from c2.domain.wargame.coordinates import xy_to_latlon


def test_preview_matches_executed_route():
    s = build_session()   # agent=None → 규칙기반
    eng = s.ensure_engine()
    eng.full_recon = True
    eng._update_intelligence()
    res = generate_attack_coas(s)
    coa = res["coas"][0]
    # 실행
    execute_coa(s, 0)
    try:
        # 실행된 각 BLUFOR 부대의 waypoints(위경도 변환) == COA preview routes(현위치 제외)
        preview_by_unit = {r["unit_id"]: r["latlon"] for r in coa["preview"]["routes"]}
        for u in eng.units:
            if u.side != "BLUFOR" or u.id not in preview_by_unit:
                continue
            pv = preview_by_unit[u.id]              # [현위치, wp1, wp2, ...]
            exec_ll = [list(xy_to_latlon(p[0], p[1])) for p in u.waypoints]
            # preview의 현위치(pv[0]) 이후가 실행 waypoints와 동일해야 함
            assert pv[1:] == exec_ll, f"{u.id}: 프리뷰≠실행\n{pv[1:]}\n{exec_ll}"
    finally:
        eng.stop()


def test_context_hint_accepted():
    s = build_session()
    s.ensure_engine()
    res = generate_attack_coas(s, context_hint="테스트 트리거")
    assert len(res["coas"]) == 3
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_coa_exact_preview.py -v`
Expected: FAIL (context_hint 인자 없음 / 프리뷰≠실행 — execute가 재확장).

- [ ] **Step 3: `generate_attack_coas` — 시그니처 + 확장 저장**

`generate_attack_coas(session)` 시그니처를 `def generate_attack_coas(session, context_hint: str = "") -> dict:` 로 변경.

LLM 쿼리 구성부(`query = (build_mission_query(state) ...)`)에 context_hint를 삽입:

```python
                    query = (build_mission_query(state)
                             + (("\n\n[재계획 트리거]\n" + context_hint) if context_hint else "")
                             + "\n\n" + _COA_DOCTRINE_HINT.get(coa["doctrine"], "")
                             + "\n\n⚠️ 계획(mission_plans/air_support_plans) JSON만 출력하라. "
                               "apply/적용 툴을 호출하지 말 것(엔진 적용 금지, 생성만).")
```

"프리뷰 부착" 블록 **앞**에 확장 저장 추가(기존 `for coa in coas: coa["preview"] = ...` 앞):

```python
    # 프리뷰=실행 경로 완전 일치: 각 COA plan을 미리 은밀기동 확장(엔진 상태 불변) 후 저장
    for coa in coas:
        try:
            coa["plan"] = eng.expand_plan_waypoints(coa["plan"])
        except Exception as _e:
            logger.warning("[COA] waypoint 확장 실패(원본 유지): %s", _e)
```

- [ ] **Step 4: `execute_coa` — 재확장 없음 + auto coas 비움**

`execute_coa`의 적용부를 교체:

```python
    plan = coas[index].get("plan", {})
    try:
        eng.apply_mission_plan(plan, stealth_expand=False)  # 이미 확장된 경로 → 재확장 없이 그대로(프리뷰와 일치)
        if plan.get("air_support_plans"):
            eng.apply_air_support_plan(plan)
        eng.start()   # 시뮬 재개
        label = coas[index].get("id", f"COA{index+1}")
        session.clear_pending_coas()
        try:
            session.auto_plan_status["coas"] = []   # 이벤트 COA 버튼 재출현 방지
        except Exception:
            pass
        return {"ok": True, "executed": label}
    except Exception as e:
        logger.exception("execute_coa 오류")
        return {"ok": False, "error": str(e)}
```

- [ ] **Step 5: 통과 + import-linter + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_coa_exact_preview.py tests/application/test_coa_orchestration.py -v`
Expected: PASS.

Run: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`
Expected: `3 kept, 0 broken`.

```bash
git add src/c2/application/simulation/replan.py tests/application/test_coa_exact_preview.py
git commit -m "feat(replan): COA 경로 미리 확장(프리뷰=실행 일치) + context_hint + execute 재확장 없음"
```

---

### Task 3: 세션 auto_plan_status 필드 + execute_auto_attack_plan → COA 생성

**Files:**
- Modify: `src/c2/application/simulation/session.py`
- Modify: `src/c2/application/simulation/replan.py`
- Test: `tests/application/test_event_coa.py` (신규)

**Interfaces:**
- Consumes: Task 2 `generate_attack_coas(context_hint)`.
- Produces: `auto_plan_status`에 `coas`/`coa_gen_id`; `execute_auto_attack_plan`이 COA 생성·미적용·정지유지.

- [ ] **Step 1: 실패 테스트**

`tests/application/test_event_coa.py`:

```python
"""이벤트 자동 재계획이 COA 3개를 생성(미적용)하고 auto_plan_status로 노출."""
from c2.composition.container import build_session
from c2.application.simulation.replan import execute_auto_attack_plan


def test_detection_event_generates_coas_without_applying():
    s = build_session()   # agent=None → 규칙기반
    eng = s.ensure_engine()
    eng.full_recon = True
    eng._update_intelligence()
    before = {u.id: list(u.waypoints) for u in eng.units if u.side == "BLUFOR"}
    gid0 = s.auto_plan_status.get("coa_gen_id", 0)
    # 탐지 이벤트로 자동 재계획 트리거
    execute_auto_attack_plan(s, "detection", "적보병1중대", "기계화보병", 20000, 19000)
    st = s.auto_plan_status
    assert len(st.get("coas", [])) == 3, "이벤트 시 COA 3개 생성"
    assert st.get("coa_gen_id", 0) == gid0 + 1, "coa_gen_id 증가"
    assert st.get("active") is False, "생성 완료 → active False"
    assert len(s.pending_coas) == 3
    # 엔진 미적용: BLUFOR waypoints 불변
    after = {u.id: list(u.waypoints) for u in eng.units if u.side == "BLUFOR"}
    assert before == after, "이벤트 COA 생성 단계에서 엔진 미적용"
    # 시뮬 정지 유지
    assert eng.running is False
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_event_coa.py -v`
Expected: FAIL (execute_auto_attack_plan이 즉시 적용/coas 미노출).

- [ ] **Step 3: 세션 auto_plan_status 필드**

`session.py`의 `self.auto_plan_status: dict = {"active": False, "message": "", "started_at": 0.0}` 를:

```python
        self.auto_plan_status: dict = {"active": False, "message": "", "started_at": 0.0,
                                       "coas": [], "coa_gen_id": 0}
```

`reset()`에서 auto_plan_status를 초기화하는 부분이 있으면 동일 키를 포함(없으면 생략 — 위 dict가 세션 수명 유지).

- [ ] **Step 4: `execute_auto_attack_plan` 본문 교체 (COA 생성)**

`execute_auto_attack_plan`에서 **시뮬 정지까지는 유지**하고, `planner = session.planner`(약 1054행)부터 함수 끝(다음 top-level `def` 전)까지를 아래로 교체한다. 즉 기존 LLM 생성·적용·재개 로직 전체를 대체:

```python
    # ── 이벤트 재계획 → COA 3개 생성(엔진 미적용, 시뮬 정지 유지) ──
    try:
        res = generate_attack_coas(session, context_hint=trigger_desc)
        coas = res.get("coas", [])
        _auto_plan_status["coas"] = coas
        _auto_plan_status["coa_gen_id"] = _auto_plan_status.get("coa_gen_id", 0) + 1
        _auto_plan_status["message"] = f"{log_tag} — COA 선택 대기"
        logger.info("[자동임무계획] COA %d개 생성 — 사용자 선택 대기(시뮬 정지 유지)", len(coas))
    except Exception as e:
        logger.exception("[자동임무계획] COA 생성 오류 → 시뮬 재개")
        if was_running:
            eng.start()
    finally:
        _auto_plan_status["active"] = False
    # 시뮬 재개하지 않음 — 사용자가 COA 버튼 클릭(execute_coa) 시 재개된다.
    return
```

> 주: `generate_attack_coas`가 replan.py 상단에 이미 정의되어 있으므로 별도 import 불필요(동일 모듈). `trigger_desc`/`log_tag`/`was_running`/`_auto_plan_status`는 함수 상단에서 이미 정의됨. 교체 범위 안의 기존 `current_mission_summary`·프롬프트·`_apply_plan_with_repair`·재개 로직은 모두 제거된다. 함수 경계(다음 `def`)를 파일에서 확인해 정확히 그 앞까지만 교체할 것.

- [ ] **Step 5: 통과 + 회귀 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_event_coa.py -v`
Expected: PASS (1 passed).

Run: `PYTHONPATH=src python3 -m pytest tests/application -k "replan or session or coa or worker" -q`
Expected: 신규 실패 없음.

```bash
git add src/c2/application/simulation/session.py src/c2/application/simulation/replan.py tests/application/test_event_coa.py
git commit -m "feat(replan): 이벤트 자동 재계획을 COA 3개 생성으로 전환(미적용·정지유지·auto_plan_status 노출)"
```

---

### Task 4: API auto_plan_status coas 노출 + 프론트 pollAutoPlan COA 버튼

**Files:**
- Modify: `src/c2/presentation/web/api.py`
- Modify: `ui/dashboard/index.html`
- Test: `tests/presentation/test_auto_plan_coa_api.py` (신규)

**Interfaces:**
- Consumes: Task 3 auto_plan_status coas/coa_gen_id.
- Produces: `/api/auto_plan_status` 응답에 coas/coa_gen_id; `pollAutoPlan`이 새 gen_id 시 COA 버튼 렌더.

- [ ] **Step 1: 실패 테스트**

`tests/presentation/test_auto_plan_coa_api.py`:

```python
"""auto_plan_status API가 coas/coa_gen_id를 노출."""
from fastapi.testclient import TestClient
from c2.presentation.web.api import create_app


def test_auto_plan_status_has_coa_fields():
    c = TestClient(create_app())
    c.get("/api/state")   # 엔진 확보
    r = c.get("/api/auto_plan_status")
    assert r.status_code == 200
    d = r.json()
    assert "coas" in d and "coa_gen_id" in d
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src python3 -m pytest tests/presentation/test_auto_plan_coa_api.py -v`
Expected: FAIL (coas/coa_gen_id 미노출).

- [ ] **Step 3: `/api/auto_plan_status` 응답에 coas 포함**

`api.py`의 `api_auto_plan_status` 핸들러를 확인하고, `session.auto_plan_status`의 `coas`/`coa_gen_id`를 응답에 포함(기존 필드 유지). 예:

```python
    @app.get("/api/auto_plan_status")
    async def api_auto_plan_status():
        try:
            s = _get_session().auto_plan_status
            return JSONResponse({
                "active": s.get("active", False),
                "message": s.get("message", ""),
                "started_at": s.get("started_at", 0.0),
                "coas": s.get("coas", []),
                "coa_gen_id": s.get("coa_gen_id", 0),
            })
        except Exception as e:
            logger.exception("api_auto_plan_status 오류")
            return JSONResponse({"active": False, "message": "", "coas": [], "coa_gen_id": 0})
```

> 주: 기존 핸들러 구조를 파일에서 확인하고 위 형태로 coas/coa_gen_id를 추가한다. 기존 응답 키(active/message/started_at)는 유지.

- [ ] **Step 4: 프론트 `pollAutoPlan` — COA 버튼 렌더**

`ui/dashboard/index.html`의 상태 변수 선언부(예: `let _autoplanActive` 근처)에 추가:

```javascript
let _lastCoaGenId = 0;   // 마지막으로 렌더한 이벤트 COA 세대 id
```

`pollAutoPlan` 함수 안, `const d = await r.json();` 이후(배너 처리 근처)에 COA 렌더 로직 추가:

```javascript
    // 이벤트 재계획으로 생성된 COA가 있으면 버튼 렌더(세대 id로 1회만)
    if (d.coas && d.coas.length && (d.coa_gen_id || 0) !== _lastCoaGenId) {
      _lastCoaGenId = d.coa_gen_id || 0;
      addChatMessage('sys', `⚠️ [이벤트 재계획] ${d.message || ''} — COA를 선택하세요.`);
      renderCoaButtons(d.coas);
    }
```

- [ ] **Step 5: 스모크 + 통과 + 커밋**

Run: `PYTHONPATH=src python3 -m pytest tests/presentation/test_auto_plan_coa_api.py -v`
Expected: PASS.

Run: `PYTHONPATH=src python3 -c "
html=open('ui/dashboard/index.html',encoding='utf-8').read()
for s in ['_lastCoaGenId','coa_gen_id','renderCoaButtons(d.coas)']:
    assert s in html, s
print('이벤트 COA 프론트 배선 확인 OK')
"`
Expected: `이벤트 COA 프론트 배선 확인 OK`.

```bash
git add src/c2/presentation/web/api.py ui/dashboard/index.html tests/presentation/test_auto_plan_coa_api.py
git commit -m "feat(ui): auto_plan_status coas 노출 + 이벤트 재계획 COA 버튼 렌더"
```

---

### Task 5: 통합 검증

**Files:** (검증 전용)

- [ ] **Step 1: 신규 테스트 전체**

Run: `PYTHONPATH=src python3 -m pytest tests/application/test_stealth_expand_toggle.py tests/application/test_coa_exact_preview.py tests/application/test_event_coa.py tests/presentation/test_auto_plan_coa_api.py tests/characterization/test_engine_determinism.py -v`
Expected: 전부 PASS.

- [ ] **Step 2: 전체 스위트 회귀**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: 신규 실패 없음.

- [ ] **Step 3: import-linter**

Run: `PYTHONPATH=src python3 -c "from importlinter.cli import lint_imports_command; lint_imports_command.main(args=[], standalone_mode=False)"`
Expected: `Contracts: 3 kept, 0 broken.`

- [ ] **Step 4: end-to-end 스모크(선택)**

Run: `PYTHONPATH=src python3 -c "
import os; os.environ.setdefault('C2_AGENT_BACKEND','langgraph')
from fastapi.testclient import TestClient
from c2.presentation.web.api import create_app
from c2.application.simulation.replan import execute_auto_attack_plan
c = TestClient(create_app()); c.get('/api/state')
from c2.presentation.web import api as _api
s = _api._get_session(); s.ensure_engine().full_recon=True; s.ensure_engine()._update_intelligence()
execute_auto_attack_plan(s, 'detection', '적보병1중대', '기계화보병', 20000, 19000)
d = c.get('/api/auto_plan_status').json()
print('coas:', len(d['coas']), '| gen_id:', d['coa_gen_id'])
assert len(d['coas'])==3
"`
Expected: `coas: 3 | gen_id: 1`.

---

## Self-Review

**1. Spec coverage:** B(경로 일치)→Task 1(엔진 stealth_expand/expand)+Task 2(생성 확장·실행 재확장없음). A(이벤트 COA)→Task 3(execute_auto_attack_plan+auto_plan_status)+Task 4(API/프론트). context_hint→Task 2. execute_coa auto coas 비움→Task 2. 검증→Task 5.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드/명령. Task 3/4의 "파일 확인" 주석은 함수 경계/기존 핸들러 위치 안내(교체 블록 자체는 완전 제공).

**3. Type consistency:** `apply_mission_plan(plan, stealth_expand=True)`/`expand_plan_waypoints(plan)`/`generate_attack_coas(session, context_hint="")` 시그니처가 정의·호출·테스트에서 일치. `auto_plan_status`의 `coas`/`coa_gen_id` 키가 session·replan·api·프론트에서 일치. `_lastCoaGenId`·`renderCoaButtons` 프론트 재사용.
