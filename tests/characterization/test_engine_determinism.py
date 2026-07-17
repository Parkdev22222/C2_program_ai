import random
import tempfile
from pathlib import Path

from wargame.scenario import setup_bn_vs_bn
from wargame.engine import WargameEngine
from wargame.models import WargameDB


def _run(seed: int, ticks: int) -> list:
    random.seed(seed)
    units = setup_bn_vs_bn()
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "char.db")
    eng = WargameEngine(units, db=db)
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
    a = _run(seed=42, ticks=50)
    b = _run(seed=42, ticks=50)
    assert a == b, "동일 시드에서 결과가 달라짐 — 숨은 비결정성"


def test_engine_snapshot_is_stable(snapshot_path=Path(__file__).parent / "engine_50tick_seed42.json"):
    import json
    current = _run(seed=42, ticks=50)
    if not snapshot_path.exists():
        snapshot_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return  # 최초 실행: 골든 생성
    golden = json.loads(snapshot_path.read_text(encoding="utf-8"))
    golden = [tuple(x) for x in golden]
    assert current == golden, "엔진 동작이 골든 스냅샷과 다름 (회귀)"
