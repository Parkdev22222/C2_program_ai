"""자동 재계획 워커 + 탐지 워커 + 플랜 적용 헬퍼 (Task 29B).

과거 `ui/gradio_app.py`의 모듈 전역 함수(`_execute_auto_attack_plan`(362줄),
`_detection_worker`, 그리고 플랜 적용 헬퍼 `_convert_latlon_plan_to_meters`/
`_apply_plan_to_engine`/`_build_plan_repair_query`/`_apply_plan_with_repair`)로
흩어져 있던 "자동 재계획" 책임을 application 계층으로 이식한 것.

이 모듈은 `WargameSession`을 통해 엔진/플래너/에이전트/큐/락/상태에 접근한다.
`c2.application` 계층이므로 `c2.domain`/`c2.application`/표준 라이브러리만 import한다:

- 좌표 변환      : `c2.domain.wargame.coordinates`
- 임무 쿼리 빌드 : `c2.application.agent.mission_planner.build_mission_query`
- 에이전트       : `session.agent` (presentation에서 주입 — 여기서 import 금지)
- presentation 툴 연동(apply tracker, 학습규칙 조회 등): `session.replan_hooks`
  (미주입 시 no-op 기본값 — session.py의 `_NullReplanHooks`)

CLAUDE.md 규칙: 재계획 후 콜백 4종을 항상 재등록한다(`session._register_callbacks`).
"""

from __future__ import annotations

import logging
import queue

from c2.application.agent.mission_planner import build_mission_query, build_rule_based_coas
from c2.domain.wargame.coordinates import (
    is_latlon_coords as _is_latlon_coords,
    latlon_to_xy as _latlon_to_xy,
    waypoints_latlon_to_xy as _waypoints_latlon_to_xy,
    xy_to_latlon,
    xy_to_latlon as _xy_to_latlon,
)

logger = logging.getLogger(__name__)

# ── 임무계획 적용 실패 시 LLM 피드백 루프 ──────────────────────────
_PLAN_REPAIR_MAX_RETRIES = 2


def _convert_latlon_plan_to_meters(plan: dict) -> dict:
    """임무계획의 위경도 waypoints/target을 내부 미터 좌표로 변환."""
    plan = dict(plan)
    converted_mps = []
    for mp in plan.get("mission_plans", []):
        mp = dict(mp)
        wps = mp.get("waypoints", [])
        if wps and _is_latlon_coords(wps):
            mp["waypoints"] = _waypoints_latlon_to_xy(wps)
            logger.info(f"[좌표변환] {mp.get('company_id','?')} waypoints 위경도→미터 변환")
        converted_mps.append(mp)
    plan["mission_plans"] = converted_mps
    converted_air = []
    for asp in plan.get("air_support_plans", []):
        asp = dict(asp)
        target = asp.get("target", [])
        if len(target) == 2:
            t0, t1 = float(target[0]), float(target[1])
            if -90.0 <= t0 <= 90.0 and t0 != round(t0):
                x_m, y_m = _latlon_to_xy(t0, t1)
                asp["target"] = [x_m, y_m]
                logger.info(f"[좌표변환] {asp.get('call_sign','?')} target 위경도→미터 변환")
        converted_air.append(asp)
    plan["air_support_plans"] = converted_air
    return plan


def build_coa_preview(plan: dict, state: dict) -> dict:
    """COA plan(미터 좌표) → 지도 프리뷰용 위경도 데이터.
    routes: 각 부대 현재위치+waypoints, air_support: 공중지원 목표/반경. 순수 함수."""
    units_by_id = {u["id"]: u for u in state.get("units", [])}
    routes = []
    for mp in plan.get("mission_plans", []):
        uid = mp.get("company_id")
        u = units_by_id.get(uid)
        latlon = []
        if u is not None:
            latlon.append(list(xy_to_latlon(u.get("x", 0), u.get("y", 0))))
        for wp in mp.get("waypoints", []):
            if isinstance(wp, (list, tuple)) and len(wp) >= 2:
                latlon.append(list(xy_to_latlon(wp[0], wp[1])))
            elif isinstance(wp, dict):
                latlon.append(list(xy_to_latlon(wp.get("x", 0), wp.get("y", 0))))
        routes.append({
            "unit_id": uid,
            "color": (u.get("color") if u else None) or "#40aaff",
            "latlon": latlon,
        })
    air = []
    for sp in plan.get("air_support_plans", []):
        tgt = sp.get("target", [0, 0])
        if isinstance(tgt, dict):
            tx, ty = tgt.get("x", 0), tgt.get("y", 0)
        else:
            tx, ty = tgt[0], tgt[1]
        alat, alon = xy_to_latlon(tx, ty)
        air.append({
            "call_sign": sp.get("call_sign", ""),
            "support_type": sp.get("support_type", "cas"),
            "target": [alat, alon],
            "radius": sp.get("radius", 1500),
        })
    return {"routes": routes, "air_support": air}


_COA_DOCTRINE_HINT = {
    "frontal": "이 COA는 '정면 집중' 교리다. 통제구역 중앙을 최단·집중 확보하도록 공격부대 waypoint를 중앙 통제구역으로 지향하라.",
    "flank":   "이 COA는 '측방 기동' 교리다. 통제구역 좌우 측면을 우회로 나눠 확보하도록 부대를 좌/우로 분리해 측방 waypoint를 구성하라.",
    "fires":   "이 COA는 '화력 우선' 교리다. 공중지원·포병을 최대한 활용하고, 기동부대는 화력 투사 후 통제구역으로 진격하라.",
}


def generate_attack_coas(session) -> dict:
    """공격 COA 3개 생성(엔진 미적용). 규칙기반 백본 + (에이전트 있으면) LLM 대체.
    반환: {"coas": [...], "history": [...]}."""
    history = []
    eng = session.ensure_engine()
    if eng is None:
        return {"coas": [], "history": [("⚔️ COA 생성", "엔진 없음")]}
    # 생성 중 시뮬 일시정지(적용은 안 함)
    was_running = eng.running
    if was_running:
        eng.stop()
    state = eng.get_state()
    coas = build_rule_based_coas(state)   # 결정적 백본

    agent = session.agent
    planner = session.planner
    if agent is not None and planner is not None:
        # 생성 중 에이전트가 실수로(또는 프롬프트 무시하고) apply 툴을 호출해도
        # 엔진이 바뀌지 않도록 BLUFOR 기동 상태를 스냅샷 후 복원한다.
        _snap = {u.id: (u.x, u.y, list(u.waypoints), u.current_action,
                        u.target_unit_id, u.mission_lock_ticks, u.pursuing)
                 for u in eng.units if u.side == "BLUFOR"}
        try:
            for coa in coas:
                try:
                    query = (build_mission_query(state)
                             + "\n\n" + _COA_DOCTRINE_HINT.get(coa["doctrine"], "")
                             + "\n\n⚠️ 계획(mission_plans/air_support_plans) JSON만 출력하라. "
                               "apply/적용 툴을 호출하지 말 것(엔진 적용 금지, 생성만).")
                    agent.reset_memory()
                    raw = agent.agent.run(query, reset=True)
                    p = planner._parse_json(str(raw))
                    if p and p.get("mission_plans"):
                        # LLM이 lat/lon 반환 가능 → 미터로 변환 후 대체(미적용)
                        coa["plan"] = _convert_latlon_plan_to_meters(p)
                except Exception as _e:
                    logger.warning("[COA] LLM 생성 실패(%s) → 규칙기반 유지: %s", coa["id"], _e)
        finally:
            # 생성 단계 엔진 미적용 보장 — 스냅샷 복원
            for u in eng.units:
                if u.side == "BLUFOR" and u.id in _snap:
                    x, y, wps, act, tgt, lock, pur = _snap[u.id]
                    u.x, u.y = x, y
                    u.waypoints = wps
                    u.current_action = act
                    u.target_unit_id = tgt
                    u.mission_lock_ticks = lock
                    u.pursuing = pur
            try:
                eng._blufor_llm_units.clear()
            except Exception:
                pass

    # 프리뷰 부착
    for coa in coas:
        coa["preview"] = build_coa_preview(coa["plan"], state)

    session.set_pending_coas(coas)
    history.append(("⚔️ 공격 COA 3개 생성", f"COA1~3 생성 완료 (엔진 미적용, 버튼 클릭 시 실행)"))
    return {"coas": coas, "history": history}


