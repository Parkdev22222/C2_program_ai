# 워게임 DB 세션 ID 도입 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 워게임 `events`를 게임 실행(세션)별로 태깅해 누적 보관하고, 전술 채팅·전술 평가/학습 등 모든 이벤트 조회가 현재 세션 데이터만 반영하도록 한다.

**Architecture:** `WargameDB`(SQLite)의 `events` 테이블에 `session_id` 컬럼을 추가하고 신규 `sessions` 레지스트리 테이블을 둔다. reset 시 실시간 상태 테이블(units/snapshots/unit_realtime)만 비우고 이벤트는 보존하며 새 session_id를 발급한다. `get_recent_events`는 기본값으로 현재 세션만 반환하므로 상위 호출부는 무수정으로 목적을 달성한다. DB 무한 증가는 "최근 N개 세션만 유지" 정책으로 막는다.

**Tech Stack:** Python 3.9+, sqlite3(표준 라이브러리), pytest.

## Global Constraints

- 대상 파일: `src/c2/infrastructure/persistence/sqlite_event_store.py`(핵심), `src/c2/application/simulation/engine.py`(reset 연결 1줄).
- 계층 규칙 준수: 이 변경은 infrastructure/application 내부에 한정, import-linter 3 계약 유지(`PYTHONPATH=src lint-imports` → 3 kept, 0 broken).
- `WargameDB`는 `c2.application.ports.event_store.EventStore` 포트를 계속 구조적으로 만족해야 한다(`get_recent_events`에 추가하는 파라미터는 **선택적(default)** 이어야 함).
- 앱 계층이므로 `uuid`/`datetime` 사용 가능(워크플로 스크립트 제약과 무관).
- 상수 `_MAX_SESSIONS = 10` (모듈 상수).
- 기존 DB 파일 호환: `events`에 `session_id` 컬럼이 없을 수 있으므로 try/except `ALTER TABLE`로 마이그레이션, 기존 행 기본값 `'legacy'`.
- 테스트는 항상 `WargameDB(db_path=tmp_path / "...")`로 격리된 임시 파일에 대해 수행(공유 `DB_PATH` 오염 금지).
- 테스트 실행: `PYTHONPATH=src python -m pytest <경로> -v` (conftest가 `src`를 sys.path에 넣지만 명시적으로 준다).

---

### Task 1: `events` 테이블 세션화 + 세션 발급/조회 기본 골격

첫 세션 자동 발급, `log_event` 자동 태깅, `get_recent_events` 현재 세션 필터까지 한 번에 완성한다(서로 의존하는 최소 단위).

**Files:**
- Modify: `src/c2/infrastructure/persistence/sqlite_event_store.py`
- Test: `tests/infrastructure/test_event_store_session.py` (신규)

**Interfaces:**
- Consumes: 기존 `WargameDB(db_path)`, `_connect()`, `_lock`, `_CREATE_EVENTS`, `_init_db()`, `log_event()`, `get_recent_events()`.
- Produces:
  - 모듈 상수 `_MAX_SESSIONS: int = 10`
  - `WargameDB._session_id: str` (인스턴스 필드, 생성 직후 유효한 12자리 hex)
  - `WargameDB.current_session_id() -> str`
  - `WargameDB.get_recent_events(n: int = 30, session_id: str | None = None) -> List[dict]` — `session_id=None`이면 현재 세션 필터
  - `log_event(...)`는 현재 `self._session_id`를 자동 태깅(시그니처 불변)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infrastructure/test_event_store_session.py` 생성:

```python
"""워게임 DB 세션 ID — events 세션화/현재 세션 필터."""

from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def test_new_db_has_session_id(tmp_path):
    db = WargameDB(db_path=tmp_path / "s.db")
    sid = db.current_session_id()
    assert isinstance(sid, str) and len(sid) == 12


