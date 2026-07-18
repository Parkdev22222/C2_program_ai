"""통제구역: 반경 내 다수 점령 → 2곳 이상 유지 시 승리."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.domain.wargame.control_point import ControlPoint, default_control_points
from c2.application.simulation.engine import WargameEngine, _CP_HOLD_TO_WIN_TICKS
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, x, y):
    return Unit(id=id, side=side, unit_type="기계화보병", x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=[], current_action="hold")


def test_default_control_points_are_three():
    cps = default_control_points()
    assert len(cps) == 3
    assert all(isinstance(c, ControlPoint) for c in cps)


def test_presence_majority_captures_point():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cp.db")
    # BLUFOR 부대를 통제-브라보(15000,15000) 위에 배치
    blu = _mk("보병1중대", "BLUFOR", 15_000.0, 15_000.0)
    eng = WargameEngine([blu], db=db)
    eng._tick()
    state = eng.get_state()
    cps = {c["id"]: c for c in state["control_points"]}
    assert cps["통제-브라보"]["owner"] == "BLUFOR"
    assert cps["통제-브라보"]["blufor_near"] >= 1
    assert cps["통제-브라보"]["radius"] == 2000.0


def test_holding_two_points_wins():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cpwin.db")
    # BLUFOR 2부대를 통제-알파(12000,14000)·통제-브라보(15000,15000) 위에 배치, OPFOR 없음
    b1 = _mk("보병1중대", "BLUFOR", 12_000.0, 14_000.0)
    b2 = _mk("보병2중대", "BLUFOR", 15_000.0, 15_000.0)
    eng = WargameEngine([b1, b2], db=db)
    # _CP_HOLD_TO_WIN_TICKS 틱 이상 유지
    ticks = _CP_HOLD_TO_WIN_TICKS + 3
    for _ in range(ticks):
        eng._tick()
    assert eng._check_winner() == "BLUFOR", "2곳을 유지시간 이상 점령하면 승리"