def execute_coa(session, index: int) -> dict:
    """선택 COA를 엔진에 적용(실행). 성공 시 pending 비움·시뮬 재개."""
    coas = session.pending_coas
    if not coas or index < 0 or index >= len(coas):
        return {"ok": False, "error": "유효하지 않은 COA 인덱스"}
    eng = session.ensure_engine()
    if eng is None:
        return {"ok": False, "error": "엔진 없음"}
    plan = coas[index].get("plan", {})
    try:
        eng.apply_mission_plan(plan)
        if plan.get("air_support_plans"):
            eng.apply_air_support_plan(plan)
        eng.start()   # 시뮬 재개
        label = coas[index].get("id", f"COA{index+1}")
        session.clear_pending_coas()
        return {"ok": True, "executed": label}
    except Exception as e:
        logger.exception("execute_coa 오류")
        return {"ok": False, "error": str(e)}


def _apply_plan_to_engine(eng, plan: dict) -> dict:
    """플랜을 엔진에 적용(지상 + 공중지원). 위경도→미터 변환 포함, 실패 시 예외 전파."""
    plan = _convert_latlon_plan_to_meters(plan)
    eng.apply_mission_plan(plan)
    if plan.get("air_support_plans"):
        eng.apply_air_support_plan(plan)
    return plan


def _build_plan_repair_query(plan: dict, error) -> str:
    """적용 실패한 플랜과 에러 메시지를 LLM 에 되먹이는 수정 요청 쿼리."""
    import json as _json_mod
    try:
        plan_str = _json_mod.dumps(plan, ensure_ascii=False)
    except Exception:
        plan_str = str(plan)
    return (
        "직전에 생성한 BLUFOR 임무계획을 워게임에 적용하는 중 오류가 발생했습니다.\n"
        "아래 오류의 원인을 분석해 임무계획을 수정한 뒤, 수정된 전체 계획을 다시 출력하세요.\n\n"
        f"[적용 오류]\n{error}\n\n"
        f"[적용 실패한 임무계획 JSON]\n{plan_str}\n\n"
        "[수정 규칙]\n"
        "- 좌표는 미터(m) 정수이며 맵 범위 0~30000 이내여야 합니다.\n"
        "- company_id 는 현재 전장의 BLUFOR 부대 ID 와 정확히 일치해야 합니다.\n"
        "- waypoints 는 [[x, y], ...] 형식(미터)이어야 합니다.\n"
        "- target/target_unit_id 는 탐지된 OPFOR 좌표·부대 ID 여야 합니다.\n"
        "- 반드시 mission_plans 를 포함한 수정된 전체 임무계획을 JSON 블록으로만 출력하세요.\n"
    )


def _apply_plan_with_repair(eng, agent, planner, plan: dict, *, log_prefix: str = "[임무계획]",
                            max_retries: int = _PLAN_REPAIR_MAX_RETRIES):
    """플랜을 워게임에 적용하고, 실패 시 에러를 LLM 에 피드백해 수정본을 재적용한다.

    Returns:
        (applied_plan, ok). ok=True 면 엔진 적용 성공(applied_plan 은 변환·적용된 최종 플랜),
        ok=False 면 재시도 후에도 실패 → 호출측에서 규칙 기반 폴백 필요.
    """
    current = plan
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            applied = _apply_plan_to_engine(eng, current)
            if attempt > 0:
                logger.info(f"{log_prefix} LLM 수정본 재적용 성공 "
                            f"(시도 {attempt + 1}/{max_retries + 1})")
            return applied, True
        except Exception as e:
            last_err = e
            logger.warning(f"{log_prefix} 적용 실패 (시도 {attempt + 1}/{max_retries + 1}): {e}")
            if agent is None or planner is None or attempt >= max_retries:
                break
            # 에러를 LLM 에 되먹여 수정본 요청 → 파싱
            try:
                repair_query = _build_plan_repair_query(current, e)
                raw = agent.agent.run(repair_query, reset=True)
                fixed = planner._parse_json(str(raw))
            except Exception as _re:
                logger.warning(f"{log_prefix} LLM 수정 요청 실패: {_re}")
                break
            if fixed and "mission_plans" in fixed:
                current = fixed
                logger.info(f"{log_prefix} LLM 수정본 수신 → 재적용 시도")
            else:
                logger.warning(f"{log_prefix} LLM 수정본 파싱 실패 → 피드백 루프 종료")
                break
    logger.warning(f"{log_prefix} 피드백 루프 종료 — 최종 적용 실패: {last_err}")
    return current, False


def _get_recon_unit_ids(eng) -> list:
    """현재 워게임에서 BLUFOR 정찰부대 ID 목록 반환.

    정찰 병종이 없으면 빈 리스트(철원 시나리오는 정찰 없이 UAV 완전정찰).
    """
    if eng is None:
        return []
    try:
        state = eng.get_state()
        return [u["id"] for u in state.get("units", [])
                if u.get("side") == "BLUFOR" and u.get("unit_type") == "정찰"]
    except Exception:
        return []


def apply_chat_plan_if_any(eng, planner, resp_str: str) -> str:
    """채팅 응답에 air_support_plans/mission_plans JSON이 있으면 실제 엔진에 적용.

    채팅으로 포격/화력지원/타격/임무를 '지시'하면 응답의 계획을 워게임에 반영한다.
    (일반 질의응답에는 JSON이 없으므로 아무것도 적용하지 않는다.)
    반환: 적용 요약 문자열(없으면 빈 문자열).
    """
    if eng is None or planner is None:
        return ""
    try:
        plan = planner._parse_json(resp_str)
    except Exception:
        plan = None
    if not isinstance(plan, dict):
        return ""
    has_air     = bool(plan.get("air_support_plans"))
    has_mission = bool(plan.get("mission_plans"))
    if not (has_air or has_mission):
        return ""
    try:
        plan = _convert_latlon_plan_to_meters(plan)
        notes = []
        if has_mission:
            eng.apply_mission_plan(plan)
            notes.append(f"지상임무 {len(plan.get('mission_plans', []))}건")
        if has_air:
            eng.apply_air_support_plan(plan)
            notes.append(f"화력지원 {len(plan.get('air_support_plans', []))}건")
        logger.info("[채팅] 지시 계획 적용 — %s (running=%s)", ", ".join(notes), eng.running)
        run_note = "" if eng.running else " (⏸ 시뮬레이션 정지 중 — ▶ 시작 시 투사)"
        return f"✅ 워게임 적용: {', '.join(notes)}{run_note}"
    except Exception as e:
        logger.warning("[채팅] 지시 계획 적용 실패: %s", e)
        return ""


def chat_send(session, message: str, history: list = None) -> dict:
    """전술채팅 메시지 처리 — {"history": [...]} 반환 (Gradio 튜플의 두번째 원소인
    입력창 클리어용 "" 는 presentation 몫이라 dict에 포함하지 않는다).
    """
    history = list(history or [])
    if not message.strip():
        return {"history": history}
    agent = session.agent
    eng = session.ensure_engine()
    context = ""
    if eng is not None:
        state = eng.get_state()

        def _fmt_unit(u):
            lat, lon = _xy_to_latlon(u["x"], u["y"])
            _utype = u.get("unit_type") or "미상"
            return (f"  {u['side']} {u['id']}(병종:{_utype}): CP={u['combat_power']:.0f}% "
                    f"위치=(lat={lat:.4f},lon={lon:.4f}) {u['status']}")

        context = (f"[현재 워게임 상황] 게임시간={state['game_time_str']}\n"
                   + "\n".join(_fmt_unit(u) for u in state["units"]) + "\n\n")
        # 지시 처리 안내: 사용자가 화력지원/포격/타격/임무를 '지시'하면 실행 가능한 JSON을 출력하게 유도
        context += (
            "[지시 처리 규칙] 사용자가 포격·화력지원·공중지원·타격·임무 이동을 '지시'하면, "
            "실행할 계획을 아래 JSON 블록으로 출력하라(시스템이 워게임에 적용한다). "
            "support_type: artillery=포병(반경 2500), cas/strike/helicopter=항공. "
            "target/waypoints 좌표는 위 [현재 워게임 상황]의 대상 OPFOR lat/lon 을 사용. "
            "단순 질문·상황설명 요청이면 JSON 없이 평문으로 답하라.\n"
            '형식: ```json\n{"air_support_plans":[{"call_sign":"ARTY-1","support_type":"artillery",'
            '"target":[위도,경도],"radius":2500,"delay":30}]}\n```\n\n'
        )
    history.append((message, "처리 중..."))
    if agent is None:
        history[-1] = (message, "에이전트가 초기화되지 않았습니다. main.py를 통해 실행해주세요.")
        return {"history": history}
    try:
        full_query = context + message if context else message
        response = agent.run(full_query, reset=False)
        resp_str = str(response)
        # 채팅으로 화력지원/임무를 지시한 경우 → 응답 속 계획을 실제 워게임에 적용
        applied_note = apply_chat_plan_if_any(eng, session.planner, resp_str)
        if applied_note:
            resp_str = resp_str + "\n\n" + applied_note
        history[-1] = (message, resp_str)
    except Exception as e:
        logger.error(f"WG chat error: {e}", exc_info=True)
        history[-1] = (message, f"오류: {e}")
    return {"history": history}


