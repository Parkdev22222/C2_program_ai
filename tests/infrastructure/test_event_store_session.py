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
