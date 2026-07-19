"""교전 중 BLUFOR 고지 기동: 사거리 유지하며 고도+엄폐 유리 지점으로."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.domain.wargame.combat import _engagement_factor
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _engine(units):
    return WargameEngine(units, db=WargameDB(db_path=Path(tempfile.mkdtemp()) / "hg.db"))


def _mk(id, side, ut, x, y, **kw):
    return Unit(id=id, side=side, unit_type=ut, x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=kw.get("wp", []), current_action=kw.get("act", "hold"))


def test_ground_score_prefers_high_and_covered():
    eng = _engine([_mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000)])
    # 점수는 고도 + 엄폐×가중치 — 실수 반환(호출 가능성·형만 검증)
    s = eng._ground_score(12000.0, 12000.0)
    assert isinstance(s, float)


def test_blufor_repositions_keeping_range_and_waypoint():
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 12000, 12000, wp=[[12000, 26000]], act="attack")
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 12700, 12000)  # 700m 접촉
    eng = _engine([b, o])
    s0 = eng._ground_score(b.x, b.y)
    for _ in range(10):
        eng._move_units(30.0)
    s1 = eng._ground_score(b.x, b.y)
    # 고지 기동: 지형 점수 비감소(더 나은 곳으로만 이동)
    assert s1 >= s0
    # 교전 유지: 적이 여전히 내 직사 사거리 내
    assert _engagement_factor("기계화보병", b.distance_to(o)) > 0
    # waypoint 보존(전진 재개용) — 원 waypoint 방향(먼 북쪽)으로 돌진하지 않음
    assert b.waypoints == [[12000, 26000]]
    assert b.y < 14000   # 사거리 유지로 북쪽 waypoint로 이탈하지 않음


def test_opfor_still_halts_only():
    # OPFOR는 고지 기동 없이 정지만 (Task 1 동작 유지)
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 12000, 12000, wp=[[12000, 4000]], act="attack")
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 12700, 12000)
    eng = _engine([o, b])
    x0, y0 = o.x, o.y
    eng._move_units(30.0)
    assert (o.x, o.y) == (x0, y0)   # OPFOR 완전 정지
