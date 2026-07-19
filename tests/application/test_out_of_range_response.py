"""사거리 밖 일방 피격: CP 기준 돌입(반격)/이탈(엄폐·후방) — 양측."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _engine(units):
    return WargameEngine(units, db=WargameDB(db_path=Path(tempfile.mkdtemp()) / "oor.db"))


def _mk(id, side, ut, x, y, cp=100.0, **kw):
    return Unit(id=id, side=side, unit_type=ut, x=x, y=y, combat_power=cp,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=kw.get("wp", []), current_action=kw.get("act", "hold"))


def test_healthy_unit_charges_into_range():
    # 기계화보병(사거리~2.5km)이 3km 밖 대전차(4km 사거리)에게 일방 피격 + CP 건전 → 돌입(거리 감소)
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000, cp=100.0)
    o = _mk("적대전차중대", "OPFOR", "대전차", 13000, 10000, cp=100.0)
    eng = _engine([b, o])
    d0 = b.distance_to(o)
    eng._move_units(30.0)
    assert b.distance_to(o) < d0, "건전한 부대는 적 사거리로 돌입(거리 감소)해야 함"


def test_damaged_unit_retreats():
    # 동일 상황, CP 손상 → 이탈(거리 증가)
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000, cp=40.0)
    o = _mk("적대전차중대", "OPFOR", "대전차", 13000, 10000, cp=100.0)
    eng = _engine([b, o])
    d0 = b.distance_to(o)
    eng._move_units(30.0)
    assert b.distance_to(o) > d0, "손상된 부대는 이탈(거리 증가)해야 함"


def test_opfor_also_responds():
    # 양측 적용: OPFOR 기계화보병이 사거리 밖 BLUFOR 대전차에게 일방 피격 + CP 건전 → 돌입
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 13000, 13000, cp=100.0)
    b = _mk("대전차중대", "BLUFOR", "대전차", 10000, 13000, cp=100.0)
    eng = _engine([o, b])
    d0 = o.distance_to(b)
    eng._move_units(30.0)
    assert o.distance_to(b) < d0, "OPFOR도 CP 건전 시 돌입해야 함(양측 적용)"


def test_in_range_does_not_trigger_charge_retreat():
    # 사거리 내 교전이면 out-of-range 응답 미발동 — BLUFOR는 고지 기동(거리 급변 없음)
    b = _mk("보병1중대", "BLUFOR", "기계화보병", 10000, 10000, cp=100.0)
    o = _mk("적보병1중대", "OPFOR", "기계화보병", 10800, 10000, cp=100.0)  # 800m 사거리 내
    eng = _engine([b, o])
    d0 = b.distance_to(o)
    eng._move_units(30.0)
    # 고지 기동은 사거리 유지(교전 이탈 방지) → 거리가 크게 벌어지거나 좁혀지지 않음
    assert abs(b.distance_to(o) - d0) < 300.0
