"""기동 중 직사 교전 접촉 시 정지·교전 + 종료 후 재개."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.domain.wargame.combat import _engagement_factor
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _engine(units):
    return WargameEngine(units, db=WargameDB(db_path=Path(tempfile.mkdtemp()) / "ch.db"))


def _mk(id, side, ut, x, y, **kw):
    return Unit(id=id, side=side, unit_type=ut, x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=kw.get("wp", []), current_action=kw.get("act", "hold"))


def test_in_direct_combat_detection():
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000)
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 10800, 10000)  # 800m < 기계화보병 1500
    eng = _engine([b, o])
    assert eng._in_direct_combat(b) is o
    o.x = 22000  # 12km 밖 → 사거리 밖
    assert eng._in_direct_combat(b) is None


def test_artillery_not_in_direct_combat():
    spg = _mk("자주포중대", "BLUFOR", "자주포", 10000, 10000)
    o   = _mk("적보병1중대", "OPFOR", "기계화보병", 10500, 10000)
    eng = _engine([spg, o])
    assert eng._in_direct_combat(spg) is None   # 자주포는 직사 교전 없음
    b   = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000)
    o2  = _mk("적자주포중대", "OPFOR", "자주포", 10500, 10000)
    eng2 = _engine([b, o2])
    assert eng2._in_direct_combat(b) is None     # 적 자주포는 직사 위협 아님(제외)


def test_unit_halts_on_contact_and_resumes():
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000, wp=[[10000, 25000]], act="attack")
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 10800, 10000)  # 800m 접촉
    eng = _engine([b, o])
    y0 = b.y
    eng._move_units(30.0)
    # 정지 — waypoint 방향(y+) 전진 안 함, waypoint 보존
    assert b.y == y0
    assert b.waypoints == [[10000, 25000]]
    # 적 격멸 → 다음 틱부터 waypoint 방향 전진 재개
    o.status = "destroyed"
    eng._move_units(30.0)
    assert b.y > y0
