"""커스텀 통제구역: set_control_points + 시나리오 설정 반영."""
import tempfile
from pathlib import Path

from c2.domain.wargame.unit import Unit
from c2.domain.wargame.control_point import ControlPoint
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, x, y):
    return Unit(id=id, side=side, unit_type="기계화보병", x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=[], current_action="hold")


def test_set_control_points_overrides_defaults():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cpc.db")
    eng = WargameEngine([_mk("보병1중대", "BLUFOR", 1000.0, 1000.0)], db=db)
    eng.set_control_points([
        ControlPoint("사용자CP1", 9000.0, 9000.0),
        ControlPoint("사용자CP2", 10000.0, 10000.0),
    ])
    state = eng.get_state()
    ids = [c["id"] for c in state["control_points"]]
    assert ids == ["사용자CP1", "사용자CP2"]
    assert state["control_points"][0]["x"] == 9000.0
    # 소유·타이머 초기화 확인
    assert all(c["owner"] is None for c in state["control_points"])


def test_set_control_points_empty_reverts_to_default():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cpc2.db")
    eng = WargameEngine([_mk("보병1중대", "BLUFOR", 1000.0, 1000.0)], db=db)
    eng.set_control_points([ControlPoint("X", 5000.0, 5000.0)])
    eng.set_control_points([])  # 빈 리스트 → 기본 3곳 복귀
    state = eng.get_state()
    assert len(state["control_points"]) == 3
    assert {c["id"] for c in state["control_points"]} == {"통제-알파", "통제-브라보", "통제-찰리"}


def test_custom_cp_capture_and_win_uses_custom_position():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cpc3.db")
    # BLUFOR 2부대를 커스텀 CP 2곳 위에 두면 그 위치로 점령돼야 함
    b1 = _mk("보병1중대", "BLUFOR", 9000.0, 9000.0)
    b2 = _mk("보병2중대", "BLUFOR", 10000.0, 10000.0)
    eng = WargameEngine([b1, b2], db=db)
    eng.set_control_points([
        ControlPoint("사용자CP1", 9000.0, 9000.0),
        ControlPoint("사용자CP2", 10000.0, 10000.0),
    ])
    eng._tick()
    owners = {c["id"]: c["owner"] for c in eng.get_state()["control_points"]}
    assert owners["사용자CP1"] == "BLUFOR"
    assert owners["사용자CP2"] == "BLUFOR"
