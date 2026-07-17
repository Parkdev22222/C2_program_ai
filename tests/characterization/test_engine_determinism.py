import random
import tempfile
from pathlib import Path

from c2.application.simulation.scenario import setup_cheorwon_bn
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB

# 900틱 동안 BLUFOR 공격부대가 OPFOR 표적을 향해 실제로 진격·교전하도록
# 강제하는 임무계획. 좌표는 setup_cheorwon_bn()의 OPFOR 초기 배치 좌표.
# 이 임무계획이 없으면 부대가 hold 상태로 정지해 있어 combat_power/status가
# 스폰 값 그대로 유지되고, 교전·피해 로직이 전혀 특성화되지 않는다.
_CONTACT_PLAN = {
    "mission_plans": [
        {"company_id": "전차중대", "mission_type": "attack", "target_unit_id": "적전차중대",
         "waypoints": [[23500, 20500]]},
        {"company_id": "보병1중대", "mission_type": "attack", "target_unit_id": "적보병1중대",
         "waypoints": [[21000, 19000]]},
        {"company_id": "보병2중대", "mission_type": "attack", "target_unit_id": "적보병2중대",
         "waypoints": [[21000, 22500]]},
        {"company_id": "보병3중대", "mission_type": "attack", "target_unit_id": "적보병3중대",
         "waypoints": [[18000, 18500]]},
        {"company_id": "대전차중대", "mission_type": "attack", "target_unit_id": "적대전차중대",
         "waypoints": [[24000, 23000]]},
    ],
}


def _run(seed: int, ticks: int) -> list:
    random.seed(seed)
    units = setup_cheorwon_bn()
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "char.db")
    eng = WargameEngine(units, db=db)
    # BLUFOR 공격부대를 OPFOR 쪽으로 진격시켜 실제 교전(피해 누적·상태 전이)이
    # 일어나도록 한다 — hold 상태로는 combat_power/status 로직이 전혀 행사되지 않는다.
    eng.apply_mission_plan(_CONTACT_PLAN)
    for _ in range(ticks):
        eng._tick()
    state = eng.get_state()
    # 부대별 (id, 위치, 전투력, 상태)만 뽑아 안정적 스냅샷 구성
    snap = sorted(
        (u["id"], round(u["x"]), round(u["y"]),
         round(u["combat_power"], 1), u["status"])
        for u in state["units"]
    )
    return snap


def test_engine_is_deterministic_under_fixed_seed():
    a = _run(seed=42, ticks=900)
    b = _run(seed=42, ticks=900)
    assert a == b, "동일 시드에서 결과가 달라짐 — 숨은 비결정성"


def test_engine_snapshot_is_stable(snapshot_path=Path(__file__).parent / "engine_900tick_seed42.json"):
    import json
    current = _run(seed=42, ticks=900)
    if not snapshot_path.exists():
        snapshot_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return  # 최초 실행: 골든 생성
    golden = json.loads(snapshot_path.read_text(encoding="utf-8"))
    golden = [tuple(x) for x in golden]
    assert current == golden, "엔진 동작이 골든 스냅샷과 다름 (회귀)"