def request_recon_plan(session, history: list = None) -> dict:
    """
    정찰 임무계획 수립 — 에이전트 툴 활용 순서
    ─────────────────────────────────────────────
    Step 1. assess_recon_need()
            └─ OPFOR 탐지 현황(detected / approximate / lost) 확인
               → 정찰 불필요 시 즉시 반환
    Step 2. recommend_recon_routes()
            └─ 정찰부대(unit_type=정찰) 경로 생성
               → apply_json, summary, mission_plans 반환
    Step 3. 최종 정찰 임무계획 JSON 직접 생성
            └─ Step 2 결과 기반 / unit_type=정찰 부대만 포함
    Step 4. apply_wargame_mission_plan(plan_json=<JSON>, dry_run=False)
            └─ 워게임 엔진에 즉시 적용 (dry_run=True 사용 금지)
    Step 5. 응답에 최종 JSON 블록 출력
    ─────────────────────────────────────────────
    금지: validate/approve 툴 호출, 공격부대(보병1중대/보병2중대/전차중대/대전차중대) 임무 부여,
          정찰+공격 임무 동시 생성

    반환: {"history": [...], "plan_text": "<json 문자열 또는 "">", "plan": dict|None}
    """
    history = list(history or [])
    eng = session.ensure_engine()
    if eng is None:
        history.append(("🔍 정찰 임무계획 요청", "워게임 초기화 실패"))
        return {"history": history, "plan_text": "", "plan": None}

    hooks = session.replan_hooks
    try:
        assessment = hooks.assess_recon_need()
    except Exception as e:
        history.append(("🔍 정찰 임무계획 요청", f"정찰 도구 로드 실패: {e}"))
        return {"history": history, "plan_text": "", "plan": None}

    opfor_sum = assessment.get("opfor_summary", {})
    if assessment.get("recommendation") == "공격 즉시 가능":
        msg = (f"**✅ 모든 OPFOR 위치가 이미 탐지되어 정찰이 불필요합니다.**\n\n탐지된 적군: {opfor_sum.get('detected', 0)}개\n\n→ **⚔️ 공격 임무계획** 버튼을 사용하여 공격을 시작하세요.")
        history.append(("🔍 정찰 임무계획 요청", msg))
        return {"history": history, "plan_text": "", "plan": None}
    if assessment.get("recommendation") == "적 없음":
        history.append(("🔍 정찰 임무계획 요청", "탐지된 적군이 없습니다."))
        return {"history": history, "plan_text": "", "plan": None}
    agent = session.agent
    agent_label = "BattlefieldAgent" if agent else "규칙 기반"
    try:
        recon_rules = hooks.get_instruction_section("RECON")
        execution_rules = hooks.get_instruction_section("EXECUTION")
        learned_rules = hooks.get_instruction_section("LEARNED_RULES")
    except Exception:
        recon_rules = execution_rules = learned_rules = ""
    learned_suffix = f"\n\n[학습된 규칙]\n{learned_rules}" if learned_rules else ""
    # ── 정찰 임무 쿼리 ────────────────────────────────────────────
    # 전장 상황(부대 위치·전투력·인텔)은 쿼리에 직접 포함하지 않는다.
    # 에이전트가 아래 tool을 순서대로 호출하여 직접 조회한다:
    #   1) assess_recon_need()        → OPFOR 탐지 현황 및 정찰 필요 여부
    #   2) recommend_recon_routes()   → 교전 회피 정찰 경로 + apply_json
    #   3) apply_wargame_mission_plan(plan_json=..., dry_run=False) → 즉시 적용
    import json as _json, re as _re
    agent_response_text = ""
    applied_plan = None
    # 정찰 경로를 미리 생성 — apply_json은 미터 좌표로 작성되어 있음
    _base_recon_result = hooks.recommend_recon_routes()
    if _base_recon_result.get("status") == "no_recon_units":
        msg = f"**⚠️ 사용 가능한 정찰부대(unit_type=정찰)가 없습니다.**\n\n{assessment.get('reason', '')}\n\n→ **⚔️ 공격 임무계획** 버튼을 사용하거나 채팅창에서 전술 조언을 요청하세요."
        history.append(("🔍 정찰 임무계획 요청", msg))
        return {"history": history, "plan_text": "", "plan": None}
    _base_apply_json = _base_recon_result.get("apply_json", "")
    _assess_json_str = _json.dumps(assessment, ensure_ascii=False)
    _recon_json_str  = _json.dumps(_base_recon_result, ensure_ascii=False, indent=2)

    recon_query = (
        f"[정찰 임무계획 수립]\n\n"
        f"⚠️ assess_recon_need() 및 recommend_recon_routes() 호출 금지 — 결과가 아래에 이미 제공됨.\n\n"
        f"[사전 계산된 결과 — assess_recon_need()]\n```json\n{_assess_json_str}\n```\n\n"
        f"[사전 계산된 결과 — recommend_recon_routes()]\n```json\n{_recon_json_str}\n```\n\n"
        f"[툴 활용 순서]\n"
        f"1. (완료) assess_recon_need() — 위 결과 참조\n"
        f"2. (완료) recommend_recon_routes() — 위 apply_json 참조\n"
        f"3. 최종 정찰 임무계획 JSON 직접 생성 (EXAONE4 담당)\n"
        f"   → recommend_recon_routes()의 unit_id·waypoints를 기반으로 최종 mission_plans JSON을 직접 작성한다.\n"
        f"   → unit_id·waypoints 좌표는 recommend_recon_routes() 결과에서 가져올 것 (임의 좌표 금지)\n"
        f"   → mission_type은 반드시 'recon', 공격부대(보병1중대/보병2중대/전차중대/대전차중대) 포함 금지\n"
        f"4. apply_wargame_mission_plan(plan_json=<Step 3에서 생성한 JSON>, dry_run=False)\n"
        f"   → 워게임 엔진에 즉시 적용 (dry_run=True 절대 금지)\n"
        f"5. 응답에 최종 JSON 블록 출력\n\n"
        f"[RECON 규칙]\n{recon_rules}\n\n"
        f"[EXECUTION 규칙]\n{execution_rules}"
        f"{learned_suffix}"
    )
    logger.debug("recon_query:\n%s", recon_query)
    history.append((f"🔍 **정찰 임무계획 생성 요청** ({agent_label})", "처리 중..."))

    # ── 시뮬레이션 일시정지 ──────────────────────────────────────────
    was_running = eng.running
    if was_running:
        eng.stop()
        logger.info("시뮬레이션 일시정지 — 정찰 임무계획 수립 중")
        try:
            hooks.set_resume_on_apply(True)  # apply_wargame_mission_plan 호출 시 자동 재개
        except Exception:
            pass

    try:
        if agent is not None:
            agent.reset_memory()  # 이전 실행 누적 토큰 제거
            try:
                agent_response_text = str(agent.run(recon_query, reset=False))
            except Exception as e:
                logger.error(f"Recon agent error: {e}", exc_info=True)
                agent_response_text = f"에이전트 오류: {e}"
            json_blocks = _re.findall(r"```json\s*(.*?)\s*```", agent_response_text, _re.DOTALL)
            for block in reversed(json_blocks):
                try:
                    parsed = _json.loads(block)
                    if "mission_plans" in parsed:
                        applied_plan = parsed  # 표시용
                        break
                except _json.JSONDecodeError:
                    pass

            # 에이전트가 tool로 적용했더라도 안전망으로 적용.
            # apply_json은 미터 좌표이므로 변환 불필요. 이중 적용해도 무해.
            if _base_recon_result.get("status") == "success" and _base_apply_json:
                try:
                    _base_plan_dict = _json.loads(_base_apply_json) if isinstance(_base_apply_json, str) else _base_apply_json
                    eng.apply_mission_plan(_base_plan_dict)
                    logger.info("[정찰임무] 안전망: 미터 좌표 apply_json 직접 적용 완료")
                except Exception as _fe:
                    logger.warning("[정찰임무] 안전망 적용 실패: %s", _fe)
                if applied_plan is None:
                    applied_plan = {"mission_plans": [{k: v for k, v in p.items() if k not in {"target_unit_id", "target_unit_ids"}} for p in _base_recon_result["mission_plans"]]}
        else:
            agent_response_text = "에이전트 미초기화 — 규칙 기반으로 정찰 경로를 생성합니다."
            if _base_recon_result.get("status") == "success":
                # apply_json은 미터 좌표로 작성되어 있으므로 변환 없이 직접 적용
                plan_dict = _json.loads(_base_apply_json) if isinstance(_base_apply_json, str) else _base_apply_json
                eng.apply_mission_plan(plan_dict)
                applied_plan = {"mission_plans": [{k: v for k, v in p.items() if k not in {"target_unit_id", "target_unit_ids"}} for p in _base_recon_result["mission_plans"]]}
        if applied_plan is None:
            history[-1] = (history[-1][0], "정찰 임무계획 생성 실패: 적용 가능한 계획이 없습니다.")
            return {"history": history, "plan_text": "", "plan": None}
        plans = applied_plan.get("mission_plans", [])
        plan_text = _json.dumps(applied_plan, ensure_ascii=False, indent=2)
        unit_lines = "\n".join(f"  - **{p['company_id']}** (정찰) → {p.get('objective', '')} ({len(p.get('waypoints', []))}개 경유지)" for p in plans)
        result_msg = (f"**🔍 정찰 임무계획 생성 완료** ({agent_label})\n\n**OPFOR 탐지 현황:**\n  - 정확히 탐지됨: {opfor_sum.get('detected', 0)}개\n  - 개략위치 파악: {opfor_sum.get('approximate', 0)}개\n  - 탐지 상실: {opfor_sum.get('lost', 0)}개\n\n**파견 정찰부대 (unit_type=정찰 한정):** {len(plans)}개\n{unit_lines}\n\n⚠️ **공격부대(보병1중대/보병2중대/전차중대/대전차중대)는 대기 중입니다.** 정찰 완료로 적 위치가 탐지되면 **⚔️ 공격 임무계획** 버튼을 눌러 공격을 개시하세요.\n\n```json\n{plan_text}\n```")
        history[-1] = (history[-1][0], result_msg)
        return {"history": history, "plan_text": plan_text, "plan": applied_plan}
    finally:
        # 에이전트가 apply_wargame_mission_plan을 호출하지 않은 경우 안전망
        if was_running and not eng.running:
            eng.start()
            logger.info("시뮬레이션 재개 (finally 안전망) — 정찰 임무계획 함수 종료")
        try:
            hooks.set_resume_on_apply(False)
        except Exception:
            pass


