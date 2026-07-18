"""대포병 shoot-and-scoot: 정적 사격 지속 시 자주포 자신이 피해."""
import random, tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import (
    WargameEngine,
    _CB_EXPOSURE_DELAY,
    _CB_RAMP,
    _SPG_FIRE_BUDGET,
    _SPG_RESUPPLY_COOLDOWN,
)
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, utype, x, y, **kw):
    return Unit(id=id, side=side, unit_type=utype, x=x, y=y, combat_power=100.0,
                firepower_index=130.0 if utype == "자주포" else 100.0,
                max_speed=4.0, status="active", waypoints=[], current_action="hold", **kw)


def test_static_firing_spg_takes_counter_battery_damage():
    random.seed(4)
    spg   = _mk("자주포중대", "BLUFOR", "자주포", 8_000.0, 8_000.0, indirect_range=30_000.0)
    enemy = _mk("적보병1중대", "OPFOR", "기계화보병", 16_000.0, 16_000.0)
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cb.db")
    eng = WargameEngine([spg, enemy], db=db)
    eng.full_recon = True
    # _CB_EXPOSURE_DELAY(게임초) 넘게 같은 자리서 사격 → 대포병 피해.
    # 로그(_INDIRECT_LOG_THRESHOLD) 이벤트가 남으려면 램프(_CB_RAMP)로 누적되는 피해가
    # 임계값까지 쌓여야 하고, 그 사이 Task-1 탄약 소진(_SPG_FIRE_BUDGET) → 재보급
    # (_SPG_RESUPPLY_COOLDOWN) 한 사이클을 거치며 사격이 잠시 멈출 수 있으므로
    # 넉넉히 그 시간까지 포함해 틱 수를 계산한다.
    ticks = int((_SPG_FIRE_BUDGET + _SPG_RESUPPLY_COOLDOWN + _CB_EXPOSURE_DELAY + _CB_RAMP) / 30) + 5
    for _ in range(ticks):
        eng._tick()
    assert spg.combat_power < 100.0, "정적 사격 자주포는 대포병 피해를 입어야 함"
    assert eng._spg_static_fire.get("자주포중대", 0.0) > _CB_EXPOSURE_DELAY
    events = db.get_recent_events(n=400)
    assert any(e["event_type"] == "COUNTER_BATTERY" for e in events)


def test_moving_resets_static_timer():
    random.seed(4)
    spg = _mk("자주포중대", "BLUFOR", "자주포", 8_000.0, 8_000.0, indirect_range=30_000.0)
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "cb2.db")
    eng = WargameEngine([spg], db=db)
    eng.full_recon = True
    # 표적 없음 → 사격 안 함 → 정적 타이머 누적 안 됨
    for _ in range(6):
        eng._tick()
    assert eng._spg_static_fire.get("자주포중대", 0.0) == 0.0
    assert spg.combat_power == 100.0
