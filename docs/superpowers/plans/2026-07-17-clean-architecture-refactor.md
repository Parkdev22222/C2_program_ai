# 클린 아키텍처 리팩토링 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** C2 군사 AI 시스템을 실용형 4계층 클린 아키텍처(domain/application/infrastructure/presentation)로 Strangler 방식 전면 리팩토링한다.

**Architecture:** 새 `src/c2/` 패키지를 신설하여 기존 top-level 패키지와 공존시키고, 안쪽(domain)부터 한 조각씩 옮기며 기존 파일은 re-export shim으로 남겨 항상 실행 가능하게 유지한다. 각 이동은 특성화 테스트와 `import-linter` 의존성 계약으로 검증한다.

**Tech Stack:** Python 3, pytest, import-linter, FastAPI(web_api), rdflib(그래프 RAG), vLLM 클라이언트(EXAONE4), LangGraph/smolagents.

## Global Constraints

- 좌표 단위: **미터(m) 정수** (9000 O, 9 X). 맵 크기 30,000×30,000m (`MAP_MAX = 30_000.0`).
- 동작 보존이 원칙 — 시뮬 로직·전술 알고리즘의 **기능적 변경 금지**.
- 엔진 자동 재계획 **콜백 4종**(`on_new_opfor_detection`, `on_blufor_cp_threshold`, `on_blufor_air_hit`, `on_target_moved`)은 마지막 Slice까지 원형 유지.
- 엔진은 전역 `random` 모듈 사용 → 결정성 테스트는 반드시 `random.seed()` 선행.
- 각 Task는 독립 커밋. Slice 완료 기준: 특성화 테스트 green + import-linter green + 스모크 통과.
- 신규 도메인/애플리케이션 코드는 프레임워크·IO import 금지(포트 경유).
- 작업 브랜치: `refactor/clean-architecture` (이미 생성됨).

---

## 계획 범위 안내

이 문서는 **Slice 0(안전망·죽은 코드 제거)** 와 **Slice 1(domain 추출)** 을 바이트사이즈 TDD로 상세 기술한다. Slice 2~5는 하단에 **task 레벨 개요**로 두며, 각 선행 Slice가 병합되어 실제 코드 구조(특히 god object의 분리 지점)가 드러난 뒤 각자 상세 계획으로 확장한다. 이는 god object 분해의 정확한 seam이 특성화 테스트 이전에는 확정 불가하기 때문이다.

---

## Slice 0 — 안전망 & 죽은 코드 제거

### Task 1: 프로젝트 스캐폴딩 (`src/c2/` 뼈대 + pytest + import-linter)

**Files:**
- Create: `pyproject.toml`
- Create: `tests/conftest.py`
- Create: `.importlinter`
- Create: `src/c2/__init__.py`, `src/c2/domain/__init__.py`, `src/c2/domain/wargame/__init__.py`, `src/c2/domain/ontology/__init__.py`, `src/c2/domain/planning/__init__.py`, `src/c2/application/__init__.py`, `src/c2/application/ports/__init__.py`, `src/c2/infrastructure/__init__.py`, `src/c2/presentation/__init__.py`, `src/c2/composition/__init__.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Produces: `c2` 패키지가 import 가능해진다(`import c2`, `from c2.domain.wargame import ...`). pytest는 `pythonpath = [".", "src"]` 로 루트와 src를 모두 인식.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scaffold.py
def test_c2_package_importable():
    import c2  # noqa: F401
    import c2.domain.wargame  # noqa: F401
    import c2.application.ports  # noqa: F401
    import c2.infrastructure  # noqa: F401
    import c2.presentation  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scaffold.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'c2'`

- [ ] **Step 3: Create pyproject.toml with pytest pythonpath**

```toml
# pyproject.toml
[tool.pytest.ini_options]
pythonpath = [".", "src"]
testpaths = ["tests"]
python_files = ["test_*.py"]
```

- [ ] **Step 4: Create the src/c2 skeleton packages**

Create each `__init__.py` listed in Files (empty files). For example:

```bash
mkdir -p src/c2/domain/wargame src/c2/domain/ontology src/c2/domain/planning \
         src/c2/application/ports src/c2/infrastructure src/c2/presentation src/c2/composition
for d in src/c2 src/c2/domain src/c2/domain/wargame src/c2/domain/ontology \
         src/c2/domain/planning src/c2/application src/c2/application/ports \
         src/c2/infrastructure src/c2/presentation src/c2/composition; do
  touch "$d/__init__.py"
done
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_scaffold.py -v`
Expected: PASS