def request_attack_plan(session, history: list = None) -> dict:
    """
    공격 임무계획 수립 — 상황·정찰·공격위치는 미리 실행되어 프롬프트에 주입됨
    ─────────────────────────────────────────────
    사전 실행 후 [제공 데이터]로 주입 (에이전트는 이 함수들을 호출하지 않음):
      · situation_result           ← [현재 전장 상황](온톨로지) 자동 주입
      · recon_result               ← recommend_recon_routes() (build_mission_query가 실행)
      · attack_positions_result    ← get_optimal_attack_positions() (build_mission_query가 실행)
      · (assess_recon_need 결과도 참고용으로 주입)
    Step 1. 최종 임무계획 JSON 생성 (제공 데이터 기반 직접 결정)
            detected OPFOR만 목표 / 공중지원도 detected 위치에만
            CP < 30% 부대 → defend/withdraw / 나머지 → attack/flank
    Step 2. apply_wargame_mission_plan(plan_json=<JSON>, dry_run=False)
            └─ 워게임 엔진에 즉시 적용 (dry_run=True 절대 금지)
    Step 3. 응답에 최종 JSON 블록 출력
    ─────────────────────────────────────────────
    금지: validate/approve 툴 호출, 사전 주입된 툴 재호출,
          approximate/lost OPFOR 공중지원 목표 지정

    반환: {"history": [...], "plan_text": "<json 문자열 또는 "">", "plan": dict|None}
    """
    history = list(history or [])
    eng = session.ensure_engine()
    if eng is None:
        history.append(("⚔️ 공격 임무계획 요청", "워게임 초기화 실패"))
        return {"history": history, "plan_text": "", "plan": None}
    planner = session.planner
    if planner is None:
        history.append(("⚔️ 공격 임무계획 요청", "Planner 없음"))
        return {"history": history, "plan_text": "", "plan": None}
    hooks = session.replan_hooks
    warning_msg = ""
    assessment = {}
    try:
        assessment = hooks.assess_recon_need()
        opfor_sum = assessment.get("opfor_summary", {})
        detected_n = opfor_sum.get("detected", 0)
        approx_n = opfor_sum.get("approximate", 0)
        lost_n = opfor_sum.get("lost", 0)
        undetected = approx_n + lost_n
        if undetected > 0:
            warning_msg = (f"\n\n⚠️ **경고:** 적군 {undetected}개 부대의 정확한 위치가 미확인입니다. (개략위치: {approx_n}개, 탐지상실: {lost_n}개)\n탐지된 {detected_n}개 부대만을 기준으로 임무계획을 수립합니다. 정찰 후 공격을 권장합니다.")
    except Exception:
        pass
    state = eng.get_state()
    agent = session.agent
    agent_label = "BattlefieldAgent" if agent else "규칙 기반"
    import json
    try:
        attack_rules = hooks.get_instruction_section("ATTACK")
        execution_rules_atk = hooks.get_instruction_section("EXECUTION")
        learned_rules_atk = hooks.get_instruction_section("LEARNED_RULES")
    except Exception:
        attack_rules = execution_rules_atk = learned_rules_atk = ""
    learned_suffix_atk = f"\n\n[학습된 규칙]\n{learned_rules_atk}" if learned_rules_atk else ""
    _atk_recon_ids = _get_recon_unit_ids(eng)
    _atk_recon_str = ", ".join(_atk_recon_ids) if _atk_recon_ids else "정찰부대"
    base_query = build_mission_query(state)
    # assess_recon_need() 결과를 쿼리에 포함 → 에이전트 재호출 방지
    try:
        import json as _atk_json
        _assess_block = (
            f"\n\n⚠️ assess_recon_need() 호출 금지 — 결과가 아래에 이미 제공됨.\n"
            f"[사전 계산된 결과 — assess_recon_need()]\n```json\n"
            f"{_atk_json.dumps(assessment, ensure_ascii=False)}\n```\n"
        )
    except Exception:
        _assess_block = ""
    # 전장 상황은 매 판단마다 온톨로지(Neo4j)에서 자동 조회되어 쿼리 앞에 주입된다
    # (agent/battlefield_agent.py 의 _session_run 래퍼). 여기서 별도 주입하지 않는다.
    attack_suffix = (
        f"{_assess_block}"
        f"\n\n⚠️ 예시의 좌표·부대명·호출부호를 절대 그대로 사용 금지. "
        f"모든 값은 반드시 [제공 데이터]에서 가져와야 한다.\n"
        f"⚠️ {_atk_recon_str}(정찰부대)가 있으면 recon 임무로 포함. recon_result의 waypoints 사용 "
        f"(recommend_recon_routes 호출 금지 — 결과가 [제공 데이터]에 이미 있음).\n\n"
        f"[ATTACK 규칙]\n{attack_rules}\n\n"
        f"[EXECUTION 규칙]\n{execution_rules_atk}"
        f"{learned_suffix_atk}"
    )
    full_query = base_query + attack_suffix
    header_msg = f"⚔️ **공격 임무계획 생성 요청** ({agent_label}){warning_msg}"
    history.append((header_msg, "처리 중..."))

    # ── 시뮬레이션 일시정지 ──────────────────────────────────────────
    was_running = eng.running
    if was_running:
        eng.stop()
        logger.info("시뮬레이션 일시정지 — 공격 임무계획 수립 중")
        try:
            hooks.set_resume_on_apply(True)  # apply_wargame_mission_plan 호출 시 자동 재개
        except Exception:
            pass

    try:
        plan = planner.plan(state, agent=agent) if agent is None else None
        if plan is None:
            if agent is not None:
                try:
                    # 에이전트 메모리 완전 초기화 — 이전 실행 잔류 변수·로그 제거
                    agent.reset_memory()
                    try:
                        hooks.reset_apply_tracker()  # 이번 실행에서 툴 적용 여부를 정확히 판별하기 위해 초기화
                        _get_applied = hooks.get_last_applied_plan
                    except Exception:
                        _get_applied = lambda: {}
                    raw = agent.agent.run(full_query, reset=True)
                    logger.info("[공격임무계획] 에이전트 원문(preview): %s", str(raw)[:400])
                    plan = planner._parse_json(str(raw))
                    if plan and plan.get("mission_plans"):
                        # 에이전트 JSON 반환(비어있지 않음) → 적용 (실패 시 LLM 피드백 루프로 수정·재적용)
                        applied_plan, _ok = _apply_plan_with_repair(
                            eng, agent, planner, plan, log_prefix="[공격임무계획]")
                        if _ok:
                            plan = applied_plan
                        else:
                            logger.warning("[공격임무계획] 수정 재시도 실패 → 규칙 기반 폴백")
                            plan = planner._rule_based(state)
                            eng.apply_mission_plan(plan)
                            if plan.get("air_support_plans"):
                                eng.apply_air_support_plan(plan)
                    else:
                        # 유효 JSON 계획 없음 → 에이전트가 apply 툴로 직접 적용했는지 정밀 확인
                        # (조회 툴의 status:success 를 오인하지 않도록 실제 적용 계획 존재로만 판정)
                        _applied = _get_applied()
                        if _applied and _applied.get("mission_plans"):
                            plan = dict(_applied)
                            plan["_tool_applied"] = True
                            logger.info(f"[공격임무계획] 에이전트가 툴로 계획 직접 적용 "
                                        f"— {len(plan['mission_plans'])}개 중대")
                        else:
                            logger.warning(f"[공격임무계획] 유효 임무계획 없음 (raw={str(raw)[:200]}) → 규칙 기반 폴백")
                            plan = planner._rule_based(state)
                            eng.apply_mission_plan(plan)
                            if plan.get("air_support_plans"):
                                eng.apply_air_support_plan(plan)
                except Exception as _ex:
                    logger.warning(f"[공격임무계획] 에이전트 실행 실패: {_ex} → 규칙 기반 폴백")
                    plan = planner._rule_based(state)
                    eng.apply_mission_plan(plan)
                    if plan.get("air_support_plans"):
                        eng.apply_air_support_plan(plan)
            else:
                plan = planner._rule_based(state)
                eng.apply_mission_plan(plan)
                if plan.get("air_support_plans"):
                    eng.apply_air_support_plan(plan)
        else:
            # agent is None 경로 — planner가 직접 계획
            eng.apply_mission_plan(plan)
            if plan.get("air_support_plans"):
                eng.apply_air_support_plan(plan)
        plan_text = json.dumps(plan, ensure_ascii=False, indent=2)
        reasoning = plan.get("reasoning", "")
        n_plans = len(plan.get("mission_plans", []))
        n_air = len(plan.get("air_support_plans", []))
        result_msg = f"**⚔️ 공격 임무계획 생성 완료** ({agent_label})\n\n"
        if warning_msg:
            result_msg += warning_msg + "\n\n"
        if reasoning:
            result_msg += f"**판단 근거:** {reasoning}\n\n"
        result_msg += f"**지상 임무:** {n_plans}개 중대"
        if n_air:
            result_msg += f"  |  **공중지원:** {n_air}건"
        result_msg += f"\n\n```json\n{plan_text}\n```"
        history[-1] = (history[-1][0], result_msg)
        return {"history": history, "plan_text": plan_text, "plan": plan}
    finally:
        # 재계획 완료 후 탐지 트리거 초기화 → 다음 이벤트가 다시 발동 가능
        try:
            eng.clear_detection_triggers()
        except Exception:
            pass
        # 에이전트가 apply_wargame_mission_plan을 호출하지 않은 경우 안전망
        if was_running and not eng.running:
            eng.start()
            logger.info("시뮬레이션 재개 (finally 안전망) — 공격 임무계획 함수 종료")
        try:
            hooks.set_resume_on_apply(False)
        except Exception:
            pass