def test_log_event_tagged_and_readback(tmp_path):
    db = WargameDB(db_path=tmp_path / "s.db")
    db.log_event(1, 60.0, "COMBAT", "hello")
    events = db.get_recent_events(n=10)
    assert len(events) == 1
    assert events[0]["event_type"] == "COMBAT"
    assert events[0]["session_id"] == db.current_session_id()


def test_get_recent_events_explicit_session_filter(tmp_path):
    db = WargameDB(db_path=tmp_path / "s.db")
    sid = db.current_session_id()
    db.log_event(1, 60.0, "COMBAT", "a")
    # 존재하지 않는 세션 필터 → 빈 결과
    assert db.get_recent_events(n=10, session_id="deadbeefdead") == []
    # 현재 세션 명시 필터 → 1건
    assert len(db.get_recent_events(n=10, session_id=sid)) == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=src python -m pytest tests/infrastructure/test_event_store_session.py -v`
Expected: FAIL — `AttributeError: 'WargameDB' object has no attribute 'current_session_id'` (또는 `session_id` KeyError).

- [ ] **Step 3: 구현**

`sqlite_event_store.py` 상단 import에 표준 라이브러리 추가(파일 최상단 import 블록, `import sqlite3` 근처):

```python
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
```

모듈 상수 추가(`DB_PATH = data_path("wargame_state.db")` 바로 아래):

```python
DB_PATH = data_path("wargame_state.db")

# 이력 보존 정책: 최근 N개 세션의 이벤트만 유지
_MAX_SESSIONS = 10
```

`_CREATE_EVENTS`에 `session_id` 컬럼 추가:

```python
    _CREATE_EVENTS = """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tick INTEGER,
        game_time REAL,
        event_type TEXT,
        message TEXT,
        session_id TEXT NOT NULL DEFAULT 'legacy'
    )
    """
```

`_CREATE_SESSIONS` 클래스 상수 추가(`_CREATE_EVENTS` 바로 아래):

```python
    _CREATE_SESSIONS = """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        scenario   TEXT NOT NULL DEFAULT ''
    )
    """
```

`__init__` 끝에서 최초 세션 발급(기존 `self._init_db()` 다음 줄):

```python
    def __init__(self, db_path: Path = DB_PATH):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._session_id = ""
        self._init_db()
        self._start_session()
```

`_init_db()`에 sessions 테이블 생성 + events 마이그레이션 추가(기존 메서드 교체):

```python
    def _init_db(self):
        with self._lock, self._connect() as conn:
            conn.execute(self._CREATE_UNITS)
            conn.execute(self._CREATE_SNAPSHOTS)
            conn.execute(self._CREATE_EVENTS)
            conn.execute(self._CREATE_UNIT_REALTIME)
            conn.execute(self._CREATE_SESSIONS)
            # unit_type 컬럼 마이그레이션 (기존 DB 호환)
            try:
                conn.execute("ALTER TABLE units ADD COLUMN unit_type TEXT NOT NULL DEFAULT ''")
            except Exception:
                pass
            # session_id 컬럼 마이그레이션 (기존 DB 호환)
            try:
                conn.execute(
                    "ALTER TABLE events ADD COLUMN session_id TEXT NOT NULL DEFAULT 'legacy'"
                )
            except Exception:
                pass
```

세션 발급 메서드 추가(`_init_db` 다음, `save_units` 앞):

```python
    # ── 세션 관리 ─────────────────────────────────────────────────

    def current_session_id(self) -> str:
        return self._session_id

    def _start_session(self, scenario: str = "") -> str:
        """새 session_id를 발급해 sessions 레지스트리에 기록하고 오래된 세션을 정리한다.
        live-state 테이블(units/snapshots/unit_realtime)은 건드리지 않는다."""
        sid = uuid.uuid4().hex[:12]
        created = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions(session_id, created_at, scenario) VALUES (?,?,?)",
                (sid, created, scenario),
            )
        self._session_id = sid
        return sid
```

`log_event` 교체(session_id 태깅):

```python
    def log_event(self, tick: int, game_time: float, event_type: str, msg: str):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events(tick,game_time,event_type,message,session_id) "
                "VALUES(?,?,?,?,?)",
                (tick, game_time, event_type, msg, self._session_id),
            )