- [ ] **Step 6: Create conftest.py (runtime sys.path for non-pytest execution parity)**

```python
# tests/conftest.py
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
for p in (_ROOT, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
```

- [ ] **Step 7: Create the import-linter contract**

```ini
# .importlinter
[importlinter]
root_package = c2

[importlinter:contract:layers]
name = C2 clean architecture layers
type = layers
layers =
    c2.presentation
    c2.infrastructure
    c2.application
    c2.domain
```

> 참고: `layers` 계약은 위에서 아래로 의존 허용(상위가 하위를 import 가능), 역방향(하위→상위) 금지. 계층이 비어 있는 현재는 통과하며, 코드가 이동할수록 의미가 생긴다. infrastructure→application(포트 구현)은 별도 Slice 2에서 계약을 정교화한다.

- [ ] **Step 8: Verify import-linter runs**

Run: `python -m pip install import-linter && python -m importlinter lint`
Expected: `Contracts: 1 kept, 0 broken.`

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml tests/conftest.py .importlinter src/c2 tests/test_scaffold.py
git commit -m "chore: src/c2 4계층 스캐폴딩 + pytest·import-linter 설정"
```

---

### Task 2: 죽은 video/PDF-RAG 참조 제거

`tools/videodb_query_tool.py`, `tools/pdf_rag_tool.py`는 존재하지 않는다. `battlefield_agent._build_tools()`의 로드 시도 블록만 남은 죽은 코드다.

**Files:**
- Modify: `agent/battlefield_agent.py:132-148`
- Test: `tests/test_no_dead_tool_refs.py`

**Interfaces:**
- Consumes: 없음
- Produces: 없음 (순수 제거)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_no_dead_tool_refs.py
from pathlib import Path

_AGENT = Path(__file__).resolve().parent.parent / "agent" / "battlefield_agent.py"

def test_no_videodb_or_pdf_rag_imports():
    src = _AGENT.read_text(encoding="utf-8")
    assert "videodb_query_tool" not in src
    assert "pdf_rag_tool" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_no_dead_tool_refs.py -v`
Expected: FAIL (두 문자열이 아직 존재)

- [ ] **Step 3: Remove the two dead try/except blocks**

`agent/battlefield_agent.py`에서 아래 두 블록을 삭제한다 (videodb 블록: `try: from tools.videodb_query_tool import (...)` ~ `logger.warning(f"Failed to load videodb tools: {e}")`, pdf_rag 블록: `try: from tools.pdf_rag_tool import pdf_rag_search, add_pdf_to_rag` ~ `logger.warning(f"Failed to load PDF RAG tools: {e}")`). `graph_rag_tool` 블록은 **유지**한다.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_no_dead_tool_refs.py -v`
Expected: PASS

- [ ] **Step 5: Smoke — agent 모듈 import 성립**

Run: `python -c "import agent.battlefield_agent"`
Expected: 에러 없이 종료 (import 성공)

- [ ] **Step 6: Commit**

```bash
git add agent/battlefield_agent.py tests/test_no_dead_tool_refs.py
git commit -m "refactor: 죽은 videodb·pdf_rag 툴 로드 참조 제거"
```

---

### Task 3: ARMA3 서브시스템 제거

**Files:**
- Delete: `arma3_integration/` (전체), `api/arma3_receiver.py`, `core_src/` (전체), `tools/arma3_order_tool.py`, `tools/arma3_query_tool.py`, `data/arma3_orders.json`, `data/arma3_state.json`
- Modify: ARMA3 툴을 등록/참조하는 코드 (`agent/battlefield_agent.py`, 필요 시 `ui/gradio_app.py`는 Slice 4에서 폐기되므로 참조가 있으면 해당 라인만 제거)
- Test: `tests/test_arma3_removed.py`

**Interfaces:**
- Consumes: 없음
- Produces: `tools→core_src`, `api→core_src` 의존 소멸

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arma3_removed.py
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

def test_arma3_paths_gone():
    for rel in [
        "arma3_integration", "api/arma3_receiver.py", "core_src",
        "tools/arma3_order_tool.py", "tools/arma3_query_tool.py",
        "data/arma3_orders.json", "data/arma3_state.json",
    ]:
        assert not (_ROOT / rel).exists(), f"still exists: {rel}"

def test_no_arma3_imports_in_agent():
    src = (_ROOT / "agent" / "battlefield_agent.py").read_text(encoding="utf-8")
    assert "arma3" not in src.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_arma3_removed.py -v`
Expected: FAIL

- [ ] **Step 3: Find every ARMA3 reference before deleting**

