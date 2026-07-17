"""Task 14/33: WargameDB(SQLite) — c2.infrastructure.persistence.sqlite_event_store.

- 새 경로(c2.infrastructure.persistence.sqlite_event_store)에서 WargameDB가 임포트 가능한지 확인.
- WargameDB가 EventStore 포트를 여전히 구조적으로 만족하는지 확인.
- 모듈 위치가 깊어져도 기본 DB_PATH가 기존과 동일한 저장소 위치(repo-root/data/...)를 가리키는지 확인.
"""

from pathlib import Path


def test_wargame_db_importable_from_new_path():
    from c2.infrastructure.persistence.sqlite_event_store import WargameDB, DB_PATH

    assert WargameDB is not None
    assert isinstance(DB_PATH, Path)


def test_wargame_db_satisfies_event_store_port(tmp_path):
    from c2.application.ports.event_store import EventStore
    from c2.infrastructure.persistence.sqlite_event_store import WargameDB

    db = WargameDB(db_path=tmp_path / "test_wargame.db")
    assert isinstance(db, EventStore)


def test_default_db_path_resolves_to_repo_root_data_dir():
    """모듈 위치가 src/c2/infrastructure/persistence/로 깊어져도
    기본 DB_PATH는 예전과 동일하게 <repo-root>/data/wargame_state.db 를 가리켜야 한다."""
    from c2.infrastructure.persistence.sqlite_event_store import DB_PATH

    repo_root = Path(__file__).resolve().parents[2]
    expected = repo_root / "data" / "wargame_state.db"

    assert DB_PATH.name == "wargame_state.db"
    assert DB_PATH.parent.name == "data"
    assert DB_PATH.resolve() == expected.resolve()
