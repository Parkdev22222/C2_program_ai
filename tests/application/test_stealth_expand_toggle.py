"""apply_mission_plan stealth_expand 토글 + expand_plan_waypoints."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _eng(units):
    return WargameEngine(units, db=WargameDB(db_path=Path(tempfile.mkdtemp()) / "s.db"))


def _mk(id, side, x, y):
    return Unit(id=id, side=side, unit_type="기계화보병", x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=[], current_action="hold")


def test_stealth_expand_false_keeps_raw_waypoints():
    b = _mk("보병1중대", "BLUFOR", 8000, 8000)
    # 적을 두어 은밀확장이 실제로 일어날 수 있는 상황(위협원 존재)
    o = _mk("적보병1중대", "OPFOR", 16000, 16000)
    eng = _eng([b, o])
    eng.full_recon = True
    eng._update_intelligence()
    plan = {"mission_plans": [{"company_id": "보병1중대", "mission_type": "attack",
                               "waypoints": [[15000, 15000]]}]}
    eng.apply_mission_plan(plan, stealth_expand=False)
    # 재확장 없이 원본 그대로
    assert b.waypoints == [[15000.0, 15000.0]]


def test_expand_plan_waypoints_does_not_mutate_engine_and_returns_expanded():
    b = _mk("보병1중대", "BLUFOR", 8000, 8000)
    o = _mk("적보병1중대", "OPFOR", 16000, 16000)
    eng = _eng([b, o])
    eng.full_recon = True
    eng._update_intelligence()
    plan = {"mission_plans": [{"company_id": "보병1중대", "mission_type": "attack",
                               "waypoints": [[15000, 15000]]}]}
    before_wp = list(b.waypoints)
    out = eng.expand_plan_waypoints(plan)
    # 엔진 상태 불변(부대 waypoints 변화 없음)
    assert list(b.waypoints) == before_wp
    # 반환 plan의 waypoints는 확장 경로(마지막 WP는 목표 유지)
    ewps = out["mission_plans"][0]["waypoints"]
    assert ewps, "확장 waypoints가 있어야 함"
    assert [round(ewps[-1][0]), round(ewps[-1][1])] == [15000, 15000]  # 목표 유지
    # 원본 plan 불변(deepcopy)
    assert plan["mission_plans"][0]["waypoints"] == [[15000, 15000]]


def test_expand_then_apply_no_reexpand_matches():
    b = _mk("보병1중대", "BLUFOR", 8000, 8000)
    o = _mk("적보병1중대", "OPFOR", 16000, 16000)
    eng = _eng([b, o])
    eng.full_recon = True
    eng._update_intelligence()
    plan = {"mission_plans": [{"company_id": "보병1중대", "mission_type": "attack",
                               "waypoints": [[15000, 15000]]}]}
    expanded = eng.expand_plan_waypoints(plan)
    eng.apply_mission_plan(expanded, stealth_expand=False)
    # 적용된 부대 waypoints == 확장 plan의 waypoints (프리뷰와 실행 일치의 근거)
    applied = [[round(p[0]), round(p[1])] for p in b.waypoints]
    expected = [[round(p[0]), round(p[1])] for p in expanded["mission_plans"][0]["waypoints"]]
    assert applied == expected
