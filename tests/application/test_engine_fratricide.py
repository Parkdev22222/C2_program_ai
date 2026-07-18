"""아군 오사(fratricide): 공중지원·간접사격 반경 내 아군도 피해."""

import random
import tempfile
from pathlib import Path

from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _engine(units):
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "ff.db")
    return WargameEngine(units, db=db), db


def _mk(id, side, utype, x, y, **kw):
    return Unit(id=id, side=side, unit_type=utype, x=x, y=y,
                combat_power=100.0, firepower_index=100.0, max_speed=5.0,
                status="active", waypoints=[], current_action="hold", **kw)


def test_air_support_damages_friendly_in_blast():
    random.seed(1)
    # BLUFOR 공중지원이 (10000,10000)에 투입 — 같은 편 아군이 그 반경 안에 있음
    friendly = _mk("보병1중대", "BLUFOR", "기계화보병", 10_000.0, 10_000.0)
    enemy    = _mk("적보병1중대", "OPFOR", "기계화보병", 25_000.0, 25_000.0)
    eng, db = _engine([friendly, enemy])
    eng.apply_air_support_plan({
        "air_support_plans": [{
            "call_sign": "EAGLE-1", "support_type": "cas",
            "target": [10_000, 10_000], "radius": 1_500, "delay": 0,
        }],
    })
    for _ in range(30):
        eng._tick()
    assert friendly.combat_power < 100.0, "반경 내 아군이 오사 피해를 입어야 함"
    events = db.get_recent_events(n=200)
    assert any(e["event_type"] == "FRATRICIDE_AIR" for e in events)


def test_air_support_spares_friendly_outside_blast():
    random.seed(1)
    friendly = _mk("보병1중대", "BLUFOR", "기계화보병", 3_000.0, 3_000.0)  # 반경 밖
    enemy    = _mk("적보병1중대", "OPFOR", "기계화보병", 25_000.0, 25_000.0)
    eng, db = _engine([friendly, enemy])
    eng.apply_air_support_plan({
        "air_support_plans": [{
            "call_sign": "EAGLE-1", "support_type": "strike",
            "target": [10_000, 10_000], "radius": 400, "delay": 0,
        }],
    })
    for _ in range(30):
        eng._tick()
    assert friendly.combat_power == 100.0, "반경 밖 아군은 무피해여야 함"


def test_indirect_fire_damages_friendly_in_aoe():
    random.seed(2)
    # BLUFOR 자주포가 detected 적을 사격 — 같은 편 아군이 표적 AoE 안에 있음
    spg      = _mk("자주포중대", "BLUFOR", "자주포", 8_000.0, 8_000.0, indirect_range=30_000.0)
    enemy    = _mk("적보병1중대", "OPFOR", "기계화보병", 16_000.0, 16_000.0)
    friendly = _mk("보병1중대", "BLUFOR", "기계화보병", 16_050.0, 16_050.0)  # 적 표적 바로 옆
    eng, db = _engine([spg, enemy, friendly])
    eng.full_recon = True
    for _ in range(60):
        eng._tick()
    assert friendly.combat_power < 100.0, "표적 AoE 내 아군이 오사 피해를 입어야 함"
    events = db.get_recent_events(n=300)
    assert any(e["event_type"] == "FRATRICIDE_INDIRECT" for e in events)
