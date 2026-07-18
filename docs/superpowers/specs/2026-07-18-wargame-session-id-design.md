# 워게임 DB 세션 ID 도입 — 설계 문서

- 날짜: 2026-07-18
- 대상 계층: `c2.infrastructure.persistence`(핵심), `c2.application.simulation`(reset 연결)
- import-linter 계약 영향 없음 (계층 순서 그대로)

## 1. 배경 / 문제

현재 워게임 이벤트는 단일 SQLite 파일 `data/wargame_state.db`의 `events` 테이블에 저장되고,
`WargameEngine.reset()` → `WargameDB.clear()`가 **모든 테이블을 전부 삭제**한다.
따라서 DB에는 항상 "직전 게임 1회분" 이벤트만 남고, 과거 게임 이력은 사라진다.

전술 채팅·전술 평가/학습·전투로그·상황조회는 모두
`WargameDB.get_recent_events()` 하나를 통해 이벤트를 읽는다.

### 목표
1. 게임 실행(세션)마다 고유 `session_id`를 부여한다.
2. reset 해도 이벤트를 **지우지 않고 누적**하여 이력을 보관한다(논문 분석용).
3. 전술 채팅·전술 평가/학습 등 **모든 이벤트 조회는 현재 세션 데이터만** 반영한다.
4. 누적으로 인한 DB 무한 증가를 막기 위해 **최근 N개 세션만 유지**한다.

## 2. 스코프 경계 (의도적 결정)

| 테이블 | 처리 | 이유 |
|--------|------|------|
| `events` | `session_id` 컬럼 추가 + **누적** | 채팅/평가가 읽는 대상. 이력 보관 목적 |
| `units` | reset 시 **기존대로 wipe** | 현재 부대 상태(단일 게임) |
| `snapshots` | reset 시 **기존대로 wipe** | PK `(tick, unit_id)` — tick이 세션마다 0부터 재시작하므로 누적 시 충돌 |
| `unit_realtime` | reset 시 **기존대로 wipe** | 실시간 상태, `MAX(tick)` 조회 로직 — 누적 시 세션 간 tick 충돌 |
| `sessions` (신규) | 세션 레지스트리, 이력 정리 시 함께 정리 | 세션 목록/메타 조회 |

즉 **`events` 테이블만 세션화**한다. 실시간 상태 테이블(units/snapshots/unit_realtime)은
tick이 세션마다 0으로 재시작하므로 세션 간 충돌을 피하려면 현재 게임분만 유지해야 한다.
요청된 "교전/이벤트 → 채팅·평가" 경로는 전부 `events`이므로 이 스코프로 목적을 완전히 달성한다.

## 3. 설계 상세

### 3.1 스키마 변경 (`WargameDB`, `sqlite_event_store.py`)

`events` 테이블에 `session_id` 컬럼 추가:

```sql
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick INTEGER,
    game_time REAL,
    event_type TEXT,
    message TEXT,
    session_id TEXT NOT NULL DEFAULT 'legacy'
);
```

신규 `sessions` 레지스트리 테이블:

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    scenario   TEXT NOT NULL DEFAULT ''
);
```

기존 DB 호환 마이그레이션 — `_init_db()`에서 try/except `ALTER TABLE`:

```python
try:
    conn.execute("ALTER TABLE events ADD COLUMN session_id TEXT NOT NULL DEFAULT 'legacy'")
except Exception:
    pass
```

기존 행은 `session_id='legacy'`로 남아 현재 세션 필터에 걸리지 않는다(과거 데이터 격리).

### 3.2 세션 상태 관리 (`WargameDB`)

- 인스턴스 필드 `self._session_id: str`.
- `__init__` 끝에서 최초 세션 발급: `self._start_session()` 호출(초기화 시엔 live-state를 지우지 않음).
- `_new_session_id() -> str`: `uuid.uuid4().hex[:12]` 기반. (앱 계층이므로 `uuid`/`datetime` 사용 가능)

```python
def _start_session(self, scenario: str = "") -> str:
    """새 session_id를 발급하고 sessions 레지스트리에 기록한 뒤, 오래된 세션을 정리한다.
    live-state 테이블은 건드리지 않는다."""
    sid = uuid.uuid4().hex[:12]
    created = datetime.now().isoformat(timespec="seconds")
    with self._lock, self._connect() as conn:
        conn.execute(
            "INSERT INTO sessions(session_id, created_at, scenario) VALUES (?,?,?)",
            (sid, created, scenario),
        )
        self._prune_old_sessions(conn)   # 같은 트랜잭션에서 정리
    self._session_id = sid
    return sid
