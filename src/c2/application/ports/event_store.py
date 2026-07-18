"""워게임 이벤트/상태 저장소 포트.

`wargame/models.py`의 `WargameDB`(SQLite CRUD + 스냅샷 이력)가 이 포트를 구현한다.
"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from c2.domain.wargame.unit import Unit


@runtime_checkable
class EventStore(Protocol):
    """워게임 부대 상태/이벤트 영속화 포트 (`WargameDB`가 구현)."""

    def save_units(self, units: List[Unit]) -> None:
        ...

    def load_units(self) -> List[Unit]:
        ...

    def update_unit(self, unit: Unit) -> None:
        ...

    def save_snapshot(self, tick: int, game_time: float, units: List[Unit]) -> None:
        ...

    def save_unit_realtime(self, tick: int, game_time: float, units: List[Unit]) -> None:
        ...

    def get_latest_unit_states(self) -> List[dict]:
        ...

    def get_unit_history(self, unit_id: str, limit: int = 20) -> List[dict]:
        ...

    def log_event(self, tick: int, game_time: float, event_type: str, msg: str) -> None:
        ...

    def get_recent_events(self, n: int = 30) -> List[dict]:
        ...

    def clear(self) -> None:
        ...

    def current_session_id(self) -> str:
        ...

    def reset_for_new_session(self, scenario: str = "") -> str:
        ...
