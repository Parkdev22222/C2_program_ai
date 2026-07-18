"""OPFOR 탈환: BLUFOR가 통제구역 확보 시 OPFOR 전 부대가 탈환 기동."""
import tempfile
from pathlib import Path
from c2.domain.wargame.unit import Unit
from c2.application.simulation.engine import WargameEngine
from c2.infrastructure.persistence.sqlite_event_store import WargameDB


def _mk(id, side, x, y, utype="기계화보병"):
    return Unit(id=id, side=side, unit_type=utype, x=x, y=y, combat_power=100.0,
                firepower_index=100.0, max_speed=5.0, status="active",
                waypoints=[], current_action="hold")


def test_opfor_retakes_when_blufor_holds_cp():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "retake.db")
    # BLUFOR 부대가 통제-브라보(15000,15000) 확보, OPFOR 부대는 북동부에서 대기
    blu = _mk("보병1중대", "BLUFOR", 15_000.0, 15_000.0)
    opf = _mk("적보병1중대", "OPFOR", 22_000.0, 22_000.0)
    eng = WargameEngine([blu, opf], db=db)
    eng.full_recon = True
    # 통제구역 점령 반영(1틱) + OPFOR AI 주기(60게임초=2틱) 이상 실행
    for _ in range(5):
        eng._tick()
    assert eng._opfor_retaking is True, "BLUFOR 확보 시 OPFOR 탈환 상태여야 함"
    # OPFOR 부대가 확보 CP(브라보) 방향으로 기동 지시받아야 함
    assert opf.current_action == "attack"
    assert opf.waypoints and opf.waypoints[0] == [15_000.0, 15_000.0]


def test_opfor_retaking_resets_when_cp_lost():
    db = WargameDB(db_path=Path(tempfile.mkdtemp()) / "retake2.db")
    blu = _mk("보병1중대", "BLUFOR", 15_000.0, 15_000.0)
    opf = _mk("적보병1중대", "OPFOR", 22_000.0, 22_000.0)
    eng = WargameEngine([blu, opf], db=db)
    eng.full_recon = True
    for _ in range(5):
        eng._tick()
    assert eng._opfor_retaking is True
    # BLUFOR 격멸 → CP 확보 해제 → 탈환 상태 리셋
    # NOTE: 통제구역 소유권 갱신(_update_control_points, Task 1 코드)은
    # "동수/무부대 → 이전 소유 유지" 정책이라 부대가 격멸돼도 실제 OPFOR가
    # 물리적으로 재점령하기 전까지는 소유권이 자동으로 풀리지 않는다(테스트
    # 틱 범위 내에 OPFOR가 브라보까지 도달하는 것은 불가능한 거리/속도).
    # 여기서는 "BLUFOR가 CP를 더 이상 확보하지 못하는 상태"를 직접 구성해
    # (부대 격멸 + 소유권 해제) 탈환 상태 리셋 로직 자체만 검증한다.
    blu.status = "destroyed"
    eng._cp_owner["통제-브라보"] = None
    for _ in range(5):
        eng._tick()
    assert eng._opfor_retaking is False, "BLUFOR가 CP를 내주면 탈환 상태 해제"
