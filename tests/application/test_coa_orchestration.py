"""COA 생성(미적용) + 실행."""
from c2.composition.container import build_session
from c2.application.simulation.replan import generate_attack_coas, execute_coa


def _session_started():
    s = build_session()   # agent=None → 규칙기반 백본
    s.ensure_engine()
    return s


def test_generate_stores_three_coas_without_applying():
    s = _session_started()
    eng = s.ensure_engine()
    # 생성 전 부대 waypoints 비어있음
    before = {u.id: list(u.waypoints) for u in eng.units if u.side == "BLUFOR"}
    res = generate_attack_coas(s)
    assert len(res["coas"]) == 3
    assert all("preview" in c for c in res["coas"])
    assert len(s.pending_coas) == 3
    # 엔진 미적용: BLUFOR waypoints 변화 없음
    after = {u.id: list(u.waypoints) for u in eng.units if u.side == "BLUFOR"}
    assert before == after, "생성 단계에서 엔진에 적용되면 안 됨"


def test_execute_coa_applies_selected():
    s = _session_started()
    eng = s.ensure_engine()
    generate_attack_coas(s)
    res = execute_coa(s, 0)
    assert res["ok"] is True
    # 실행 후 최소 1개 BLUFOR 부대가 waypoints/attack 갱신
    changed = [u for u in eng.units if u.side == "BLUFOR" and (u.waypoints or u.current_action == "attack")]
    assert changed, "COA 실행 시 엔진에 적용돼야 함"
    assert s.pending_coas == [], "실행 후 pending 비움"


def test_execute_coa_bad_index():
    s = _session_started()
    generate_attack_coas(s)
    assert execute_coa(s, 9)["ok"] is False
