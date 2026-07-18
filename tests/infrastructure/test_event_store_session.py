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
