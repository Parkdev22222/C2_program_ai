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
