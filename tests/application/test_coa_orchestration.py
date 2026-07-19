"""COA 생성(미적용) + 실행."""
from c2.composition.container import build_session
from c2.application.simulation.replan import generate_attack_coas, execute_coa, chat_send


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
    try:
        assert res["ok"] is True
        # 실행 후 최소 1개 BLUFOR 부대가 waypoints/attack 갱신
        changed = [u for u in eng.units if u.side == "BLUFOR" and (u.waypoints or u.current_action == "attack")]
        assert changed, "COA 실행 시 엔진에 적용돼야 함"
        assert s.pending_coas == [], "실행 후 pending 비움"
    finally:
        # execute_coa가 eng.start()로 시뮬 스레드를 띄우므로 반드시 정지 —
        # 백그라운드 틱이 전역 random을 소비해 결정성 테스트를 오염시키는 것 방지.
        eng.stop()


def test_execute_coa_bad_index():
    s = _session_started()
    generate_attack_coas(s)
    assert execute_coa(s, 9)["ok"] is False


def test_generate_llm_branch_does_not_mutate_engine():
    s = _session_started()
    eng = s.ensure_engine()

    # 엔진을 실제로 바꾸는 '나쁜' 에이전트 스텁 (생성 중 적용 시도 흉내)
    class _BadAgent:
        class _Inner:
            def run(self_inner, q, reset=True):
                # 생성 도중 엔진에 임무 적용을 시도(가드가 이를 되돌려야 함)
                eng.apply_mission_plan({"mission_plans": [
                    {"company_id": eng.units[0].id, "mission_type": "attack",
                     "waypoints": [[9000, 9000]]}]})
                return "{}"   # 유효 JSON 아님(mission_plans 없음) → 규칙기반 유지

        agent = _Inner()

        def reset_memory(self):
            pass

        def run(self, q, reset=False):
            return "{}"

    s.agent = _BadAgent()
    before = {u.id: list(u.waypoints) for u in eng.units if u.side == "BLUFOR"}
    generate_attack_coas(s)
    after = {u.id: list(u.waypoints) for u in eng.units if u.side == "BLUFOR"}
    assert before == after, "생성 단계에서 엔진 변경은 스냅샷 복원으로 되돌려져야 함"


def test_chat_coa_edit_converts_latlon_to_meters():
    s = build_session()
    s.ensure_engine()
    if s.planner is None:
        from c2.application.agent.mission_planner import MissionPlanner
        s.planner = MissionPlanner()
    s.set_pending_coas([{"id": "COA1", "label": "COA1", "summary": "s",
                         "plan": {"mission_plans": [{"company_id": "보병1중대", "mission_type": "attack", "waypoints": [[9000, 9000]]}]}}])
    # 위경도 waypoints를 반환하는 가짜 에이전트 (LLM이 위경도로 답하는 상황)
    class _A:
        def run(self, q, reset=False):
            return 'COA1 수정: ```json\n{"mission_plans":[{"company_id":"보병1중대","mission_type":"attack","waypoints":[[38.10,127.12]]}]}\n```'
        def reset_memory(self): pass

    s.agent = _A()
    res = chat_send(s, "COA1을 우측으로 이동")
    assert "coas" in res, "COA 수정이 반영되어 coas가 반환돼야 함"
    wp = res["coas"][0]["plan"]["mission_plans"][0]["waypoints"][0]
    # 위경도(38,127)가 아니라 미터(수천~수만)로 변환됐는지
    assert wp[0] > 1000 and wp[1] > 1000, f"위경도가 미터로 변환돼야 함: {wp}"


def test_generate_llm_timeout_falls_back_to_rulebased(monkeypatch):
    import time as _t
    from c2.composition.container import build_session
    from c2.application.simulation import replan
    from c2.application.agent.mission_planner import MissionPlanner
    monkeypatch.setattr(replan, "_COA_LLM_TIMEOUT", 0.3)   # 짧은 타임아웃
    s = build_session()
    s.ensure_engine()
    if s.planner is None:
        s.planner = MissionPlanner()
    class _Hang:
        def run(self, q, reset=True):
            _t.sleep(2.0); return "{}"   # 타임아웃보다 오래 대기
    class _A:
        agent = _Hang()
        def reset_memory(self): pass
    s.agent = _A()
    t0 = _t.time()
    res = replan.generate_attack_coas(s)
    # 타임아웃돼도 규칙기반 3개 COA 반환, 무한 대기 안 함
    assert len(res["coas"]) == 3
    assert _t.time() - t0 < 5.0, "타임아웃으로 조기 반환해야 함(무한 대기 금지)"
