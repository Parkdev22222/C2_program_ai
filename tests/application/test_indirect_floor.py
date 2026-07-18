"""간접포 격멸 상한: 자주포 간접사격은 CP 15% 바닥까지만."""
import random, tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine, _INDIRECT_CP_FLOOR
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, utype, x, y, **kw):
    return Unit(id=id, side=side, unit_type=utype, x=x, y=y, combat_power=100.0,
                firepower_index=130.0 if utype == "자주포" else 100.0,
                max_speed=4.0, status="active", waypoints=[], current_action="hold", **kw)


def test_indirect_fire_cannot_kill_below_floor():
    random.seed(5)
    spg   = _mk("자주포중대", "BLUFOR", "자주포", 8_000.0, 8_000.0, indirect_range=30_000.0)
    enemy = _mk("적보병1중대", "OPFOR", "기계화보병", 16_000.0, 16_000.0)
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "floor.db")
    eng = WargameEngine([spg, enemy], db=db)
    eng.full_recon = True
    # 충분히 오래 간접사격 (재보급 사이클 포함) — 적은 격멸되지 않고 바닥에서 멈춰야 함
    for _ in range(60):
        eng._tick()
    assert enemy.status != "destroyed", "간접포 단독으로는 격멸되면 안 됨"
    assert enemy.combat_power >= _INDIRECT_CP_FLOOR - 0.01, f"CP가 바닥({_INDIRECT_CP_FLOOR}) 밑으로 내려가면 안 됨: {enemy.combat_power}"
