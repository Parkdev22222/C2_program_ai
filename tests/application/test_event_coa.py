"""이벤트 자동 재계획이 COA 3개를 생성(미적용)하고 auto_plan_status로 노출."""
from c2.composition.container import build_session
from c2.application.simulation.replan import execute_auto_attack_plan


def test_detection_event_generates_coas_without_applying():
    s = build_session()   # agent=None → 규칙기반
    eng = s.ensure_engine()
    eng.full_recon = True
    eng._update_intelligence()
    before = {u.id: list(u.waypoints) for u in eng.units if u.side == "BLUFOR"}
    gid0 = s.auto_plan_status.get("coa_gen_id", 0)
    # 탐지 이벤트로 자동 재계획 트리거
    execute_auto_attack_plan(s, "detection", "적보병1중대", "기계화보병", 20000, 19000)
    st = s.auto_plan_status
    assert len(st.get("coas", [])) == 3, "이벤트 시 COA 3개 생성"
    assert st.get("coa_gen_id", 0) == gid0 + 1, "coa_gen_id 증가"
    assert st.get("active") is False, "생성 완료 → active False"
    assert len(s.pending_coas) == 3
    # 엔진 미적용: BLUFOR waypoints 불변
    after = {u.id: list(u.waypoints) for u in eng.units if u.side == "BLUFOR"}
    assert before == after, "이벤트 COA 생성 단계에서 엔진 미적용"
    # 시뮬 정지 유지
    assert eng.running is False