Run: `grep -rniE "arma3" --include=*.py agent tools ui api core_src | grep -v __pycache__`
각 참조가 삭제 대상 파일 안이거나 등록부(제거할 라인)인지 확인한다. 삭제 대상 밖에서 ARMA3에 **기능적으로 의존**하는 코드가 있으면 중단하고 보고한다.

- [ ] **Step 4: Delete the ARMA3 files/dirs**

```bash
git rm -r arma3_integration core_src
git rm api/arma3_receiver.py tools/arma3_order_tool.py tools/arma3_query_tool.py
git rm data/arma3_orders.json data/arma3_state.json
```

- [ ] **Step 5: Remove ARMA3 tool-registration lines in battlefield_agent**

`agent/battlefield_agent.py`에서 `arma3_order_tool`/`arma3_query_tool` import·등록 라인(있다면)을 삭제한다. Step 3의 grep 결과를 근거로 정확한 라인만 제거.

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_arma3_removed.py -v`
Expected: PASS

- [ ] **Step 7: Smoke — 핵심 모듈 import 성립**

Run: `python -c "import agent.battlefield_agent; import tools.wargame_query_tool; import ui.web_api"`
Expected: 에러 없이 종료

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: ARMA3 연동 서브시스템 전면 제거"
```

---

### Task 4: 특성화 테스트 — 엔진 결정성 스냅샷

**Files:**
- Create: `tests/characterization/__init__.py`
- Create: `tests/characterization/test_engine_determinism.py`

**Interfaces:**
- Consumes: `wargame.scenario.setup_bn_vs_bn() -> List[Unit]`, `wargame.engine.WargameEngine(units, db=...)`, `WargameEngine._tick()`, `WargameEngine.get_state() -> dict`, `wargame.models.WargameDB(db_path=Path)`
- Produces: 리팩토링 전후 엔진 동작 동일성을 보장하는 골든 스냅샷

- [ ] **Step 1: Write the characterization test (seed 고정 + N틱 스냅샷)**

```python
# tests/characterization/test_engine_determinism.py
import random
import tempfile
from pathlib import Path

from wargame.scenario import setup_bn_vs_bn
from wargame.engine import WargameEngine
from wargame.models import WargameDB


def _run(seed: int, ticks: int) -> list:
    random.seed(seed)
    units = setup_bn_vs_bn()
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "char.db")
    eng = WargameEngine(units, db=db)
    for _ in range(ticks):
        eng._tick()
    state = eng.get_state()
    # 부대별 (id, 위치, 전투력, 상태)만 뽑아 안정적 스냅샷 구성
    snap = sorted(
        (u["id"], round(u["x"]), round(u["y"]),
         round(u["combat_power"], 1), u["status"])
        for u in state["units"]
    )
    return snap


def test_engine_is_deterministic_under_fixed_seed():
    a = _run(seed=42, ticks=50)
    b = _run(seed=42, ticks=50)
    assert a == b, "동일 시드에서 결과가 달라짐 — 숨은 비결정성"


def test_engine_snapshot_is_stable(snapshot_path=Path(__file__).parent / "engine_50tick_seed42.json"):
    import json
    current = _run(seed=42, ticks=50)
    if not snapshot_path.exists():
        snapshot_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return  # 최초 실행: 골든 생성
    golden = json.loads(snapshot_path.read_text(encoding="utf-8"))
    golden = [tuple(x) for x in golden]
    assert current == golden, "엔진 동작이 골든 스냅샷과 다름 (회귀)"
```

- [ ] **Step 2: Run to generate golden + verify determinism**

Run: `python -m pytest tests/characterization/test_engine_determinism.py -v`
Expected: 최초 실행 시 PASS(골든 생성). 재실행 시 PASS.

- [ ] **Step 3: Confirm golden file committed**

Run: `ls tests/characterization/engine_50tick_seed42.json`
Expected: 파일 존재

- [ ] **Step 4: Commit**

```bash
git add tests/characterization/
git commit -m "test: 엔진 결정성 특성화 스냅샷 (seed42/50tick)"
```

---

### Task 5: 특성화 테스트 — 임무계획 적용

**Files:**
- Create: `tests/characterization/test_mission_apply.py`

**Interfaces:**
- Consumes: `WargameEngine.apply_mission_plan(plan: dict)`, `Unit.to_dict()`, `wargame.scenario.setup_bn_vs_bn`
- Produces: 임무 적용 후 부대 waypoints/target 스냅샷

- [ ] **Step 1: Write the characterization test**

