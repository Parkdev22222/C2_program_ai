"""WargameSession.ensure_engine의 full_recon 초기 인텔 갱신 회귀 테스트.

버그: full_recon(UAV 완전정찰)은 원래 _tick() 중에만 인텔에 반영돼, 시뮬 시작 직후(틱 0)
에는 OPFOR가 approximate로 남았다. 그 결과 채팅("탐지 없음")과 공격계획("모두 탐지") 표시가
어긋났다. 수정: ensure_engine이 full_recon 설정 직후 _update_intelligence()로 초기 인텔을
즉시 갱신한다. 이 테스트는 그 동작을 고정한다.
"""
import tempfile
from pathlib import Path

from c2.application.simulation.session import WargameSession
from c2.application.simulation.engine import WargameEngine
from c2.application.simulation.scenario import setup_bn_vs_bn
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _make_session() -> WargameSession:
    def factory():
        db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "s.db")
        return WargameEngine(setup_bn_vs_bn(), db=db)

    return WargameSession(engine_factory=factory)


def test_full_recon_marks_opfor_detected_before_first_tick():
    """ensure_engine 직후(틱 0), full_recon으로 모든 활성 OPFOR가 detected 여야 한다."""
    session = _make_session()
    engine = session.ensure_engine()

    assert engine.tick == 0, "이 검증은 첫 틱 이전 상태여야 의미가 있다"
    assert getattr(engine, "full_recon", False) is True

    intel = engine.get_state()["intelligence"]["BLUFOR"]
    assert intel, "OPFOR 인텔 항목이 있어야 한다"
    statuses = {e["status"] for e in intel}
    # 시작 직후에도 approximate가 아니라 detected 여야 채팅·공격계획 표시가 일치한다.
    assert statuses == {"detected"}, f"틱 0에서 모두 detected 여야 하는데: {statuses}"


def test_full_recon_detection_consistent_with_state():
    """detected 항목 수가 활성 OPFOR 수와 일치한다 (일부만 detected면 여전히 불일치)."""
    session = _make_session()
    engine = session.ensure_engine()

    state = engine.get_state()
    active_opfor = [
        u for u in state["units"]
        if u["side"] == "OPFOR" and u["status"] != "destroyed"
    ]
    detected = [e for e in state["intelligence"]["BLUFOR"] if e["status"] == "detected"]
    assert len(detected) == len(active_opfor), (
        f"활성 OPFOR {len(active_opfor)}개 중 detected {len(detected)}개 — 전부 탐지되어야 함"
    )
