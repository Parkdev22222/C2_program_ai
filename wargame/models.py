"""
워게임 데이터 모델 및 SQLite 영속성 레이어.

Unit: 각 중대급 부대 상태
WargameDB: SQLite CRUD + 스냅샷 이력
"""

import json
import sqlite3
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

# 공중지원 유형별 기본 파라미터
AIR_SUPPORT_PRESETS = {
    "cas": {           # 근접항공지원 (A-10 류)
        "damage_rate": 60.0,   # %/hour (반경 중심)
        "radius": 1_500.0,     # m
        "duration": 300.0,     # 게임 초
        "delay": 120.0,        # 게임 초 (투입 전 대기)
    },
    "strike": {        # 정밀타격 (F-35 류)
        "damage_rate": 200.0,
        "radius": 400.0,
        "duration": 60.0,
        "delay": 180.0,
    },
    "artillery": {     # 장거리 포병지원
        "damage_rate": 30.0,
        "radius": 2_500.0,
        "duration": 600.0,
        "delay": 30.0,
    },
    "helicopter": {    # 공격헬기 지원
        "damage_rate": 45.0,
        "radius": 1_000.0,
        "duration": 240.0,
        "delay": 60.0,
    },
}

DB_PATH = Path(__file__).parent.parent / "data" / "wargame_state.db"


@dataclass
class AirSupport:
    """공중지원 임무 단위."""
    call_sign: str              # 호출부호 (예: "DARKSTAR-1")
    support_type: str           # "cas" | "strike" | "artillery" | "helicopter"
    target_x: float             # 폭격 중심 x (m)
    target_y: float             # 폭격 중심 y (m)
    radius: float               # 피해 반경 (m)
    damage_rate: float          # %/hour — 반경 중심 최대 피해율
    duration: float             # 지속 시간 (게임 초)
    delay: float                # 투입 지연 (게임 초)
    status: str = "pending"     # "pending" | "active" | "completed"
    elapsed: float = 0.0        # 활성화 후 경과 게임 시간 (초)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Unit:
    id: str                             # "Alpha" | "Bravo" | "Red1" | "Red2"
    side: str                           # "BLUFOR" | "OPFOR"
    x: float                            # 지도 좌표 (m, 동쪽)
    y: float                            # 지도 좌표 (m, 북쪽)
    combat_power: float                 # 0-100 %
    firepower_index: float              # 상대적 화력지수 (100 = 만편성 기계화 중대)
    max_speed: float                    # m/s (최대 기동 속도)
    status: str = "active"             # "active" | "suppressed" | "destroyed"
    waypoints: List[List[float]] = field(default_factory=list)  # [[x,y], ...]
    current_action: str = "hold"       # "move" | "attack" | "defend" | "hold"
    color: str = "blue"                # UI 색상
    unit_type: str = ""                # "기계화보병" | "전차" | "정찰" | "대전차" | "자주포"

    # ── 파생 속성 ──────────────────────────────────────────────────

    def effective_firepower(self) -> float:
        """현재 전투력 기반 실질 화력."""
        if self.status == "destroyed":
            return 0.0
        return self.firepower_index * (self.combat_power / 100.0)

    def is_active(self) -> bool:
        return self.status != "destroyed" and self.combat_power > 0

    def distance_to(self, other: "Unit") -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5

    def to_dict(self) -> dict:
        d = asdict(self)
        d["waypoints"] = json.dumps(d["waypoints"])
        return d

    @classmethod
    def from_row(cls, row: dict) -> "Unit":
        row = dict(row)
        row["waypoints"] = json.loads(row.get("waypoints") or "[]")
        return cls(**row)


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