def evaluate_and_learn(session, history: list = None) -> dict:
    """워게임 현재 상태를 평가하고 학습된 규칙을 `session.replan_hooks.append_learned_rule`로 추가한다.

    반환: {"history": [...]}
    """
    import re as _re
    history = list(history or [])
    eng = session.ensure_engine()
    agent = session.agent
    if eng is None:
        history.append(("🧠 전술 평가", "워게임 엔진 없음"))
        return {"history": history}
    state = eng.get_state()
    hooks = session.replan_hooks

    blufor = [u for u in state["units"] if u["side"] == "BLUFOR"]
    opfor  = [u for u in state["units"] if u["side"] == "OPFOR"]
    bf_alive = [u for u in blufor if u["status"] == "active"]
    op_alive = [u for u in opfor  if u["status"] == "active"]
    bf_destroyed = [u for u in blufor if u["status"] == "destroyed"]
    op_destroyed = [u for u in opfor  if u["status"] == "destroyed"]
    winner = state.get("winner")

    # ── 주요 전투 이벤트 요약 (좌표·고도 수치는 유닛타입/방향 정보로 추상화) ──
    events = eng.db.get_recent_events(n=500)
    # 전투력 소모 이벤트만 추려서 전술 패턴 추출
    event_types_of_interest = {"COMBAT", "INDIRECT", "AIR_STRIKE", "SURPRISE",
                                "DESTROYED", "OPFOR_AI", "AIR_ORDER", "AIR_COMPLETE"}
    filtered_events = [e for e in events if e.get("event_type") in event_types_of_interest]

    # 격멸된 유닛 요약
    op_destroyed_summary = ", ".join(
        f"{u['id']}({u['unit_type']})" for u in op_destroyed
    ) or "없음"
    bf_destroyed_summary = ", ".join(
        f"{u['id']}({u['unit_type']})" for u in bf_destroyed
    ) or "없음"

    # 공중지원 사용 여부
    air_orders = [e for e in events if e.get("event_type") == "AIR_ORDER"]
    air_by_side = {"BLUFOR": [], "OPFOR": []}
    for ev in air_orders:
        msg = ev.get("message", "")
        if "[BLUFOR]" in msg:
            air_by_side["BLUFOR"].append(msg)
        elif "[OPFOR]" in msg:
            air_by_side["OPFOR"].append(msg)

    # 이벤트 메시지에서 좌표([x, y]), 고도, 특정 ID를 제거한 전술 요약 생성
    def _abstract_event(msg: str) -> str:
        """이벤트 메시지에서 구체적 수치/ID를 제거하고 전술 패턴만 남김."""
        # 좌표 제거: (12.3km, 4.5km) / (12345, 67890)
        msg = _re.sub(r'\(\d+\.?\d*km,\s*\d+\.?\d*km\)', '(위치)', msg)
        msg = _re.sub(r'\[\d+,\s*\d+\]', '[좌표]', msg)
        # 고도 수치 제거: 고도우위0.85 → 고도우위있음
        msg = _re.sub(r'고도우위[\d.]+', '고도우위', msg)
        # 거리 수치 제거: 거리1.2km → 근거리 / 중거리 / 원거리
        def dist_abstract(m):
            v = float(m.group(1))
            if v < 1.0: return "근거리"
            elif v < 3.0: return "중거리"
            else: return "원거리"
        msg = _re.sub(r'거리([\d.]+)km', dist_abstract, msg)
        # 피해 수치: -12.3% CP → 피해있음
        msg = _re.sub(r'-[\d.]+% CP', '피해', msg)
        # AoE 반경 수치 제거
        msg = _re.sub(r'AoE반경\d+m', 'AoE', msg)
        return msg

    key_events_text = "\n".join(
        f"  [{e['event_type']}] {_abstract_event(e['message'])}"
        for e in filtered_events[-60:]  # 최근 60개
    )

    summary_lines = [
        "[워게임 전술 평가 요청]",
        f"게임시간: {state['game_time_str']} | 승자: {winner or '미결'}",
        f"BLUFOR — 생존: {len(bf_alive)}/{len(blufor)}, 격멸된 아군: {bf_destroyed_summary}",
        f"OPFOR  — 생존: {len(op_alive)}/{len(opfor)},  격멸된 적군: {op_destroyed_summary}",
        f"아군 공중지원 사용: {len(air_by_side['BLUFOR'])}회 | 적군 공중지원: {len(air_by_side['OPFOR'])}회",
        "",
        "[주요 전투 이벤트 (추상화)]",
        key_events_text or "  이벤트 없음",
        "",
        "─" * 60,
        "위 전투 결과를 분석하여 다음 지침에 따라 전술 규칙을 작성하세요.",
        "",
        "■ 규칙 작성 필수 지침:",
        "  1. 규칙은 반드시 어떤 전투 상황에도 재사용 가능한 일반적 원칙으로 작성",
        "  2. 특정 좌표([x,y]), 고도 수치(m), 거리 수치(km), 특정 부대명(적보병1중대, 보병1중대 등) 절대 포함 금지",
        "  3. 부대명 대신 병종(전차, 자주포, 기계화보병, 정찰, 대전차)으로 표현",
        "  4. 수치 대신 상대적 표현 사용: '고지대', '근거리', '측방', '전방', '후방', '우세', '취약'",
        "",
        "  ✗ 나쁜 예: '고도 226m의 [13723, 14083]에서 적자주포중대(자주포) 격멸'",
        "  ✓ 좋은 예: '적 자주포보다 고지대를 선점하여 화력 우위 확보 시 자주포 격멸 효과적'",
        "",
        "  ✗ 나쁜 예: '보병1중대가 (12.3km, 4.5km)에서 적전차중대와 2km 거리 교전 시 효과적'",
        "  ✓ 좋은 예: '전차는 2~3km 거리에서 기계화보병 지원을 받아 교전 시 전투 효과 극대화'",
        "",
        "■ 출력 형식 (JSON·코드블록 불필요):",
        "  - <긍정적 전술 원칙>  (이번 전투에서 효과적이었던 패턴, 1~3개)",
        "  - <개선 필요 전술 원칙>  (이번 전투에서 문제가 된 패턴, 1~2개)",
        "",
        "규칙만 출력하고 부연 설명은 최소화하세요.",
    ]
    eval_query = "\n".join(summary_lines)

    if agent is not None:
        try:
            response = agent.run(eval_query, reset=False)
            response_text = str(response)
        except Exception as e:
            response_text = f"에이전트 평가 오류: {e}"
    else:
        response_text = (
            f"[규칙 기반 평가]\n"
            f"- BLUFOR 생존율: {len(bf_alive)/max(len(blufor),1)*100:.0f}%\n"
            f"- OPFOR 잔존: {len(op_alive)}개 부대\n"
            + ("- 승리: 현재 전술 패턴 유지 권장" if winner == "BLUFOR"
               else "- 패배 또는 미결: 정찰 강화 및 공격 분산 권장")
        )

    # ── 2차 일반화 패스: 응답에 좌표·특정 ID·수치가 남아있으면 재작성 ──
    _SPECIFIC_PATTERN = _re.compile(
        r'\[\d{3,},\s*\d{3,}\]'          # [13723, 14083] 형태 좌표
        r'|(?:Red|Blue|Alpha|Bravo|Charlie|Delta|Echo|Foxtrot)\d*'  # 구 부대명(영문)
        r'|적?(?:보병|전차|대전차|자주포|정찰)\d*중대'  # 한국어 중대명(예: 적자주포중대)
        r'|\b\d{3,}m\b'                    # 1200m 같은 수치
        r'|\(\d+\.?\d*km,\s*\d+\.?\d*km\)'  # (12.3km, 4.5km)
    )

    def _needs_generalization(rule: str) -> bool:
        return bool(_SPECIFIC_PATTERN.search(rule))

    # 2차 일반화가 필요한 규칙은 에이전트에게 재작성 요청
    raw_rules = []
    for line in response_text.splitlines():
        line = line.strip()
        if line.startswith("- ") and len(line) > 5:
            raw_rules.append(line[2:].strip())

    needs_rewrite = [r for r in raw_rules if _needs_generalization(r)]
    if needs_rewrite and agent is not None:
        rewrite_query = (
            "다음 전술 규칙들에 특정 좌표, 수치, 부대명이 포함되어 있습니다. "
            "각각을 병종·방향·상대적 거리 등의 일반적 표현으로 재작성하세요. "
            "출력은 '- <재작성된 규칙>' 형식으로만 작성하세요.\n\n"
            + "\n".join(f"- {r}" for r in needs_rewrite)
        )
        try:
            rewrite_response = agent.run(rewrite_query, reset=False)
            rewritten = []
            for line in str(rewrite_response).splitlines():
                line = line.strip()
                if line.startswith("- ") and len(line) > 5:
                    rewritten.append(line[2:].strip())
            # 원본 규칙 중 재작성된 것은 교체
            for orig, new in zip(needs_rewrite, rewritten):
                for i, r in enumerate(raw_rules):
                    if r == orig:
                        raw_rules[i] = new
                        break
        except Exception:
            pass  # 재작성 실패 시 원본 유지

    final_rules = [r for r in raw_rules if r and not _needs_generalization(r)]

    learned_count = 0
    for rule_text in final_rules:
        try:
            hooks.append_learned_rule(rule_text)
            learned_count += 1
        except Exception:
            pass

    result_msg = (
        f"**🧠 전술 평가 완료** — {learned_count}개 규칙이 `agent_custom_instructions.txt`에 추가됨\n\n"
        f"{response_text}"
    )
    history.append(("🧠 전술 평가 & 규칙 학습", result_msg))
    return {"history": history}


