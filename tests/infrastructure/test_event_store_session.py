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
