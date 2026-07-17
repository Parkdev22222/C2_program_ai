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

from c2.application.agent.mission_planner import build_mission_query
from c2.domain.wargame.coordinates import (
    is_latlon_coords as _is_latlon_coords,
    latlon_to_xy as _latlon_to_xy,
    waypoints_latlon_to_xy as _waypoints_latlon_to_xy,
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
