"""자주포 탄약: 지속사격 예산 소진 → 재보급 동안 사격 정지."""
import random, tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine, _SPG_FIRE_BUDGET
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, utype, x, y, **kw):
    return Unit(id=id, side=side, unit_type=utype, x=x, y=y, combat_power=100.0,
                firepower_index=130.0 if utype == "자주포" else 100.0,
                max_speed=4.0, status="active", waypoints=[], current_action="hold", **kw)


def test_spg_stops_firing_during_resupply():
    random.seed(3)
    spg   = _mk("자주포중대", "BLUFOR", "자주포", 8_000.0, 8_000.0, indirect_range=30_000.0)
    enemy = _mk("적보병1중대", "OPFOR", "기계화보병", 16_000.0, 16_000.0)
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "ammo.db")
    eng = WargameEngine([spg, enemy], db=db)
    eng.full_recon = True
    # 예산(_SPG_FIRE_BUDGET 게임초)을 넘겨 사격 → 재보급 진입 유도
    # dt = 0.5*60 = 30게임초/틱 → budget/30 틱 + 여유
    ticks_to_deplete = int(_SPG_FIRE_BUDGET / 30) + 2
    for _ in range(ticks_to_deplete):
        eng._tick()
    assert eng.game_time < eng._spg_resupply_until.get("자주포중대", 0.0), "재보급 대기에 진입해야 함"
    cp_at_resupply = enemy.combat_power
    # 재보급 동안(쿨다운 내) 적 CP가 더 안 깎여야 함 (사격 정지)
    for _ in range(3):
        eng._tick()
    assert enemy.combat_power == cp_at_resupply, "재보급 중에는 사격이 멈춰 적 피해가 없어야 함"
    events = db.get_recent_events(n=300)
    assert any(e["event_type"] == "AMMO_RESUPPLY" for e in events)