def execute_auto_attack_plan(session, event_type: str, *args):
    """
    신규 OPFOR 탐지 / BLUFOR CP 임계값 / BLUFOR 공중지원 피격 시 공격임무계획 재수립.
    별도 백그라운드 스레드에서 실행됨.

    event_type == "detection"    : args = (enemy_id, unit_type, x, y)
    event_type == "cp_threshold" : args = (unit_id, unit_type, threshold_pct, current_cp)
    event_type == "air_hit"      : args = (unit_id, unit_type, call_sign, current_cp)
    event_type == "target_moved" : args = (unit_id, unit_type, target_id, moved_dist)
    """
    eng = session.engine
    if eng is None:
        logger.warning("[자동임무계획] 엔진 없음 — 건너뜀")
        return

    _auto_plan_status = session.auto_plan_status

    if event_type == "detection":
        enemy_id, unit_type, x, y = args
        det_lat, det_lon = _xy_to_latlon(x, y)
        trigger_desc = (
            f"⚠️ [자동 탐지 트리거] {enemy_id}({unit_type}) 새로 탐지 "
            f"— 위치(lat={det_lat:.4f}, lon={det_lon:.4f})\n"
            f"위 위치는 참고용이며, 실제 임무계획은 반드시 아래 툴 호출 결과를 기반으로 수립하라.\n"
            f"예시의 좌표·부대명·호출부호를 절대 그대로 사용 금지."
        )
        strategy_hint = (
            f"새로 탐지된 {enemy_id}와 기존 기동 중인 BLUFOR 부대 현황을 고려하여, "
            f"어느 부대를 재배정하고 어느 부대는 기존 임무를 유지할지 조언해주세요."
        )
        log_tag = f"신규 탐지: {enemy_id}({unit_type}) @ (lat={det_lat:.4f}, lon={det_lon:.4f})"
    elif event_type == "cp_threshold":
        unit_id, unit_type, threshold_pct, current_cp = args
        trigger_desc = (
            f"⚠️ [전투력 임계값 트리거] 아군 {unit_id}({unit_type})의 전투력이 "
            f"{threshold_pct:.0f}% 이하로 저하 (현재 {current_cp:.1f}%)\n"
            f"전술적 상황을 재평가하여 임무계획을 갱신하라."
        )
        strategy_hint = (
            f"아군 {unit_id}({unit_type})의 전투력이 {threshold_pct:.0f}%로 저하되었습니다. "
            f"해당 부대를 후퇴·방어로 전환할지, 지속 임무를 부여할지, "
            f"다른 부대로 임무를 인계할지 전술적으로 판단하여 최적 임무계획을 조언해주세요."
        )
        log_tag = f"CP 임계값: {unit_id}({unit_type}) {threshold_pct:.0f}% 이하 (현재 {current_cp:.1f}%)"
    elif event_type == "target_moved":
        unit_id, unit_type, target_id, moved_dist = args
        trigger_desc = (
            f"⚠️ [표적 이동 트리거] 아군 {unit_id}({unit_type})의 담당 표적 "
            f"{target_id}이(가) 임무 발령 시점 대비 {moved_dist/1000:.1f}km 이동했다.\n"
            f"기존 경유지가 표적 현위치와 어긋났으니 공격 임무계획을 재판단하라."
        )
        strategy_hint = (
            f"아군 {unit_id}({unit_type})의 담당 표적 {target_id}이(가) "
            f"{moved_dist/1000:.1f}km 이동하여 기존 접근 경로가 유효하지 않습니다. "
            f"{target_id}의 현재 탐지 위치를 기준으로 {unit_id}의 접근 경유지·공격 방향을 "
            f"재산정하고, 필요 시 담당 표적 재지정 여부도 판단하여 조언해주세요."
        )
        log_tag = f"표적 이동: {unit_id}({unit_type}) 담당표적 {target_id} {moved_dist/1000:.1f}km 이동"
    else:  # air_hit
        unit_id, unit_type, call_sign, current_cp = args
        trigger_desc = (
            f"⚠️ [공중지원 피격 트리거] 아군 {unit_id}({unit_type})이 "
            f"적 공중지원({call_sign})에 피격 (현재 전투력 {current_cp:.1f}%)\n"
            f"공중지원 피격으로 전술 상황이 변경되었다. 임무계획을 즉시 재평가하라."
        )
        strategy_hint = (
            f"아군 {unit_id}({unit_type})이 적 공중지원({call_sign})에 피격당했습니다 "
            f"(현재 전투력 {current_cp:.1f}%). "
            f"피격 부대의 임무 지속 가능 여부를 판단하고, 필요 시 후퇴·방어 전환 또는 "
            f"다른 부대로 임무를 인계하는 방안을 조언해주세요."
        )
        log_tag = f"공중지원 피격: {unit_id}({unit_type}) by {call_sign} (현재 CP {current_cp:.1f}%)"

    logger.info(f"[자동임무계획] {log_tag} — running={eng.running}")

    # UI 팝업용 상태 플래그 설정
    import time as _t_status
    _auto_plan_status["active"] = True
    _auto_plan_status["message"] = log_tag
    _auto_plan_status["started_at"] = _t_status.time()

    # 진행 중인 공중지원(pending/active)이 완료될 때까지 대기한 후 정지
    # 직접사격·간접사격은 틱 내 즉시 처리되므로 대기 불필요
    was_running = eng.running
    if was_running:
        import time as _time
        _COMBAT_WAIT_MAX = 120.0   # 최대 2분 대기
        _waited = 0.0
        _wait_step = 0.5
        while _waited < _COMBAT_WAIT_MAX:
            try:
                _air_ongoing = [
                    a for a in eng.get_state().get("air_supports", [])
                    if a.get("status") in ("pending", "active")
                ]
            except Exception:
                _air_ongoing = []
            if not _air_ongoing:
                break
            _time.sleep(_wait_step)
            _waited += _wait_step
        if _waited > 0:
            logger.info(f"[자동임무계획] 공중지원 완료 대기 {_waited:.1f}s 후 일시정지")
        eng.stop()
        logger.info(f"[자동임무계획] 시뮬레이션 일시정지 완료 — running={eng.running}")
        # set_resume_on_apply 사용 안 함 — LLM 툴 호출 시 엔진이 중간에 재시작되어
        # "배너 표시 중인데 시뮬레이션 돌아가는" 문제 발생. finally에서 일괄 재시작.
    else:
        logger.info("[자동임무계획] 시뮬레이션이 이미 정지 상태")

    planner = session.planner
    if planner is None:
        logger.warning("[자동임무계획] planner 없음 → 재개")
        _auto_plan_status["active"] = False
        _auto_plan_status["message"] = ""
        if was_running:
            eng.start()
        return

    agent = session.agent
    hooks = session.replan_hooks

    try:
        state = eng.get_state()

        # ── 현재 각 BLUFOR 부대의 임무 상태 요약 → 에이전트에 제공 ──────
        # apply_mission_plan()은 plan에 포함된 부대만 업데이트하므로,
        # 에이전트가 특정 부대를 plan에 넣지 않으면 그 부대는 기존 임무를 유지한다.
        import math as _math
        intel_index = {
            e["unit_id"]: e
            for e in state.get("intelligence", {}).get("BLUFOR", [])
        }
        current_mission_lines = []
        for u in state.get("units", []):
            if u["side"] != "BLUFOR" or u["status"] == "destroyed":
                continue
            wps = u.get("waypoints", [])
            action = u.get("current_action", "대기")
            if wps:
                final_wp = wps[-1]
                # 잔여 WP 최종 지점에서 가장 가까운 탐지 OPFOR 찾기
                nearest_opfor = None
                nearest_dist = float("inf")
                for e in intel_index.values():
                    if e["status"] not in ("detected", "approximate"):
                        continue
                    d = _math.hypot(final_wp[0] - e["known_x"], final_wp[1] - e["known_y"])
                    if d < nearest_dist:
                        nearest_dist = d
                        nearest_opfor = e["unit_id"]
                if nearest_opfor and nearest_dist < 8_000:
                    status_str = (
                        f"기동 중({action}) → 목표방향: {nearest_opfor} "
                        f"(거리 {int(nearest_dist/1000*10)/10}km), 잔여WP {len(wps)}개"
                    )
                else:
                    status_str = f"기동 중({action}), 잔여WP {len(wps)}개 (목표 미확인)"
            else:
                status_str = "유휴 (웨이포인트 없음)"
            current_mission_lines.append(f"  • {u['id']}: {status_str}")

        current_mission_summary = "\n".join(current_mission_lines)

        try:
            attack_rules      = hooks.get_instruction_section("ATTACK")
            execution_rules   = hooks.get_instruction_section("EXECUTION")
            learned_rules     = hooks.get_instruction_section("LEARNED_RULES")
        except Exception:
            attack_rules = execution_rules = learned_rules = ""

        learned_suffix = f"\n\n[학습된 규칙]\n{learned_rules}" if learned_rules else ""
        _recon_ids = _get_recon_unit_ids(eng)
        _recon_id_str = ", ".join(_recon_ids) if _recon_ids else "정찰부대"
        base_query = build_mission_query(state)

        # 전장 상황은 매 판단마다 온톨로지(Neo4j)에서 자동 조회되어 쿼리 앞에 주입된다
        # (agent/battlefield_agent.py 의 _session_run 래퍼). 여기서 별도 주입하지 않는다.
        full_query = (
            f"⛔ [최우선 지시 — 반드시 준수]\n"
            f"1. 모든 툴 호출을 완료하기 전에 절대 final_answer()를 호출하지 말 것.\n"
            f"2. {_recon_id_str}는 recon 임무로 mission_plans에 포함. 나머지 부대는 공격임무(attack/defend/flank/withdraw/hold) 부여.\n"
            f"3. recon_result / attack_positions_result 는 아래 [제공 데이터]의 JSON을 그대로 사용 — recommend_recon_routes·get_optimal_attack_positions·recon_advisor_tool 호출 금지.\n"
            f"4. 전장 상황은 자동 주입된 [현재 전장 상황](온톨로지)을 사용 — 별도 상황 조회 툴은 없음.\n"
            f"5. apply_wargame_mission_plan(dry_run=False) 호출 후 즉시 final_answer() 호출하고 종료. 추가 툴 호출 절대 금지.\n"
            + base_query
            + f"\n\n{trigger_desc}\n\n"
            f"[현재 BLUFOR 부대별 임무 현황]\n"
            f"{current_mission_summary}\n\n"
            f"⚠️ [선택적 임무 재배정 규칙]\n"
            f"   • mission_plans에 포함된 부대만 새 임무를 받는다.\n"
            f"   • 포함하지 않은 부대는 위 현황의 기존 임무를 그대로 유지한다.\n"
            f"   • 기존 목표 OPFOR가 격멸되거나 위협이 낮으면 새 목표로 재배정 고려\n"
            f"   • 이미 교전 중이거나 목표까지 거리가 짧으면 기존 임무 유지 고려\n"
            f"   • CP임계값 이하 부대는 후퇴·방어 전환 또는 임무 인계 고려\n\n"
            f"[ATTACK 규칙]\n{attack_rules}\n\n"
            f"[EXECUTION 규칙]\n{execution_rules}"
            f"{learned_suffix}"
        )

        if agent is not None:
            try:
                import threading as _thr
                import time as _t_session
                _AGENT_TIMEOUT = 900  # 자동 재계획 최대 대기 시간 (초)
                _session_start = _t_session.time()

                # 이 세션 시작 전 apply 기록 초기화
                try:
                    hooks.reset_apply_tracker()
                except Exception:
                    pass

                # 에이전트 메모리 완전 초기화 — 반복 실행 시 토큰 누적 방지
                agent.reset_memory()
                logger.info("[자동임무계획] 에이전트 메모리 초기화 완료")

                _raw_holder: list = [None]
                _err_holder: list = [None]
                _done_evt = _thr.Event()

                def _run_agent_thread():
                    try:
                        _raw_holder[0] = agent.agent.run(full_query, reset=True)
                    except Exception as _te:
                        _err_holder[0] = _te
                    finally:
                        _done_evt.set()

                _agent_t = _thr.Thread(target=_run_agent_thread, daemon=True, name="auto-plan-agent")
                _agent_t.start()
                _finished = _done_evt.wait(timeout=_AGENT_TIMEOUT)
                if not _finished:
                    logger.warning(f"[자동임무계획] 에이전트 타임아웃 ({_AGENT_TIMEOUT}s) → 스레드 중단 시도")
                    try:
                        import ctypes as _ctypes
                        _tid = _agent_t.ident
                        if _tid is not None:
                            _res = _ctypes.pythonapi.PyThreadState_SetAsyncExc(
                                _ctypes.c_ulong(_tid),
                                _ctypes.py_object(SystemExit),
                            )
                            if _res == 1:
                                logger.info("[자동임무계획] SystemExit 주입 완료 — 스레드 종료 대기 (최대 5s)")
                                _agent_t.join(timeout=5.0)
                            elif _res > 1:
                                _ctypes.pythonapi.PyThreadState_SetAsyncExc(
                                    _ctypes.c_ulong(_tid), None)
                                logger.warning("[자동임무계획] PyThreadState_SetAsyncExc 다중 매칭 — 롤백")
                    except Exception as _kill_err:
                        logger.debug(f"[자동임무계획] 스레드 중단 실패 (무시): {_kill_err}")
                    raise RuntimeError("agent timeout")
                if _err_holder[0]:
                    raise _err_holder[0]
                raw = _raw_holder[0]

                # 에이전트 실행 완료 후 대형 객체(step_logs, 생성 텍스트) GC 해제
                try:
                    import gc as _gc
                    _gc.collect()
                except Exception:
                    pass

                # 툴 호출로 이미 적용됐는지 타임스탬프로 판단
                try:
                    _tool_applied = hooks.was_plan_applied_since(_session_start)
                except Exception:
                    _tool_applied = False

                logger.info("[자동임무계획] 에이전트 원문(preview): %s", str(raw)[:400])
                plan = planner._parse_json(str(raw))
                if plan and plan.get("mission_plans"):
                    # 에이전트 JSON 반환(비어있지 않음) → 적용 (실패 시 LLM 피드백 루프로 수정·재적용)
                    applied_plan, _ok = _apply_plan_with_repair(
                        eng, agent, planner, plan, log_prefix="[자동임무계획]")
                    if _ok:
                        plan = applied_plan
                        logger.info(f"[자동임무계획] 에이전트 계획 적용 완료 "
                                    f"— {len(plan.get('mission_plans', []))}개 중대 재배정")
                    else:
                        logger.warning("[자동임무계획] 수정 재시도 실패 → 규칙 기반 폴백")
                        plan = planner._rule_based(state)
                        eng.apply_mission_plan(plan)
                        if plan.get("air_support_plans"):
                            eng.apply_air_support_plan(plan)
                elif _tool_applied:
                    # 에이전트가 apply_wargame_mission_plan 툴을 직접 호출해 이미 적용 완료
                    # → 툴이 보관한 실제 적용 계획을 표시용으로 회수
                    try:
                        _applied = hooks.get_last_applied_plan()
                    except Exception:
                        _applied = {}
                    if _applied and _applied.get("mission_plans"):
                        plan = dict(_applied)
                        plan["_tool_applied"] = True
                    logger.info(f"[자동임무계획] 에이전트가 툴로 계획 직접 적용 완료 — 폴백 불필요 "
                                f"({len((_applied or {}).get('mission_plans', []))}개 중대)")
                else:
                    logger.warning(f"[자동임무계획] 에이전트 미적용 → 규칙 기반 폴백")
                    plan = planner._rule_based(state)
                    eng.apply_mission_plan(plan)
                    if plan.get("air_support_plans"):
                        eng.apply_air_support_plan(plan)
                        logger.info(f"[자동임무계획] 규칙 기반 공중지원 {len(plan['air_support_plans'])}회 적용")
            except Exception as _e:
                logger.warning(f"[자동임무계획] 에이전트 실행 실패: {_e} → 규칙 기반 폴백")
                plan = planner._rule_based(state)
                eng.apply_mission_plan(plan)
                if plan.get("air_support_plans"):
                    eng.apply_air_support_plan(plan)
                    logger.info(f"[자동임무계획] 규칙 기반 공중지원 {len(plan['air_support_plans'])}회 적용")
        else:
            plan = planner._rule_based(state)
            eng.apply_mission_plan(plan)
            if plan.get("air_support_plans"):
                eng.apply_air_support_plan(plan)
                logger.info(f"[자동임무계획] 규칙 기반 공중지원 {len(plan['air_support_plans'])}회 적용")
            logger.info("[자동임무계획] 규칙 기반 계획 적용")

    except Exception as _ex:
        logger.error(f"[자동임무계획] 오류: {_ex}", exc_info=True)
        try:
            state = eng.get_state()
            plan = planner._rule_based(state)
            eng.apply_mission_plan(plan)
            if plan.get("air_support_plans"):
                eng.apply_air_support_plan(plan)
                logger.info(f"[자동임무계획] 규칙 기반 공중지원 {len(plan['air_support_plans'])}회 적용")
            logger.info("[자동임무계획] 오류 후 규칙 기반 폴백 적용")
        except Exception as _fb_ex:
            logger.error(f"[자동임무계획] 폴백도 실패: {_fb_ex}")
    finally:
        # UI 팝업 상태 해제
        _auto_plan_status["active"] = False
        _auto_plan_status["message"] = ""
        # 재계획 완료 후 탐지 트리거 초기화 → 다음 OPFOR 탐지/이벤트가 다시 발동 가능
        try:
            eng.clear_detection_triggers()
        except Exception:
            pass
        # 콜백 재등록 — 재계획 도중 엔진이 교체됐을 경우를 대비해 현재 세션 엔진에 재등록
        try:
            _cur_eng = session.engine
            if _cur_eng is not None:
                session._register_callbacks(_cur_eng)
        except Exception:
            pass
        if was_running and not eng.running:
            eng.start()
            logger.info("[자동임무계획] 시뮬레이션 재개")
        try:
            hooks.set_resume_on_apply(False)
        except Exception:
            pass