```

`get_recent_events` 교체(현재 세션 기본 필터):

```python
    def get_recent_events(self, n: int = 30, session_id: str | None = None) -> List[dict]:
        sid = session_id if session_id is not None else self._session_id
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (sid, n),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=src python -m pytest tests/infrastructure/test_event_store_session.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: 포트 만족 회귀 확인**

Run: `PYTHONPATH=src python -m pytest tests/infrastructure/test_event_store_move.py -v`
Expected: PASS (3 passed) — `get_recent_events`에 추가한 선택적 파라미터가 EventStore 포트 만족을 깨지 않음.

- [ ] **Step 6: 커밋**

```bash
git add src/c2/infrastructure/persistence/sqlite_event_store.py tests/infrastructure/test_event_store_session.py
git commit -m "feat(persistence): events 테이블 session_id 태깅 + 현재 세션 필터"
```

---

### Task 2: 새 세션 시작(live-state wipe) + 이벤트 누적

reset 진입점이 될 `reset_for_new_session()`을 만든다. 이벤트는 보존하고 실시간 상태만 비우며 새 session_id를 발급한다.

**Files:**
- Modify: `src/c2/infrastructure/persistence/sqlite_event_store.py`
- Test: `tests/infrastructure/test_event_store_session.py` (Task 1 파일에 추가)

**Interfaces:**
- Consumes: Task 1의 `_start_session()`, `current_session_id()`, `log_event()`, `get_recent_events()`.
- Produces:
  - `WargameDB.reset_for_new_session(scenario: str = "") -> str` — units/snapshots/unit_realtime DELETE(events 보존) 후 새 session_id 발급, 새 session_id 반환.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infrastructure/test_event_store_session.py`에 추가:

```python
def test_reset_for_new_session_keeps_events_but_switches_filter(tmp_path):
    db = WargameDB(db_path=tmp_path / "s.db")
    db.log_event(1, 60.0, "COMBAT", "old-session-event")
    old_sid = db.current_session_id()

    new_sid = db.reset_for_new_session()
    assert new_sid != old_sid
    assert db.current_session_id() == new_sid

    # 현재(새) 세션 조회 → 비어 있음
    assert db.get_recent_events(n=10) == []
    # 이전 세션 이벤트는 DB에 보존(명시 필터로 조회 가능)
    old = db.get_recent_events(n=10, session_id=old_sid)
    assert len(old) == 1 and old[0]["message"] == "old-session-event"

    # 새 세션에 로깅하면 새 세션 조회에만 나타남
    db.log_event(1, 60.0, "COMBAT", "new-session-event")
    cur = db.get_recent_events(n=10)
    assert len(cur) == 1 and cur[0]["message"] == "new-session-event"


def test_reset_for_new_session_wipes_live_state(tmp_path):
    from c2.domain.wargame.unit import Unit

    db = WargameDB(db_path=tmp_path / "s.db")
    u = Unit(id="Alpha", side="BLUFOR", x=1000, y=1000, combat_power=100.0,
             firepower_index=50.0, max_speed=10.0, status="active",
             unit_type="mech_inf")
    db.save_units([u])
    db.save_unit_realtime(1, 60.0, [u])
    assert len(db.load_units()) == 1
    assert len(db.get_latest_unit_states()) == 1

    db.reset_for_new_session()
    assert db.load_units() == []
    assert db.get_latest_unit_states() == []
```

> 주: `Unit` 생성 인자는 `c2.domain.wargame.unit.Unit`의 실제 필드에 맞춘다. 필드명이 다르면
> Task 실행 시 `Unit`을 열어 확인 후 맞춘다(테스트는 live-state wipe 검증이 목적이므로 최소 필드만).

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=src python -m pytest tests/infrastructure/test_event_store_session.py -k reset_for_new_session -v`
Expected: FAIL — `AttributeError: 'WargameDB' object has no attribute 'reset_for_new_session'`.