```python
# tests/characterization/test_mission_apply.py
import random
import tempfile
from pathlib import Path

from wargame.scenario import setup_bn_vs_bn
from wargame.engine import WargameEngine
from wargame.models import WargameDB


def _engine():
    random.seed(7)
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "m.db")
    return WargameEngine(setup_bn_vs_bn(), db=db)


def test_mission_plan_sets_waypoints_and_target():
    eng = _engine()
    blu_ids = [u.id for u in eng.units if u.side == "BLUFOR"]
    assert blu_ids, "BLUFOR 부대가 있어야 함"
    company = blu_ids[0]
    plan = {
        "mission_plans": [
            {
                "company_id": company,
                "mission_type": "attack",
                "waypoints": [[9000, 9000], [12000, 12000]],
                "objective": "특성화 테스트",
            }
        ]
    }
    eng.apply_mission_plan(plan)
    u = next(u for u in eng.units if u.id == company)
    assert len(u.waypoints) >= 1, "waypoints가 적용되어야 함"
    assert u.mission_lock_ticks > 0, "임무 잠금이 걸려야 함"
```

- [ ] **Step 2: Run to verify it passes**

Run: `python -m pytest tests/characterization/test_mission_apply.py -v`
Expected: PASS. (필드명이 실제와 다르면 실제 필드로 맞춘 뒤 통과시킨다 — 목적은 현재 동작 고정.)

- [ ] **Step 3: Commit**

```bash
git add tests/characterization/test_mission_apply.py
git commit -m "test: 임무계획 적용 특성화 테스트"
```

---

### Task 6: 특성화 테스트 — web_api `/api/state` 계약

**Files:**
- Create: `tests/characterization/test_web_api_contract.py`

**Interfaces:**
- Consumes: `ui.web_api` FastAPI 앱, 엔드포인트 `/api/state`
- Produces: state 응답 스키마 스냅샷 (키 존재·타입)

- [ ] **Step 1: Write the contract test (스키마 고정)**

```python
# tests/characterization/test_web_api_contract.py
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient


def _client():
    import ui.web_api as web_api
    app = web_api.create_app() if hasattr(web_api, "create_app") else web_api.app
    return TestClient(app)


def test_api_state_schema():
    client = _client()
    r = client.get("/api/state")
    assert r.status_code == 200
    body = r.json()
    for key in ("running", "tick", "units"):
        assert key in body, f"/api/state 응답에 '{key}' 누락"
    assert isinstance(body["units"], list)
    if body["units"]:
        u = body["units"][0]
        for key in ("id", "side", "unit_type", "combat_power"):
            assert key in u, f"unit에 '{key}' 누락"
```

- [ ] **Step 2: Run to verify it passes**

Run: `python -m pytest tests/characterization/test_web_api_contract.py -v`
Expected: PASS (fastapi 미설치 시 skip). 앱 팩토리명이 다르면 `_client()`를 실제 진입점에 맞춘다.

- [ ] **Step 3: Commit**

```bash
git add tests/characterization/test_web_api_contract.py
git commit -m "test: web_api /api/state 계약 특성화 테스트"
```

- [ ] **Step 4: Slice 0 완료 검증 (전체 실행)**

Run: `python -m pytest tests/ -v && python -m importlinter lint`
Expected: 모든 테스트 PASS, `Contracts: 1 kept, 0 broken.`

---

## Slice 1 — domain 추출

패턴(모든 Task 공통): ① 새 `src/c2/domain/...` 모듈에 로직 이동 → ② 기존 파일을 명시적 re-export shim으로 치환 → ③ 기존·신규 양쪽 import가 동일 객체를 가리키는지 테스트 → ④ 전체 특성화 테스트 green 확인 → ⑤ 커밋.

### Task 7: 좌표 유틸 → `domain/wargame/coordinates.py`

**Files:**
- Create: `src/c2/domain/wargame/coordinates.py`
- Modify: `tools/coord_utils.py` (shim으로 치환)
- Test: `tests/domain/test_coordinates.py`

**Interfaces:**
- Produces: `c2.domain.wargame.coordinates` — `xy_to_latlon(x_m, y_m) -> tuple`, `latlon_to_xy(lat, lon) -> tuple`, `waypoints_xy_to_latlon(list) -> list`, `waypoints_latlon_to_xy(list) -> list`, `is_latlon_coords(list) -> bool`
- Consumes: 없음 (순수 함수)

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_coordinates.py
def test_new_module_exports_all_functions():
    from c2.domain.wargame.coordinates import (
        xy_to_latlon, latlon_to_xy, waypoints_xy_to_latlon,
        waypoints_latlon_to_xy, is_latlon_coords,
    )
    lat, lon = xy_to_latlon(0, 0)
    assert isinstance(lat, float) and isinstance(lon, float)


