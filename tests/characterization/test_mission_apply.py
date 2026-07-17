import random
import tempfile
from pathlib import Path

from c2.application.simulation.scenario import setup_cheorwon_bn
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _engine():
    random.seed(7)
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "m.db")
    return WargameEngine(setup_cheorwon_bn(), db=db)


def test_mission_plan_sets_waypoints_and_target():
    eng = _engine()
    blu_ids = [u.id for u in eng.units if u.side == "BLUFOR"]
    assert blu_ids, "BLUFOR 부대가 있어야 함"
    company = blu_ids[0]
    plan = {
        "mission_plans": [
            {
                "company_id": company,
                "mission_type": "attack",
                "waypoints": [[9000, 9000], [12000, 12000]],
                "objective": "특성화 테스트",
            }
        ]
    }
    eng.apply_mission_plan(plan)
    u = next(u for u in eng.units if u.id == company)
    assert len(u.waypoints) >= 1, "waypoints가 적용되어야 함"
    assert u.mission_lock_ticks > 0, "임무 잠금이 걸려야 함"