- [ ] **Step 3: 구현**

`reset_for_new_session` 추가(`_start_session` 다음):

```python
    def reset_for_new_session(self, scenario: str = "") -> str:
        """새 게임 세션을 시작한다. 실시간 상태 테이블(units/snapshots/unit_realtime)만
        비우고 events는 보존한 뒤 새 session_id를 발급한다."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM units")
            conn.execute("DELETE FROM snapshots")
            conn.execute("DELETE FROM unit_realtime")
        return self._start_session(scenario)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=src python -m pytest tests/infrastructure/test_event_store_session.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: 커밋**

```bash
git add src/c2/infrastructure/persistence/sqlite_event_store.py tests/infrastructure/test_event_store_session.py
git commit -m "feat(persistence): reset_for_new_session — live-state만 wipe, events 누적"
```

---

### Task 3: 이력 보존 정책 — 최근 N개 세션만 유지

새 세션 발급 시 `sessions`를 최신순 N개만 남기고, 그 밖 세션의 events/sessions 행을 삭제한다. `'legacy'` 잔여 이벤트도 이때 청소된다.

**Files:**
- Modify: `src/c2/infrastructure/persistence/sqlite_event_store.py`
- Test: `tests/infrastructure/test_event_store_session.py` (추가)

**Interfaces:**
- Consumes: Task 1/2의 `_start_session()`, `_MAX_SESSIONS`, `reset_for_new_session()`, `get_recent_events()`.
- Produces:
  - `WargameDB._prune_old_sessions(conn) -> None` (내부, 호출자가 이미 잡은 conn/트랜잭션에서 실행)
  - `_start_session`이 INSERT 직후 같은 트랜잭션에서 `_prune_old_sessions(conn)` 호출

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infrastructure/test_event_store_session.py`에 추가:

```python
def test_prune_keeps_only_recent_sessions(tmp_path):
    import c2.infrastructure.persistence.sqlite_event_store as ses

    db = WargameDB(db_path=tmp_path / "s.db")
    max_n = ses._MAX_SESSIONS

    first_sid = db.current_session_id()
    db.log_event(1, 60.0, "COMBAT", "first")

    # 최초 세션 포함 총 (max_n + 2)개 세션을 만든다
    for i in range(max_n + 1):
        db.reset_for_new_session()
        db.log_event(1, 60.0, "COMBAT", f"s{i}")

    # 가장 오래된 세션(first_sid)은 정리되어 events가 없어야 한다
    assert db.get_recent_events(n=10, session_id=first_sid) == []

    # sessions 레지스트리에도 max_n개만 남아야 한다
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    try:
        count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()
    assert count == max_n


def test_prune_clears_legacy_events(tmp_path):
    import sqlite3

    # session_id 컬럼이 이미 있는 DB에 'legacy' 행을 직접 주입한 뒤 새 세션 발급
    path = tmp_path / "s.db"
    db = WargameDB(db_path=path)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO events(tick,game_time,event_type,message,session_id) "
            "VALUES (1,60.0,'COMBAT','legacy-row','legacy')"
        )
        conn.commit()
    finally:
        conn.close()

    db.reset_for_new_session()
    # legacy는 sessions에 없으므로 정리 대상 → 조회 불가
    assert db.get_recent_events(n=10, session_id="legacy") == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=src python -m pytest tests/infrastructure/test_event_store_session.py -k prune -v`
Expected: FAIL — first 세션/legacy 이벤트가 아직 남아 있어 assert 실패.

- [ ] **Step 3: 구현**

`_prune_old_sessions` 추가(`_start_session` 다음):