def detection_worker(session):
    """백그라운드 데몬 스레드 — 세션 탐지 큐를 소비하여 자동 임무계획 수립.

    30틱 쿨다운: 마지막 재계획 완료 후 30틱 이내에 발생한 이벤트는 모두 무시한다.
    동시 발생 이벤트 배치: 큐에 쌓인 추가 이벤트를 한번에 드레인하여 중복 재계획 방지.
    """
    q = session.detection_queue
    while not session._worker_stop.is_set():
        try:
            event = q.get(timeout=2.0)
        except queue.Empty:
            continue

        # 큐에 쌓인 추가 이벤트를 모두 드레인 (30틱 내 복수 이벤트 → 1회 처리)
        extra_count = 0
        try:
            while True:
                q.get_nowait()
                extra_count += 1
        except queue.Empty:
            pass
        if extra_count:
            logger.info(f"[자동임무계획] 동시 발생 이벤트 {extra_count}개 병합 → 1회 재계획 처리")

        # 30틱 쿨다운 확인
        eng = session.engine
        if eng is not None:
            ticks_since_last = eng.tick - session.last_replan_tick
            if ticks_since_last < 30:
                logger.info(
                    f"[자동임무계획] 30틱 쿨다운 — 마지막 재계획 후 {ticks_since_last}틱 경과, "
                    f"{event[0]} 이벤트 건너뜀"
                )
                continue

        # 동시 계획 방지: 이미 계획 중이면 이벤트 무시
        if not session._auto_plan_lock.acquire(blocking=False):
            logger.info(f"[자동임무계획] 계획 수립 중 — {event[0]} 이벤트 건너뜀")
            continue
        try:
            execute_auto_attack_plan(session, *event)
        finally:
            if eng is not None:
                session.last_replan_tick = eng.tick
            session._auto_plan_lock.release()
