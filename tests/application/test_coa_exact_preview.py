"""COA 프리뷰 경로 == 실행 경로 (완전 일치)."""
from c2.composition.container import build_session
from c2.application.simulation.replan import generate_attack_coas, execute_coa
from c2.domain.wargame.coordinates import xy_to_latlon


def test_preview_matches_executed_route():
    s = build_session()   # agent=None → 규칙기반
    eng = s.ensure_engine()
    eng.full_recon = True
    eng._update_intelligence()
    res = generate_attack_coas(s)
    coa = res["coas"][0]
    # 실행
    execute_coa(s, 0)
    try:
        # 실행된 각 BLUFOR 부대의 waypoints(위경도 변환) == COA preview routes(현위치 제외)
        preview_by_unit = {r["unit_id"]: r["latlon"] for r in coa["preview"]["routes"]}
        for u in eng.units:
            if u.side != "BLUFOR" or u.id not in preview_by_unit:
                continue
            pv = preview_by_unit[u.id]              # [현위치, wp1, wp2, ...]
            exec_ll = [list(xy_to_latlon(p[0], p[1])) for p in u.waypoints]
            # preview의 현위치(pv[0]) 이후가 실행 waypoints와 동일해야 함
            assert pv[1:] == exec_ll, f"{u.id}: 프리뷰≠실행\n{pv[1:]}\n{exec_ll}"
    finally:
        eng.stop()


def test_context_hint_accepted():
    s = build_session()
    s.ensure_engine()
    res = generate_attack_coas(s, context_hint="테스트 트리거")
    assert len(res["coas"]) == 3