```python
    def _prune_old_sessions(self, conn) -> None:
        """sessions를 최신순 _MAX_SESSIONS개만 남기고, 그 밖 세션의 events/sessions를 삭제.
        호출자가 이미 잡은 conn/락 안에서 실행한다."""
        keep = [
            r[0]
            for r in conn.execute(
                "SELECT session_id FROM sessions ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (_MAX_SESSIONS,),
            ).fetchall()
        ]
        if not keep:
            return
        placeholders = ",".join("?" * len(keep))
        conn.execute(f"DELETE FROM events   WHERE session_id NOT IN ({placeholders})", keep)
        conn.execute(f"DELETE FROM sessions WHERE session_id NOT IN ({placeholders})", keep)
```

`_start_session`에서 INSERT 직후 prune 호출(기존 메서드 교체):

```python
    def _start_session(self, scenario: str = "") -> str:
        """새 session_id를 발급해 sessions 레지스트리에 기록하고 오래된 세션을 정리한다.
        live-state 테이블(units/snapshots/unit_realtime)은 건드리지 않는다."""
        sid = uuid.uuid4().hex[:12]
        created = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions(session_id, created_at, scenario) VALUES (?,?,?)",
                (sid, created, scenario),
            )
            self._prune_old_sessions(conn)
        self._session_id = sid
        return sid
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=src python -m pytest tests/infrastructure/test_event_store_session.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: 커밋**

```bash
git add src/c2/infrastructure/persistence/sqlite_event_store.py tests/infrastructure/test_event_store_session.py
git commit -m "feat(persistence): 최근 N개 세션만 유지하는 이력 보존 정책"
```

---

### Task 4: `engine.reset()`를 세션 발급 경로로 연결

`WargameEngine.reset()`이 이벤트 전체를 지우는 `db.clear()` 대신 `db.reset_for_new_session()`을 호출하도록 바꿔, 게임 리셋마다 새 세션이 발급되고 이벤트가 누적되게 한다.

**Files:**
- Modify: `src/c2/application/simulation/engine.py:356`
- Test: `tests/application/test_engine_session_reset.py` (신규)

**Interfaces:**
- Consumes: Task 2의 `WargameDB.reset_for_new_session()`, 기존 `WargameEngine(units, db=...)`, `engine.reset(units)`.
- Produces: (동작 계약) `engine.reset()` 후 `engine.db.current_session_id()`가 이전과 달라지고, 리셋 전 이벤트는 현재 세션 조회에서 제외된다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_engine_session_reset.py` 생성:

```python
"""engine.reset()가 db.clear()가 아닌 새 세션 발급 경로를 타는지 검증."""

from c2.infrastructure.persistence.sqlite_event_store import WargameDB
from c2.application.simulation.engine import WargameEngine
from c2.application.simulation.scenario import setup_cheorwon_bn


def test_reset_starts_new_session_and_preserves_prior_events(tmp_path):
    db = WargameDB(db_path=tmp_path / "s.db")
    units = setup_cheorwon_bn()
    eng = WargameEngine(units, db=db)

    sid_before = db.current_session_id()
    db.log_event(1, 60.0, "COMBAT", "before-reset")
    assert len(db.get_recent_events(n=10)) == 1

    eng.reset(setup_cheorwon_bn())

    sid_after = db.current_session_id()
    assert sid_after != sid_before
    # 리셋 후 현재 세션 이벤트 조회는 이전 이벤트를 포함하지 않음
    assert db.get_recent_events(n=10, session_id=sid_after) == []
    # 이전 이벤트는 이전 세션에 보존
    prior = db.get_recent_events(n=10, session_id=sid_before)
    assert len(prior) == 1 and prior[0]["message"] == "before-reset"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=src python -m pytest tests/application/test_engine_session_reset.py -v`
Expected: FAIL — 현재 `engine.reset()`은 `db.clear()`를 호출하므로 session_id가 바뀌지 않고(같은 세션 유지) prior 이벤트도 통째로 삭제되어 assert 실패.

- [ ] **Step 3: 구현**

`src/c2/application/simulation/engine.py`의 `reset()` 내부 `self.db.clear()`(현재 356행)를 교체:

```python
            self._air_use_count  = {"BLUFOR": 0, "OPFOR": 0}
            self._air_reset_at   = self._AIR_RESET_TICKS
            self.db.reset_for_new_session()
            self.db.save_units(units)
            self.db.save_snapshot(0, 0.0, units)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=src python -m pytest tests/application/test_engine_session_reset.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: 커밋**

```bash
git add src/c2/application/simulation/engine.py tests/application/test_engine_session_reset.py
git commit -m "feat(engine): reset 시 db.clear() 대신 새 세션 발급(이벤트 누적)"
```

---

### Task 5: 통합 확인 — 계약/회귀/린트

전체 스위트와 import-linter로 계층 계약·회귀를 확인한다. 상위 호출부(채팅/평가/전투로그/상황)는 무수정이므로 별도 코드 없음.

**Files:**
- (코드 변경 없음 — 검증 전용)

**Interfaces:**
- Consumes: Task 1~4 전체.

- [ ] **Step 1: 세션 관련 테스트 전체 실행**

Run: `PYTHONPATH=src python -m pytest tests/infrastructure/test_event_store_session.py tests/infrastructure/test_event_store_move.py tests/application/test_engine_session_reset.py -v`
Expected: PASS (전부 통과).

- [ ] **Step 2: 전체 스위트 회귀 확인**

Run: `PYTHONPATH=src python -m pytest -q`
Expected: 기존 통과 테스트가 그대로 통과(신규 실패 없음). 만약 `get_recent_events`/`clear` 관련 기존 테스트가 세션 필터 도입으로 깨지면, 해당 테스트가 "동일 db 인스턴스에서 log→get"을 하는지 확인한다(동일 인스턴스면 현재 세션 필터로 정상 조회되어야 함). 별도 세션 경계를 기대하지 않는 한 통과해야 한다.

- [ ] **Step 3: import-linter 계약 확인**

Run: `PYTHONPATH=src lint-imports`
Expected: `Contracts: 3 kept, 0 broken.`

- [ ] **Step 4: (변경 없음이면 커밋 불필요)**

검증만 통과하면 이 태스크는 커밋 없이 종료. 회귀 수정이 필요했다면 해당 수정만 별도 커밋.

---

## Self-Review

**1. Spec coverage:**
- 스펙 3.1(스키마: events.session_id, sessions 테이블, ALTER 마이그레이션) → Task 1.
- 스펙 3.2(세션 상태 관리, `_start_session`, `_new_session_id`) → Task 1(+Task 3에서 prune 결합). `_new_session_id` 별도 헬퍼는 인라인 `uuid.uuid4().hex[:12]`로 대체(YAGNI) — 동작 동일.
- 스펙 3.3(최근 N개 유지, `_prune_old_sessions`, legacy 청소) → Task 3.
- 스펙 3.4(log_event 태깅, get_recent_events 현재 세션 기본) → Task 1.
- 스펙 3.5(reset_for_new_session, engine.reset 연결, clear 보존) → Task 2 + Task 4. 기존 `clear()`는 손대지 않아 그대로 남음.
- 스펙 4(호출부 무수정) → Task 5에서 회귀로 확인.
- 스펙 5(엣지: tick 오탐 차단/엔진 재사용/서버 재시작/동시성/마이그레이션) → Task 1(마이그레이션), Task 3(동시성: 단일 conn), Task 4(엔진 재사용 유지), tick 오탐은 현재 세션 필터로 자동 차단.
- 스펙 6(테스트 계획) → Task 1~4 각 테스트로 커버.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드/명령/기대출력 포함. "TBD/TODO/적절히 처리" 없음. Task 2의 `Unit` 필드는 실행 시 실제 필드 확인 안내를 명시(플레이스홀더 아님, 안전장치).

**3. Type consistency:** `current_session_id()`, `_start_session()`, `reset_for_new_session()`, `_prune_old_sessions(conn)`, `get_recent_events(n, session_id=None)`, `_MAX_SESSIONS` — Task 간 명칭·시그니처 일관. `engine.reset()`은 `reset_for_new_session()`(Task 2 정의) 호출로 일치.
