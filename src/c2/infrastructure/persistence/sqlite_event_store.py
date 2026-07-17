"""워게임 SQLite 영속성 레이어 (EventStore 포트 구현).

`WargameDB`: SQLite CRUD + 스냅샷 이력.
`c2.application.ports.event_store.EventStore` 포트를 구조적으로 만족한다.

원래 `wargame/models.py`에 있던 코드를 이동한 것 — `wargame/models.py`는 이제
이 모듈의 `WargameDB`/`DB_PATH`를 shim re-export한다.
"""

import sqlite3
import threading
from pathlib import Path
from typing import List

from c2.domain.wargame.unit import Unit

# src/c2/infrastructure/persistence/sqlite_event_store.py 기준 4단계 상위가 리포지토리 루트
DB_PATH = Path(__file__).resolve().parents[4] / "data" / "wargame_state.db"


class WargameDB:
    """SQLite 기반 워게임 상태 저장소."""

    _CREATE_UNITS = """
    CREATE TABLE IF NOT EXISTS units (
        id TEXT PRIMARY KEY,
        side TEXT NOT NULL,
        x REAL NOT NULL,
        y REAL NOT NULL,
        combat_power REAL NOT NULL,
        firepower_index REAL NOT NULL,
        max_speed REAL NOT NULL,
        status TEXT NOT NULL,
        waypoints TEXT NOT NULL DEFAULT '[]',
        current_action TEXT NOT NULL DEFAULT 'hold',
        color TEXT NOT NULL DEFAULT 'blue',
        unit_type TEXT NOT NULL DEFAULT ''
    )
    """

    _CREATE_SNAPSHOTS = """
    CREATE TABLE IF NOT EXISTS snapshots (
        tick INTEGER,
        game_time REAL,
        unit_id TEXT,
        x REAL,
        y REAL,
        combat_power REAL,
        status TEXT,
        PRIMARY KEY (tick, unit_id)
    )
    """

    # 매 틱 저장 — LLM 에이전트가 읽는 실시간 전장 상태
    _CREATE_UNIT_REALTIME = """
    CREATE TABLE IF NOT EXISTS unit_realtime (
        tick        INTEGER NOT NULL,
        game_time   REAL    NOT NULL,
        unit_id     TEXT    NOT NULL,
        side        TEXT    NOT NULL,
        unit_type   TEXT    NOT NULL DEFAULT '',
        x           REAL    NOT NULL,
        y           REAL    NOT NULL,
        combat_power REAL   NOT NULL,
        status      TEXT    NOT NULL,
        current_action TEXT NOT NULL DEFAULT 'hold',
        PRIMARY KEY (tick, unit_id)
    )
    """

    _CREATE_EVENTS = """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tick INTEGER,
        game_time REAL,
        event_type TEXT,
        message TEXT
    )
    """

    def __init__(self, db_path: Path = DB_PATH):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock, self._connect() as conn:
            conn.execute(self._CREATE_UNITS)
            conn.execute(self._CREATE_SNAPSHOTS)
            conn.execute(self._CREATE_EVENTS)
            conn.execute(self._CREATE_UNIT_REALTIME)
            # unit_type 컬럼 마이그레이션 (기존 DB 호환)
            try:
                conn.execute("ALTER TABLE units ADD COLUMN unit_type TEXT NOT NULL DEFAULT ''")
            except Exception:
                pass

    # ── Unit CRUD ─────────────────────────────────────────────────

    def save_units(self, units: List[Unit]):
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM units")
            for u in units:
                conn.execute(
                    """INSERT INTO units VALUES
                    (:id,:side,:x,:y,:combat_power,:firepower_index,
                     :max_speed,:status,:waypoints,:current_action,:color,:unit_type)""",
                    u.to_dict(),
                )

    def load_units(self) -> List[Unit]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM units").fetchall()
        return [Unit.from_row(dict(r)) for r in rows]

    def update_unit(self, unit: Unit):
        d = unit.to_dict()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE units SET x=:x, y=:y, combat_power=:combat_power,
                   status=:status, waypoints=:waypoints,
                   current_action=:current_action
                   WHERE id=:id""",
                d,
            )

    # ── 스냅샷 (이력용) ──────────────────────────────────────────

    def save_snapshot(self, tick: int, game_time: float, units: List[Unit]):
        rows = [
            (tick, game_time, u.id, u.x, u.y, u.combat_power, u.status)
            for u in units
        ]
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?)", rows
            )

    # ── 실시간 부대 상태 (매 틱, LLM 에이전트 접근용) ─────────────

    def save_unit_realtime(self, tick: int, game_time: float, units: List[Unit]):
        """매 틱 전체 부대 상태를 unit_realtime 테이블에 저장."""
        rows = [
            (tick, game_time, u.id, u.side, u.unit_type,
             u.x, u.y, u.combat_power, u.status, u.current_action)
            for u in units
        ]
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO unit_realtime VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            # 오래된 레코드 정리 (최근 120틱만 유지)
            conn.execute(
                "DELETE FROM unit_realtime WHERE tick < (SELECT MAX(tick) - 120 FROM unit_realtime)"
            )

    def get_latest_unit_states(self) -> List[dict]:
        """가장 최근 틱의 전 부대 상태 반환."""
        with self._lock, self._connect() as conn:
            rows = conn.execute("""
                SELECT r.*
                FROM unit_realtime r
                INNER JOIN (
                    SELECT unit_id, MAX(tick) AS max_tick
                    FROM unit_realtime GROUP BY unit_id
                ) latest ON r.unit_id = latest.unit_id AND r.tick = latest.max_tick
                ORDER BY r.side, r.unit_id
            """).fetchall()
        return [dict(r) for r in rows]

    def get_unit_history(self, unit_id: str, limit: int = 20) -> List[dict]:
        """특정 부대의 최근 이동 이력 반환."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM unit_realtime WHERE unit_id=? ORDER BY tick DESC LIMIT ?",
                (unit_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ── 이벤트 로그 ───────────────────────────────────────────────

    def log_event(self, tick: int, game_time: float, event_type: str, msg: str):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events(tick,game_time,event_type,message) VALUES(?,?,?,?)",
                (tick, game_time, event_type, msg),
            )

    def get_recent_events(self, n: int = 30) -> List[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def clear(self):
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM units")
            conn.execute("DELETE FROM snapshots")
            conn.execute("DELETE FROM unit_realtime")
            conn.execute("DELETE FROM events")