```

### 3.3 이력 보존 정책 — 최근 N개 세션

- 상수 `_MAX_SESSIONS = 10` (모듈 상수).
- `_prune_old_sessions(conn)`: `sessions`를 `created_at DESC`로 정렬해 상위 N개만 남기고,
  나머지 세션의 `events`와 `sessions` 행을 삭제.

```python
def _prune_old_sessions(self, conn):
    keep = [r[0] for r in conn.execute(
        "SELECT session_id FROM sessions ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (_MAX_SESSIONS,)).fetchall()]
    if not keep:
        return
    placeholders = ",".join("?" * len(keep))
    conn.execute(f"DELETE FROM events   WHERE session_id NOT IN ({placeholders})", keep)
    conn.execute(f"DELETE FROM sessions WHERE session_id NOT IN ({placeholders})", keep)
```

`'legacy'` 이벤트는 `sessions`에 없으므로, 최초 정리 시 함께 삭제된다(과거 잔여 데이터 청소 효과).

### 3.4 이벤트 기록/조회

`log_event` — 현재 `self._session_id`를 자동 태깅(엔진 호출부 시그니처 불변):

```python
def log_event(self, tick, game_time, event_type, msg):
    with self._lock, self._connect() as conn:
        conn.execute(
            "INSERT INTO events(tick,game_time,event_type,message,session_id) VALUES(?,?,?,?,?)",
            (tick, game_time, event_type, msg, self._session_id),
        )
```

`get_recent_events` — `session_id=None`이면 **현재 세션**으로 필터(기본 동작 = 현재 세션):

```python
def get_recent_events(self, n: int = 30, session_id: str | None = None):
    sid = session_id if session_id is not None else self._session_id
    with self._lock, self._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (sid, n),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]
```

### 3.5 새 세션 시작 지점 (`WargameEngine.reset`, `engine.py:356`)

`self.db.clear()` 한 줄을 다음으로 교체:

```python
self.db.reset_for_new_session()   # live-state wipe + 새 session_id 발급 (events 보존)
```

`WargameDB.reset_for_new_session(scenario="")`:
1. `units` / `snapshots` / `unit_realtime` DELETE (events는 보존)
2. `self._start_session(scenario)` — 새 session_id + 레지스트리 기록 + 오래된 세션 정리

기존 `clear()`(events 포함 전체 wipe)는 그대로 남겨 두어, 진짜 완전 초기화가 필요한 경우/테스트에서 재사용한다.

## 4. 호출부 영향 — 수정 불필요

`get_recent_events` 기본값이 "현재 세션"이므로 아래 호출부는 **코드 변경 없이** 현재 세션만 반영:

- `evaluate_and_learn()` — `replan.py:602`, `get_recent_events(n=500)`
- `get_wargame_battle_log()` — `wargame_query_tool.py:346`
- `get_wargame_situation()` 최근 이벤트 — `wargame_query_tool.py:38`
- 피격 판정 peek — `wargame_query_tool.py:38~` (tick 필터 + 세션 필터로 이전 세션 오탐 차단)

## 5. 안전성 / 엣지 케이스

1. **tick 재사용 오탐 차단**: 이벤트가 누적돼도 tick 기반 조회(피격 peek)가 세션 필터로 격리된다.
2. **엔진 재사용**: reset은 새 엔진이 아니라 동일 `WargameEngine`/`WargameDB` 인스턴스를 재사용하므로 `self._session_id` 상태가 유지된다.
3. **서버 재시작**: DB 파일은 유지되고 새 `WargameDB` 인스턴스가 새 세션을 발급하므로 이전 서버 실행분과 구분된다(최근 N개 정책으로 오래된 것 자동 정리).
4. **동시성**: `_start_session`의 INSERT+prune을 단일 커넥션/락에서 수행.
5. **기존 DB 마이그레이션**: `ALTER TABLE` 실패는 무시(이미 컬럼 존재 시).

## 6. 테스트 계획

- `WargameDB`: 새 세션 발급 시 `get_recent_events`가 이전 세션 이벤트를 반환하지 않음.
- 누적: reset 2회 후 `events` 총 행수는 두 세션 합, 현재 세션 조회는 마지막 세션분만.
- 보존 정책: `_MAX_SESSIONS`+2개 세션 생성 후 오래된 2개 세션 이벤트/레지스트리 삭제 확인.
- 마이그레이션: session_id 컬럼 없는 기존 DB 파일 열 때 정상 ALTER + `'legacy'` 격리.
- `engine.reset()` 후 새 session_id로 로깅되고 이전 이벤트가 조회에서 빠짐.

## 7. 미포함(향후)

- `snapshots`/`unit_realtime`의 세션별 이력 보관(부대 궤적 리플레이) — 별도 스펙.
- 웹 UI에서 과거 세션 선택 조회 API/화면 — 별도 스펙.