def test_shim_points_to_same_object():
    from c2.domain.wargame.coordinates import xy_to_latlon as new_fn
    from tools.coord_utils import xy_to_latlon as shim_fn
    assert new_fn is shim_fn, "shim이 새 모듈을 재-export해야 함"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/domain/test_coordinates.py -v`
Expected: FAIL (`No module named 'c2.domain.wargame.coordinates'`)

- [ ] **Step 3: Move the implementation into the new module**

`tools/coord_utils.py`의 전체 구현(5개 함수 + 상수)을 `src/c2/domain/wargame/coordinates.py`로 그대로 옮긴다.

- [ ] **Step 4: Replace tools/coord_utils.py with a shim**

```python
# tools/coord_utils.py
"""[shim] 구현은 c2.domain.wargame.coordinates 로 이동됨. (Slice 5에서 제거 예정)"""
from c2.domain.wargame.coordinates import (  # noqa: F401
    xy_to_latlon, latlon_to_xy, waypoints_xy_to_latlon,
    waypoints_latlon_to_xy, is_latlon_coords,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/domain/test_coordinates.py -v`
Expected: PASS

- [ ] **Step 6: Full regression + lint**

Run: `python -m pytest tests/ -v && python -m importlinter lint`
Expected: 전부 PASS, 계약 유지

- [ ] **Step 7: Commit**

```bash
git add src/c2/domain/wargame/coordinates.py tools/coord_utils.py tests/domain/test_coordinates.py
git commit -m "refactor(domain): 좌표 유틸 → c2.domain.wargame.coordinates + shim"
```

---

### Task 8: Unit/AirSupport → `domain/wargame/unit.py`

`wargame/models.py`는 도메인 엔티티(`Unit`, `AirSupport`)와 인프라(`WargameDB`, SQLite)가 섞여 있다. **엔티티만** domain으로 옮기고 `WargameDB`는 파일에 남긴다(Slice 2에서 infrastructure로 이동).

**Files:**
- Create: `src/c2/domain/wargame/unit.py`
- Modify: `wargame/models.py` (Unit/AirSupport를 shim import로, WargameDB는 잔류)
- Test: `tests/domain/test_unit.py`

**Interfaces:**
- Produces: `c2.domain.wargame.unit` — `AirSupport`(@dataclass, `.to_dict()`), `Unit`(@dataclass, `.effective_firepower()->float`, `.is_active()->bool`, `.distance_to(other)->float`, `.to_dict()->dict`, classmethod `from_row(row)->Unit`)
- Consumes: 없음 (순수 dataclass). `Unit`이 `wargame.terrain`을 참조하면 Task 9 완료 후 순환 없이 `c2.domain.wargame.terrain`을 쓰도록 정리.

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_unit.py
def test_unit_and_airsupport_importable_from_domain():
    from c2.domain.wargame.unit import Unit, AirSupport
    assert hasattr(Unit, "effective_firepower")
    assert hasattr(Unit, "is_active")
    assert hasattr(AirSupport, "to_dict")


def test_models_shim_reexports_same_classes():
    from c2.domain.wargame.unit import Unit as NewUnit
    from wargame.models import Unit as ShimUnit
    assert NewUnit is ShimUnit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/domain/test_unit.py -v`
Expected: FAIL

- [ ] **Step 3: Move Unit/AirSupport into the new module**

`wargame/models.py`의 `AirSupport`, `Unit` 클래스(및 그들이 쓰는 상수/헬퍼)를 `src/c2/domain/wargame/unit.py`로 옮긴다. `WargameDB`와 그 상수(`DB_PATH` 등)는 `wargame/models.py`에 남긴다.

- [ ] **Step 4: Add shim imports at top of wargame/models.py**

```python
# wargame/models.py 상단 (WargameDB 정의는 그대로 유지)
from c2.domain.wargame.unit import Unit, AirSupport  # noqa: F401  [shim]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/domain/test_unit.py -v`
Expected: PASS

- [ ] **Step 6: Full regression + lint**

Run: `python -m pytest tests/ -v && python -m importlinter lint`
Expected: 전부 PASS (엔진 결정성 스냅샷 유지 = 동작 보존 확인)

- [ ] **Step 7: Commit**

```bash
git add src/c2/domain/wargame/unit.py wargame/models.py tests/domain/test_unit.py
git commit -m "refactor(domain): Unit/AirSupport → c2.domain.wargame.unit + shim (WargameDB 잔류)"
```

---

### Task 9: 지형 → `domain/wargame/terrain.py`

**Files:**
- Create: `src/c2/domain/wargame/terrain.py`
- Modify: `wargame/terrain.py`, `wargame/terrain_korea.py` (shim으로 치환/통합)
- Test: `tests/domain/test_terrain.py`

**Interfaces:**
- Produces: `c2.domain.wargame.terrain` — `get_heightmap() -> np.ndarray`, `elevation(x, y)`, `elevation_advantage(ax, ay, dx, dy)`, `cover_factor(x, y)`, `movement_speed_factor(x, y)` (현재 `wargame/terrain.py`의 공개 API와 동일 시그니처 유지)
- Consumes: numpy (도메인이나 순수 수치 계산으로 허용)

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_terrain.py
def test_terrain_public_api_from_domain():
    from c2.domain.wargame import terrain
    hm = terrain.get_heightmap()
    assert hm is not None


def test_terrain_shim_reexports():
    from c2.domain.wargame.terrain import get_heightmap as new_fn
    from wargame.terrain import get_heightmap as shim_fn
    assert new_fn is shim_fn
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/domain/test_terrain.py -v`
Expected: FAIL

- [ ] **Step 3: Move terrain implementation into the new module**

`wargame/terrain.py`(+ `terrain_korea.py`의 데이터/로직)를 `src/c2/domain/wargame/terrain.py`로 옮긴다. 데이터 파일 경로 참조가 있으면 절대경로/패키지 상대경로로 보존.

- [ ] **Step 4: Replace wargame/terrain.py with a shim**

```python
# wargame/terrain.py
"""[shim] 구현은 c2.domain.wargame.terrain 로 이동됨."""
from c2.domain.wargame.terrain import *  # noqa: F401,F403
from c2.domain.wargame.terrain import (  # noqa: F401
    get_heightmap,
)
```

> `terrain_korea.py`의 공개 심볼도 동일 방식으로 shim 재-export.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/domain/test_terrain.py -v`
Expected: PASS

- [ ] **Step 6: Full regression + lint**

Run: `python -m pytest tests/ -v && python -m importlinter lint`
Expected: 전부 PASS (엔진 스냅샷 유지 — 지형이 전투 계산에 쓰이므로 동작 보존의 강한 신호)

- [ ] **Step 7: Commit**

```bash
git add src/c2/domain/wargame/terrain.py wargame/terrain.py wargame/terrain_korea.py tests/domain/test_terrain.py
git commit -m "refactor(domain): 지형 계산 → c2.domain.wargame.terrain + shim"
```

---

### Task 10: 온톨로지 엔티티 → `domain/ontology/models.py`

**Files:**
- Create: `src/c2/domain/ontology/models.py`
- Modify: `ontology/models.py` (shim)
- Test: `tests/domain/test_ontology_models.py`

**Interfaces:**
- Produces: `c2.domain.ontology.models` — 현재 `ontology/models.py`의 공개 데이터클래스 전체 (동일 이름·필드)
- Consumes: 표준 라이브러리만 (프레임워크 import 금지 확인)

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_ontology_models.py
import importlib

def test_ontology_models_from_domain_match_shim():
    new = importlib.import_module("c2.domain.ontology.models")
    shim = importlib.import_module("ontology.models")
    public = [n for n in dir(new) if not n.startswith("_")]
    assert public, "공개 심볼이 있어야 함"
    for name in public:
        if isinstance(getattr(new, name), type):
            assert getattr(new, name) is getattr(shim, name, None), f"{name} 불일치"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/domain/test_ontology_models.py -v`
Expected: FAIL

- [ ] **Step 3: Move ontology models into the new module**

`ontology/models.py`의 도메인 데이터클래스를 `src/c2/domain/ontology/models.py`로 옮긴다. IO/프레임워크 의존이 섞여 있으면 그 부분은 남기고 순수 엔티티만 이동한다.

- [ ] **Step 4: Replace ontology/models.py with a shim**

```python
# ontology/models.py
"""[shim] 도메인 엔티티는 c2.domain.ontology.models 로 이동됨."""
from c2.domain.ontology.models import *  # noqa: F401,F403
```

> `import *`가 누락 없이 재-export하도록 새 모듈에 `__all__`을 명시한다.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/domain/test_ontology_models.py -v`
Expected: PASS

- [ ] **Step 6: Full regression (온톨로지 파이프라인 포함) + lint**

Run: `python -m pytest tests/ -v && python -m importlinter lint`
Expected: 전부 PASS (기존 `tests/test_ontology_pipeline.py` 포함)

- [ ] **Step 7: Commit**

```bash
git add src/c2/domain/ontology/models.py ontology/models.py tests/domain/test_ontology_models.py
git commit -m "refactor(domain): 온톨로지 엔티티 → c2.domain.ontology.models + shim"
```

---

### Task 11: 임무계획 값 객체 → `domain/planning/mission_plan.py`

`tools/mission_plan_validator.py`는 **값 객체/스키마**(Pydantic 모델, `MAP_MAX`, `validate_mission_plan`)와 **애플리케이션 상태**(pending plan 세션, `guard_write_tool`, `classify_intent`)가 섞여 있다. **스키마·검증 순수 부분만** domain으로 옮기고 세션/가드/의도분류는 남긴다(Slice 3에서 application으로).

**Files:**
- Create: `src/c2/domain/planning/mission_plan.py`
- Modify: `tools/mission_plan_validator.py` (스키마 부분 shim)
- Test: `tests/domain/test_mission_plan.py`

**Interfaces:**
- Produces: `c2.domain.planning.mission_plan` — `MAP_MAX: float = 30_000.0`, 임무계획 Pydantic 모델, `validate_mission_plan(plan) -> dict`
- Consumes: pydantic (도메인 값 객체 검증 라이브러리로 허용)

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_mission_plan.py
def test_map_max_and_validator_from_domain():
    from c2.domain.planning.mission_plan import MAP_MAX, validate_mission_plan
    assert MAP_MAX == 30_000.0
    result = validate_mission_plan({"mission_plans": []})
    assert isinstance(result, dict)


def test_validator_shim_reexports_same_callable():
    from c2.domain.planning.mission_plan import validate_mission_plan as new_fn
    from tools.mission_plan_validator import validate_mission_plan as shim_fn
    assert new_fn is shim_fn
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/domain/test_mission_plan.py -v`
Expected: FAIL

- [ ] **Step 3: Move schema/validation into the new module**

`MAP_MAX`, Pydantic 모델(중첩 포함), `validate_mission_plan`(및 그것만 쓰는 순수 헬퍼)을 `src/c2/domain/planning/mission_plan.py`로 옮긴다. `save_pending_plan`, `get_pending_plan`, `approve_plan`, `guard_write_tool`, `classify_intent`, `update_valid_company_ids`, 세션 상태는 `tools/mission_plan_validator.py`에 남긴다.

- [ ] **Step 4: Add shim imports in tools/mission_plan_validator.py**

```python
# tools/mission_plan_validator.py 상단
from c2.domain.planning.mission_plan import (  # noqa: F401  [shim]
    MAP_MAX, validate_mission_plan,
)
```

기존 파일 내부에서 `MAP_MAX`/`validate_mission_plan`을 참조하던 코드는 이 import를 그대로 사용.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/domain/test_mission_plan.py -v`
Expected: PASS

- [ ] **Step 6: Full regression + lint**

Run: `python -m pytest tests/ -v && python -m importlinter lint`
Expected: 전부 PASS

- [ ] **Step 7: Slice 1 완료 검증 + Commit**

```bash
git add src/c2/domain/planning/mission_plan.py tools/mission_plan_validator.py tests/domain/test_mission_plan.py
git commit -m "refactor(domain): 임무계획 값객체·검증 → c2.domain.planning.mission_plan + shim"
python -m pytest tests/ -v && python -m importlinter lint
```

Expected: 전체 PASS, `Contracts: 1 kept, 0 broken.` — **Slice 1 완료: domain 계층이 순수 모듈로 확립됨.**

---

## Slice 2~5 — Task 레벨 개요 (선행 Slice 병합 후 상세화)

### Slice 2 — ports + infrastructure

- **Task 12:** 포트 4종 정의 — `c2/application/ports/{llm,ontology_store,event_store,conversation_store}.py` (Protocol/ABC). 각 포트에 대한 계약 테스트(가짜 구현 통과) 작성.
- **Task 13:** `agent/vllm_client.py`·`model_loader.py`·`langgraph_llm.py` → `c2/infrastructure/llm/*`, `LLMClient` 포트 구현 + shim.
- **Task 14:** `wargame/models.py`의 `WargameDB`(SQLite) → `c2/infrastructure/persistence/sqlite_event_store.py`, `EventStore` 포트 구현 + shim.
- **Task 15:** `agent/conversation_store.py`(PostgreSQL) → `c2/infrastructure/persistence/conversation_store.py`, 포트 구현 + shim.
- **Task 16:** `ontology/graph_store.py`·`in_memory_store.py`·`factory.py` → `c2/infrastructure/ontology/*`, `OntologyStore` 포트 구현 + shim.
- **Task 17:** `graph_rag_tool.py`의 rdflib TTL 로더 → `c2/infrastructure/ontology/doctrine_loader.py` + shim.
- **Task 18:** import-linter 계약 정교화 — infrastructure는 application의 `ports`만 의존하도록 forbidden 계약 추가.

### Slice 3 — application (핵심, 최고 위험)

- **Task 19:** 엔진 순수 전투/탐지 계산(`_resolve_combat`, `_exchange_fire`, `_resolve_indirect_fire`, `_point_detection_risk` 내부의 순수부) → `c2/domain/wargame/combat.py`로 추출. **특성화 스냅샷으로 동작 보존 검증.**
- **Task 20:** 엔진 틱 루프·상태 전이 → `c2/application/simulation/engine.py`. `EventStore`/`OntologyStore` 포트 주입받도록 전환. **여기서 `wargame⇄tools` 순환 제거** (엔진에서 tools import 소멸).
- **Task 21:** `wargame/scenario.py` → `c2/application/simulation/scenario.py` + shim.
- **Task 22:** `wargame/llm_planner.py` → `c2/application/agent/mission_planner.py`, `LLMClient` 포트 사용 + shim.
- **Task 23:** `ontology/{wargame_builder,retrieval,writer,coa_view}.py` → `c2/application/ontology/*`, `OntologyStore` 포트 사용 + shim.
- **Task 24:** `graph_rag_tool` 조회 로직 → `c2/application/ontology/doctrine_rag.py` (`doctrine_loader` 포트 경유).
- **Task 25:** `agent/langgraph_agent.py`·`battlefield_agent.py`의 오케스트레이션 → `c2/application/agent/battlefield.py`. LLM 런타임은 인프라로.
- **Task 26:** `wargame/harness/*` → `c2/application/harness/*` (harness_db는 Task 14 방식으로 인프라).
- **Task 27:** `mission_plan_validator`의 세션/가드/의도분류 잔여부 → `c2/application/planning/*`.

### Slice 4 — presentation 재배선

- **Task 28:** `tools/*`(ARMA3 제외) → `c2/presentation/tools/*`, application 유스케이스 호출로 재배선 + shim.
- **Task 29:** `gradio_app.py`의 자동 재계획 워커·임무 적용 흐름 → `c2/application/simulation/replan.py`로 추출.
- **Task 30:** `ui/web_api.py` → `c2/presentation/web/api.py`. 엔진 접근을 `gradio_app._wg_ensure_engine()` 대신 **조립 루트(container)** 에서 주입받도록 변경. 자동 재계획 워커를 web_api에 연결.
- **Task 31:** Gradio 기능 인벤토리 → web_api 커버리지 확인 후 **`ui/gradio_app.py` 삭제**.
- **Task 32:** `main.py` → `c2/presentation/cli/main.py`, `c2/composition/container.py`로 포트↔구현 바인딩(조립 루트) 구성.

### Slice 5 — 정리

- **Task 33:** 모든 shim 제거, 남은 import를 `c2.*` 경로로 일괄 갱신.
- **Task 34:** 옛 top-level 패키지(`wargame/`, `agent/`, `ontology/`, `tools/`, `ui/`, `api/`) 잔여물 제거.
- **Task 35:** `config/` 경로·`scripts/`·`docs/`·`CLAUDE.md` 갱신 (새 구조 반영, 금지사항/디렉토리 표 갱신).
- **Task 36:** 최종 import-linter 전체 계약 green + 전체 특성화 테스트 green + 스모크(엔진·web_api·시나리오) 확인.

---

## Self-Review 결과

- **Spec coverage:** spec의 4계층 정의(§2)·디렉토리 구조(§3)·포트(§4)·6-Slice 마이그레이션(§5)·스코프 축소(§1) 모두 Task로 매핑됨. ARMA3/PDF RAG/video 제거 → Task 2·3. 특성화 테스트 → Task 4·5·6. 순환 제거 → Task 20.
- **Placeholder scan:** Slice 0·1의 모든 코드 스텝은 실제 시그니처 기반 실행 가능 코드. Slice 2~5는 의도적으로 task 레벨(상세화 예정)임을 명시.
- **Type consistency:** 포트명(`LLMClient`/`OntologyStore`/`EventStore`/`ConversationStore`)·shim 대상·domain 모듈 경로가 spec과 전 Task에서 일관.
