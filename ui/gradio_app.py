"""
C2 군사 AI - Gradio 웹 인터페이스
"""
import re
import time
import logging
import gradio as gr
import yaml
from pathlib import Path
from typing import List, Optional, Tuple, Generator

try:
    import plotly.graph_objects as go
    _PLOTLY_OK = True
except ImportError:
    _PLOTLY_OK = False

try:
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    # 기본 시나리오: 철원 축선 기계화대대 교전 (6v6, 정찰→보병, UAV 완전정찰, 자주포 실사거리)
    from wargame import WargameEngine, setup_cheorwon_bn as setup_bn_vs_bn
    from wargame.llm_planner import MissionPlanner
    from wargame.terrain import get_heightmap, GRID_W, GRID_H, MAP_W, MAP_H
    from c2.application.simulation.session import WargameSession
    _WARGAME_OK = True
except Exception as _wg_err:
    _WARGAME_OK = False
    _wg_err = str(_wg_err)

try:
    from tools.coord_utils import (
        xy_to_latlon as _xy_to_latlon,
        is_latlon_coords as _is_latlon_coords,
        waypoints_latlon_to_xy as _waypoints_latlon_to_xy,
        latlon_to_xy as _latlon_to_xy,
    )
    _COORD_OK = True
except Exception:
    _COORD_OK = False
    def _xy_to_latlon(x, y):
        return (y / 111000.0 + 38.0, x / 88645.0 + 127.0)
    def _is_latlon_coords(wps):
        if not wps: return False
        v = wps[0][0] if isinstance(wps[0], (list, tuple)) else 0
        return -90.0 <= float(v) <= 90.0 and float(v) != round(float(v))
    def _waypoints_latlon_to_xy(wps):
        return wps
    def _latlon_to_xy(lat, lon):
        return int((lon - 127.0) * 88645), int((lat - 38.0) * 111000)

logger = logging.getLogger(__name__)


# Task 29C: 위경도→미터 변환 + 플랜적용-with-repair 헬퍼는
# `c2.application.simulation.replan`(`_convert_latlon_plan_to_meters`/
# `_apply_plan_to_engine`/`_build_plan_repair_query`/`_apply_plan_with_repair`)로
# 완전히 이관됐다. gradio는 `_session.request_attack_plan()`/`_session.chat_send()`
# 등 세션 메서드를 통해 이 헬퍼들을 간접 사용한다.

CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_ui_config() -> dict:
    with open(CONFIG_DIR / "agent_config.yaml") as f:
        return yaml.safe_load(f).get("gradio", {})


_agent = None
_last_situation_analysis: str = ""

_wg_engine: Optional["WargameEngine"] = None
_wg_planner: Optional["MissionPlanner"] = None
_wg_last_plan: dict = {}
_wg_last_opfor_ai_count: int = 0
_harness_controller = None

# ── 온톨로지 실시간 적재 (워게임 상태 → Neo4j/폴백 KG) ──────────────
_wg_graph_store = None          # Neo4jGraphStore 또는 InMemoryGraphStore(폴백)
_wg_ontology_writer = None      # OntologyWriter (이벤트 + 주기 스냅샷)

# ── 자동 탐지 → 임무계획 수립 ────────────────────────────────────
# Task 29B: 탐지 큐(`detection_queue`)·동시계획 락(`_auto_plan_lock`)·재계획 상태
# (`auto_plan_status`)·쿨다운 틱(`last_replan_tick`)은 모두 `WargameSession`이 소유한다.
# 엔진 콜백 4종 → `_session.enqueue_*` → `_session.detection_queue` → 세션 탐지 워커.


# ── UI 상태 영속성 ────────────────────────────────────────────
import json as _json_mod

_UI_STATE_FILE = CONFIG_DIR / "ui_state.json"


def _save_ui_state(
    wg_history: list = None,
    main_history: list = None,
    harness_history: list = None,
    plan_box: str = None,
    timescale: float = None,
) -> None:
    """UI 상태를 파일에 저장. None인 항목은 기존 값 유지."""
    try:
        data: dict = {}
        if _UI_STATE_FILE.exists():
            data = _json_mod.loads(_UI_STATE_FILE.read_text(encoding="utf-8"))
        if wg_history is not None:
            data["wg_chat"] = wg_history
        if main_history is not None:
            data["main_chat"] = main_history
        if harness_history is not None:
            data["harness_chat"] = harness_history
        if plan_box is not None:
            data["plan_box"] = plan_box
        if timescale is not None:
            data["timescale"] = timescale
        _UI_STATE_FILE.write_text(
            _json_mod.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _load_ui_state() -> dict:
    """저장된 UI 상태 반환."""
    try:
        if _UI_STATE_FILE.exists():
            return _json_mod.loads(_UI_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_chat_history(wg_history: list, main_history: list = None) -> None:
    """하위 호환용 래퍼."""
    _save_ui_state(wg_history=wg_history, main_history=main_history)


def _load_chat_history() -> Tuple[list, list]:
    """하위 호환용 래퍼."""
    d = _load_ui_state()
    wg   = [[m[0], m[1]] for m in d.get("wg_chat", [])   if len(m) == 2]
    main = [[m[0], m[1]] for m in d.get("main_chat", []) if len(m) == 2]
    return wg, main


def _get_agent():
    global _agent
    return _agent


def _is_situation_analysis_response(response: str) -> bool:
    situation_markers = [
        "전장 상황 분석", "탐지된 전력", "상황 분석 보고서",
        "battlefield situation", "situation analysis", "detected", "탐지",
        "이동 패턴", "threat", "위협", "tank", "soldier", "truck", "전차", "병력",
    ]
    response_lower = response.lower()
    matched = sum(1 for m in situation_markers if m.lower() in response_lower)
    return matched >= 2


def _update_situation_memory_if_needed(response: str):
    global _last_situation_analysis
    if _is_situation_analysis_response(response):
        _last_situation_analysis = response
        try:
            from tools.strategy_advisor_tool import update_situation_memory
            update_situation_memory(response)
            logger.info("Situation memory updated from EXAONE4 analysis response")
        except Exception as e:
            logger.warning(f"Failed to update situation memory: {e}")


def chat(message: str, history: List[Tuple[str, str]]) -> Tuple[str, List[Tuple[str, str]]]:
    if not message.strip():
        return "", history
    agent = _get_agent()
    if agent is None:
        history.append((message, "에이전트가 초기화되지 않았습니다. main.py를 통해 실행해주세요."))
        return "", history
    history.append((message, "처리 중..."))
    try:
        response = agent.run(message)
        response_text = str(response)
        _update_situation_memory_if_needed(response_text)
        history[-1] = (message, response_text)
    except Exception as e:
        logger.error(f"Agent run error: {e}", exc_info=True)
        history[-1] = (message, f"처리 중 오류가 발생했습니다: {e}")
    _save_ui_state(main_history=history)
    return "", history


_MARKER_SYMBOL = {"infantry": "circle", "apc": "square", "armor": "diamond", "helicopter": "triangle-up", "aircraft": "triangle-up", "vehicle": "square", "truck": "square", "unknown": "circle"}
_MARKER_SIZE = {"infantry": 5, "apc": 8, "armor": 9, "helicopter": 8, "aircraft": 8, "vehicle": 6, "truck": 6, "unknown": 5}


def _build_map_figure(state: dict):
    units = state.get("units", [])
    groups = state.get("groups", [])
    mission_time = state.get("mission_time", 0)
    last_updated = state.get("last_updated", "데이터 없음")
    fig = go.Figure()
    if not units and not groups:
        fig.add_annotation(text="데이터 없음", x=0.5, y=0.5, xref="paper", yref="paper", font=dict(size=16, color="#aaaaaa"), showarrow=False)
    else:
        from collections import defaultdict
        buckets = defaultdict(list)
        for u in units:
            buckets[(u.get("side", "UNKNOWN"), u.get("cat", "unknown"))].append(u)
        for (side, cat), unit_list in buckets.items():
            base_color = "#4a90d9" if side == "BLUFOR" else "#e05252" if side == "OPFOR" else "#aaaaaa"
            symbol = _MARKER_SYMBOL.get(cat, "circle")
            size = _MARKER_SIZE.get(cat, 5)
            hover = [f"그룹: {u.get('grp','')}<br>종류: {cat}<br>HP: {u.get('hp', 0)}%<br>위치: ({u.get('x',0):.0f}, {u.get('y',0):.0f})" for u in unit_list]
            fig.add_trace(go.Scatter(x=[u.get("x", 0) for u in unit_list], y=[u.get("y", 0) for u in unit_list], mode="markers", name=f"{side} {cat} ({len(unit_list)})", marker=dict(color=base_color, size=size, symbol=symbol, line=dict(width=0.5, color="rgba(255,255,255,0.3)")), text=hover, hovertemplate="%{text}<extra></extra>", legendgroup=side))
        for g in groups:
            side = g.get("side", "UNKNOWN")
            color = "#00aaff" if side == "BLUFOR" else "#ff4444" if side == "OPFOR" else "#aaaaaa"
            gid = g.get("id", "?")
            strength = g.get("strength", 0)
            fig.add_trace(go.Scatter(x=[g.get("x", 0)], y=[g.get("y", 0)], mode="markers+text", name=gid, marker=dict(color=color, size=14, symbol="diamond", line=dict(width=1.5, color="white")), text=[f"<b>{gid}</b>"], textposition="top center", textfont=dict(color="white", size=11), hovertemplate=f"<b>{gid}</b><br>진영: {side}<br>잔존 병력: {strength}<br>위치: ({g.get('x',0):.0f}, {g.get('y',0):.0f})<extra></extra>", showlegend=False, legendgroup=side))
    mins, secs = divmod(int(mission_time), 60)
    fig.update_layout(title=dict(text=f"전장 상황도  |  미션 경과: {mins:02d}:{secs:02d}  |  최종 수신: {last_updated}", font=dict(size=14, color="#dddddd")), xaxis=dict(title="동쪽 (m)", range=[0, 30000], gridcolor="#2a3a4a", zeroline=False, tickformat=",d", tickfont=dict(color="#aaaaaa")), yaxis=dict(title="북쪽 (m)", range=[0, 30000], scaleanchor="x", scaleratio=1, gridcolor="#2a3a4a", zeroline=False, tickformat=",d", tickfont=dict(color="#aaaaaa")), paper_bgcolor="#0d1117", plot_bgcolor="#0f1923", font=dict(color="#dddddd"), legend=dict(bgcolor="rgba(0,0,0,0.5)", bordercolor="#334455", borderwidth=1, font=dict(size=11)), height=620, margin=dict(l=60, r=20, t=50, b=50), hovermode="closest")
    return fig


def get_battlefield_map():
    if not _PLOTLY_OK:
        return None, "plotly 미설치: pip install plotly"
    state = {}
    fig = _build_map_figure(state)
    summary = state.get("summary", {})
    blu = summary.get("blufor", {})
    opp = summary.get("opfor", {})
    units = state.get("units", [])
    status_lines = [f"🔵 BLUFOR  보병: {blu.get('infantry', 0):>4}  장갑/APC: {blu.get('armor', 0):>3}  헬기: {blu.get('helicopter', 0):>2}", f"🔴 OPFOR   보병: {opp.get('infantry', 0):>4}  장갑/APC: {opp.get('armor', 0):>3}  헬기: {opp.get('helicopter', 0):>2}", f"────────────────────────────────", f"전체 유닛: {len(units)}  |  미션 시간: {state.get('mission_time', 0)}s", f"최종 수신: {state.get('last_updated', '없음')}"]
    return fig, "\n".join(status_lines)


def _wg_register_engine(engine):
    for mod_name, func_name in [
        ("tools.wargame_query_tool", "register_wargame_engine"),
        ("tools.wargame_mission_tool", "register_wargame_engine"),
        ("tools.wargame_strategy_tool", "register_wargame_engine"),
        ("tools.wargame_attack_advisor_tool", "register_wargame_engine"),
        ("tools.wargame_fire_priority_tool", "register_wargame_engine"),
        ("tools.wargame_recon_tool", "register_wargame_engine"),
        ("tools.wargame_opfor_routes_tool", "register_wargame_engine"),
        ("tools.coa_analysis_tool", "register_wargame_engine"),
    ]:
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            getattr(mod, func_name)(engine)
        except Exception:
            pass


def _wg_graph_store_factory():
    """온톨로지 그래프 스토어 생성 + 조회 툴 등록 (WargameSession.graph_store_factory로 주입)."""
    from ontology.factory import build_graph_store
    from tools.ontology_query_tool import register_graph_store
    from ontology.wargame_builder import WARGAME_SCENARIO_ID
    gs = build_graph_store()
    register_graph_store(gs, WARGAME_SCENARIO_ID)
    return gs


def _wg_ontology_writer_factory(engine, graph_store):
    """OntologyWriter 생성 (WargameSession.ontology_writer_factory로 주입)."""
    from ontology.writer import OntologyWriter
    return OntologyWriter(engine, graph_store)


class _GradioReplanHooks:
    """자동 재계획 워커(c2.application.simulation.replan)가 사용하는 presentation
    툴 연동 훅. application은 tools/agent를 import할 수 없으므로 여기서 주입한다.
    각 메서드는 과거 gradio 워커의 try/except 폴백과 동일하게 안전값을 반환한다.
    """

    def reset_apply_tracker(self) -> None:
        try:
            from tools.wargame_mission_tool import reset_apply_tracker
            reset_apply_tracker()
        except Exception:
            pass

    def was_plan_applied_since(self, ts: float) -> bool:
        try:
            from tools.wargame_mission_tool import was_plan_applied_since
            return was_plan_applied_since(ts)
        except Exception:
            return False

    def get_last_applied_plan(self) -> dict:
        try:
            from tools.wargame_mission_tool import get_last_applied_plan
            return get_last_applied_plan()
        except Exception:
            return {}

    def set_resume_on_apply(self, value: bool) -> None:
        try:
            from tools.wargame_mission_tool import set_resume_on_apply
            set_resume_on_apply(value)
        except Exception:
            pass

    def get_instruction_section(self, name: str) -> str:
        try:
            from agent.battlefield_agent import get_instruction_section
            return get_instruction_section(name)
        except Exception:
            return ""

    def assess_recon_need(self) -> dict:
        from tools.wargame_recon_tool import assess_recon_need
        return assess_recon_need()

    def recommend_recon_routes(self) -> dict:
        from tools.wargame_recon_tool import recommend_recon_routes
        return recommend_recon_routes()

    def append_learned_rule(self, rule: str) -> None:
        from agent.battlefield_agent import append_learned_rule
        append_learned_rule(rule)


# Task 29A/29B: 엔진 생명주기(엔진/콜백4종/온톨로지/8개 툴등록) + 자동 재계획
# 워커·탐지 큐를 소유하는 WargameSession 싱글턴. application 계층은 tools/ui/infra를
# 직접 import하지 않으므로, 아래 훅/팩토리를 gradio_app(presentation 경계)에서 주입한다.
_session = WargameSession(
    engine_factory=lambda: WargameEngine(setup_bn_vs_bn()),
    tool_register_hook=_wg_register_engine,
    graph_store_factory=_wg_graph_store_factory,
    ontology_writer_factory=_wg_ontology_writer_factory,
    replan_hooks=_GradioReplanHooks(),
) if _WARGAME_OK else None


def _wg_ensure_ontology(engine) -> None:
    """온톨로지 그래프 스토어 + 실시간 적재기(OntologyWriter)를 준비한다.

    Task 29A: 실제 준비 로직은 `_session.ensure_ontology()`로 위임됐다. 레거시
    전역(`_wg_graph_store`/`_wg_ontology_writer`)은 파일 하위 다른 함수들이 계속
    직접 참조하므로 세션 상태와 동기화해 유지한다.
    """
    global _wg_graph_store, _wg_ontology_writer
    if _session is None:
        return
    _session.ensure_ontology(engine)
    _wg_graph_store = _session.graph_store
    _wg_ontology_writer = _session.ontology_writer


def _wg_ensure_engine() -> Optional["WargameEngine"]:
    """엔진 확보 — Task 29A: 실제 생성/콜백4종등록/툴등록/온톨로지 준비는
    `_session.ensure_engine()`(c2.application.simulation.session.WargameSession)에
    위임한다. 레거시 전역(`_wg_engine`/`_wg_planner`/`_wg_graph_store`/
    `_wg_ontology_writer`)은 이 파일의 다른 함수들이 아직 직접 참조하므로 세션
    상태와 동기화해 유지한다 (전량 이관은 Task 29B/29C에서 진행).
    """
    global _wg_engine, _wg_planner, _wg_graph_store, _wg_ontology_writer
    if not _WARGAME_OK or _session is None:
        return None
    if _wg_engine is None:
        engine = _session.ensure_engine()
        _wg_engine = engine
        # Task 29C: 별도 MissionPlanner 인스턴스를 새로 만들지 않고 세션이 소유한
        # `_session.planner`(ensure_engine() 내부에서 이미 생성됨)를 그대로 참조한다.
        _wg_planner = _session.planner
        _wg_graph_store = _session.graph_store
        _wg_ontology_writer = _session.ontology_writer
        # Task 29B: 콜백 4종은 `_session.ensure_engine()`이 세션 enqueue → 세션 큐 →
        # 세션 워커(replan)로 등록한다. gradio는 더 이상 콜백을 재정의하지 않고
        # 세션에 전적으로 위임한다. 세션 탐지 워커를 (1회) 기동한다.
        _session.start_detection_worker()
        _bl = [u.id for u in engine.units if u.side == "BLUFOR"]
        _op = [u.id for u in engine.units if u.side == "OPFOR"]
        logger.warning("[시나리오] setup_cheorwon_bn 로드 — BLUFOR %d개%s / OPFOR %d개%s",
                       len(_bl), _bl, len(_op), _op)
    return _wg_engine


def wargame_apply_custom_scenario(scenario_config: dict) -> dict:
    """사용자 정의 시나리오 적용 — 부대 구성·배치 변경 후 엔진 리셋.

    Task 29C: 실제 오케스트레이션은 `_session.apply_custom_scenario()`(dict 반환)로
    이관됐다. 여기서는 레거시 전역(`_wg_engine`/`_wg_planner`/`_wg_last_plan`)을
    세션 상태와 동기화만 한다 (파일 하위 다른 함수들이 아직 이 전역을 참조).
    """
    global _wg_engine, _wg_planner, _wg_last_plan
    if not _WARGAME_OK or _session is None:
        return {"ok": False, "error": "워게임 초기화 실패"}
    result = _session.apply_custom_scenario(scenario_config)
    if result.get("ok"):
        _wg_engine = _session.engine
        _wg_planner = _session.planner
        _wg_last_plan = {}
    return result


# ── 자동 탐지 / 전투력 임계값 / 공중지원 피격 → 공격임무계획 수립 ──
# Task 29B: 자동 재계획 워커(`_execute_auto_attack_plan`) + 탐지 워커
# (`_detection_worker`) + enqueue 4종 + 플랜적용 헬퍼(`_convert_latlon_plan_to_meters`/
# `_apply_plan_to_engine`/`_build_plan_repair_query`/`_apply_plan_with_repair`)는
# `c2.application.simulation.replan` + `WargameSession`으로 이관됐다.
#   • 콜백 4종  → `_session.enqueue_*` → `_session.detection_queue`
#   • 세션 워커 → `_session.start_detection_worker()` (엔진 확보 시 1회 기동)
#   • 상태 폴링 → `_session.auto_plan_status`


def _build_wargame_map(state: dict) -> Optional[go.Figure]:
    if not _PLOTLY_OK:
        return None
    fig = go.Figure()
    try:
        hm = get_heightmap()
        step = max(1, GRID_H // 60)
        hm_down = hm[::step, ::step]
        x_scale = MAP_W / hm_down.shape[1]
        y_scale = MAP_H / hm_down.shape[0]
        fig.add_trace(go.Heatmap(z=hm_down.tolist(), x=[i * x_scale for i in range(hm_down.shape[1])], y=[i * y_scale for i in range(hm_down.shape[0])], colorscale="Greens", showscale=False, opacity=0.35, hoverinfo="skip"))
    except Exception:
        pass
    _SIDE_COLOR = {"BLUFOR": "#4FC3F7", "OPFOR": "#EF5350"}
    _STATUS_SYM = {"active": "circle", "suppressed": "triangle-up", "destroyed": "x"}
    blufor_intel_index = {e["unit_id"]: e for e in state.get("intelligence", {}).get("BLUFOR", [])}
    for u in state.get("units", []):
        color = u.get("color", _SIDE_COLOR.get(u["side"], "gray"))
        sym = _STATUS_SYM.get(u["status"], "circle")
        size = 18 if u["status"] == "active" else 12
        cp = u["combat_power"]
        elev = u.get("elevation", 0)
        if u["side"] == "OPFOR":
            intel = blufor_intel_index.get(u["id"])
            if intel is None:
                continue
            det_status = intel["status"]
            kx, ky = intel["known_x"], intel["known_y"]
            if det_status == "detected":
                fig.add_trace(go.Scatter(x=[kx], y=[ky], mode="markers+text", name=f"OPFOR {u['id']} (탐지됨)", marker=dict(symbol=sym, size=size, color="#EF5350", line=dict(color="white", width=1.5), opacity=0.3 if u["status"] == "destroyed" else 1.0), text=[f"{u['id']}<br>{cp:.0f}%"], textposition="top center", textfont=dict(color="#EF5350", size=11), hovertemplate=f"<b>{u['id']}</b> [탐지됨]<br>위치: ({kx/1000:.1f}km, {ky/1000:.1f}km)<br>고도: {elev:.0f}m<br>전투력: {cp:.1f}%<br>상태: {u['status']}<extra></extra>"))
                wps = u.get("waypoints", [])
                if wps and u["status"] != "destroyed":
                    fig.add_trace(go.Scatter(x=[kx] + [w[0] for w in wps], y=[ky] + [w[1] for w in wps], mode="lines", line=dict(color="#EF5350", width=1.5, dash="dot"), hoverinfo="skip", showlegend=False))
            elif det_status == "approximate":
                fig.add_trace(go.Scatter(x=[kx], y=[ky], mode="markers+text", name=f"OPFOR {u['id']} (개략)", marker=dict(symbol="circle-open", size=20, color="#FFA726", line=dict(color="#FFA726", width=2)), text=[f"?{u['id']}"], textposition="top center", textfont=dict(color="#FFA726", size=11), hovertemplate=f"<b>?{u['id']}</b> [개략 위치]<br>추정 위치: ({kx/1000:.1f}km, {ky/1000:.1f}km)<extra></extra>"))
            else:
                fig.add_trace(go.Scatter(x=[kx], y=[ky], mode="markers+text", name=f"OPFOR {u['id']} (탐지 상실)", marker=dict(symbol="circle-open", size=16, color="#9E9E9E", line=dict(color="#9E9E9E", width=1.5), opacity=0.6), text=[f"({u['id']})"], textposition="top center", textfont=dict(color="#9E9E9E", size=10), hovertemplate=f"<b>{u['id']}</b> [탐지 상실]<br>최종 탐지 위치: ({kx/1000:.1f}km, {ky/1000:.1f}km)<extra></extra>"))
            continue
        wps = u.get("waypoints", [])
        if wps:
            fig.add_trace(go.Scatter(x=[u["x"]] + [w[0] for w in wps], y=[u["y"]] + [w[1] for w in wps], mode="lines", line=dict(color=color, width=1.5, dash="dot"), hoverinfo="skip", showlegend=False))
        # ── 임무 오버레이: 목표 지점 + 임무유형 라벨 (경유지 소진 후에도 유지) ──
        _mobj = u.get("mission_objective")
        _mtype = u.get("mission_type") or u.get("current_action") or ""
        if _mobj and u["status"] != "destroyed" and u["side"] == "BLUFOR":
            _MTYPE_KO = {"attack": "공격", "flank": "측방", "defend": "방어",
                         "withdraw": "철수", "hold": "고수", "recon": "정찰", "move": "기동"}
            _mt_ko = _MTYPE_KO.get(_mtype, _mtype)
            # 현위치 → 목표 임무선 (실선, 반투명)
            fig.add_trace(go.Scatter(x=[u["x"], _mobj[0]], y=[u["y"], _mobj[1]], mode="lines", line=dict(color=color, width=1.2, dash="solid"), opacity=0.45, hoverinfo="skip", showlegend=False))
            # 목표 지점 깃발 마커 + 임무 라벨
            fig.add_trace(go.Scatter(x=[_mobj[0]], y=[_mobj[1]], mode="markers+text", marker=dict(symbol="star", size=13, color=color, line=dict(color="white", width=1)), text=[f"{u['id']} {_mt_ko}"], textposition="bottom center", textfont=dict(color=color, size=9), showlegend=False, hovertemplate=f"<b>{u['id']} 임무목표</b><br>유형: {_mt_ko}<br>목표: ({_mobj[0]/1000:.1f}km, {_mobj[1]/1000:.1f}km)<extra></extra>"))
        fig.add_trace(go.Scatter(x=[u["x"]], y=[u["y"]], mode="markers+text", name=f"{u['side']} {u['id']}", marker=dict(symbol=sym, size=size, color=color, line=dict(color="white", width=1.5), opacity=0.3 if u["status"] == "destroyed" else 1.0), text=[f"{u['id']}<br>{cp:.0f}%"], textposition="top center", textfont=dict(color=color, size=11), hovertemplate=f"<b>{u['id']}</b><br>위치: ({u['x']/1000:.1f}km, {u['y']/1000:.1f}km)<br>고도: {elev:.0f}m<br>전투력: {cp:.1f}%<br>상태: {u['status']}<br>행동: {u['current_action']}<extra></extra>"))
    import math as _math
    _AIR_COLOR = {"cas": "#FF6F00", "strike": "#F50057", "artillery": "#AA00FF", "helicopter": "#00BFA5"}
    _AIR_STATUS_ALPHA = {"pending": 0.15, "active": 0.35, "completed": 0.05}
    for air in state.get("air_supports", []):
        clr = _AIR_COLOR.get(air["support_type"], "#FFFFFF")
        alpha = _AIR_STATUS_ALPHA.get(air["status"], 0.1)
        r = air["radius"]
        cx, cy = air["target_x"], air["target_y"]
        pts = 36
        circle_x = [cx + r * _math.cos(2 * _math.pi * i / pts) for i in range(pts + 1)]
        circle_y = [cy + r * _math.sin(2 * _math.pi * i / pts) for i in range(pts + 1)]
        label = f"{air['call_sign']} ({air['support_type']})"
        status_ko = {"pending": "대기", "active": "투입중", "completed": "완료"}.get(air["status"], "")
        fig.add_trace(go.Scatter(x=circle_x, y=circle_y, mode="lines", fill="toself", fillcolor=f"rgba({int(clr[1:3],16)},{int(clr[3:5],16)},{int(clr[5:7],16)},{alpha})", line=dict(color=clr, width=2, dash="dash" if air["status"] == "pending" else "solid"), name=label, hovertemplate=f"<b>{label}</b><br>상태: {status_ko}<br>목표: ({cx/1000:.1f}km, {cy/1000:.1f}km)<br>반경: {r:.0f}m<extra></extra>"))
        fig.add_trace(go.Scatter(x=[cx], y=[cy], mode="markers+text", marker=dict(symbol="x", size=12, color=clr), text=[air["call_sign"]], textposition="bottom center", textfont=dict(color=clr, size=10), showlegend=False, hoverinfo="skip"))
    # ── 자주포(포병) 간접사격 표시 — 사수→탄착 사격선 + 탄착 AoE ──────────
    for fire in state.get("indirect_fire", []):
        _own = fire.get("side") == "BLUFOR"
        f_clr = "#42A5F5" if _own else "#FF7043"   # 아군 포격 청색 / 적 포격 주황
        tx, ty = fire["target_x"], fire["target_y"]
        rr = fire.get("radius", 600.0)
        pts = 30
        cxs = [tx + rr * _math.cos(2 * _math.pi * i / pts) for i in range(pts + 1)]
        cys = [ty + rr * _math.sin(2 * _math.pi * i / pts) for i in range(pts + 1)]
        _who = "아군 포병" if _own else "적 포병"
        # 탄착 지점 AoE 원 (점선)
        fig.add_trace(go.Scatter(x=cxs, y=cys, mode="lines", fill="toself", fillcolor=f"rgba({int(f_clr[1:3],16)},{int(f_clr[3:5],16)},{int(f_clr[5:7],16)},0.12)", line=dict(color=f_clr, width=1.5, dash="dot"), name=f"{_who} 포격", legendgroup="artillery", hovertemplate=f"<b>{_who} 포격</b><br>{fire.get('shooter_id','?')} ({fire.get('unit_type','')})<br>탄착: ({tx/1000:.1f}km, {ty/1000:.1f}km)<br>반경: {rr:.0f}m<extra></extra>"))
        # 탄착 표시(폭발 마커)
        fig.add_trace(go.Scatter(x=[tx], y=[ty], mode="markers", marker=dict(symbol="star-triangle-up", size=11, color=f_clr, line=dict(color="white", width=0.5)), showlegend=False, hoverinfo="skip"))
        # 사수 위치가 보이면 사격선 (사수→탄착)
        if fire.get("shooter_visible") and fire.get("shooter_x") is not None:
            fig.add_trace(go.Scatter(x=[fire["shooter_x"], tx], y=[fire["shooter_y"], ty], mode="lines", line=dict(color=f_clr, width=1.0, dash="dashdot"), opacity=0.6, showlegend=False, hovertemplate=f"<b>{_who} 사격</b><br>{fire.get('shooter_id','?')}<extra></extra>"))
    fig.update_layout(title=dict(text=f"전장 지도 | 게임 시간: {state.get('game_time_str','00:00:00')} {'▶ 진행 중' if state.get('running') else '⏸ 정지'}", font=dict(color="#dddddd", size=14)), xaxis=dict(title="동쪽 (m)", range=[0, MAP_W], gridcolor="#2a3a4a", zeroline=False, tickformat=",d", tickfont=dict(color="#aaa")), yaxis=dict(title="북쪽 (m)", range=[0, MAP_H], scaleanchor="x", scaleratio=1, gridcolor="#2a3a4a", zeroline=False, tickformat=",d", tickfont=dict(color="#aaa")), paper_bgcolor="#0d1117", plot_bgcolor="#0f1923", font=dict(color="#dddddd"), legend=dict(bgcolor="rgba(0,0,0,0.5)", bordercolor="#334455", borderwidth=1, font=dict(size=10)), height=300, margin=dict(l=60, r=20, t=40, b=40), hovermode="closest")
    return fig


def _build_damage_chart(state: dict) -> Optional[go.Figure]:
    if not _PLOTLY_OK:
        return None
    units = state.get("units", [])
    game_time = state.get("game_time_str", "00:00:00")

    blufor_units = [u for u in units if u["side"] == "BLUFOR"]
    opfor_units  = [u for u in units if u["side"] == "OPFOR"]
    if not blufor_units and not opfor_units:
        return None

    def _cp(u):
        return u["combat_power"] if u["status"] != "destroyed" else 0.0

    bl_ids  = [u["id"] for u in blufor_units]
    bl_cp   = [_cp(u) for u in blufor_units]
    bl_dmg  = [100.0 - c for c in bl_cp]

    op_ids  = [u["id"] for u in opfor_units]
    op_cp   = [_cp(u) for u in opfor_units]
    op_dmg  = [100.0 - c for c in op_cp]

    fig = go.Figure()

    # BLUFOR 잔여 전투력 (초록)
    fig.add_trace(go.Bar(
        x=bl_ids, y=bl_cp,
        name="BLUFOR 전투력",
        marker=dict(color="#4CAF50"),
        showlegend=False,
        hovertemplate="%{x}: 잔여전력 %{y:.0f}%<extra></extra>",
    ))
    # BLUFOR 피해량 (짙은 초록)
    fig.add_trace(go.Bar(
        x=bl_ids, y=bl_dmg,
        name="BLUFOR 피해",
        marker=dict(color="#1B5E20"),
        showlegend=False,
        hovertemplate="%{x}: 피해 %{y:.0f}%<extra></extra>",
    ))
    # OPFOR 잔여 전투력 (빨강)
    fig.add_trace(go.Bar(
        x=op_ids, y=op_cp,
        name="OPFOR 전투력",
        marker=dict(color="#F44336"),
        showlegend=False,
        hovertemplate="%{x}: 잔여전력 %{y:.0f}%<extra></extra>",
    ))
    # OPFOR 피해량 (짙은 빨강)
    fig.add_trace(go.Bar(
        x=op_ids, y=op_dmg,
        name="OPFOR 피해",
        marker=dict(color="#B71C1C"),
        showlegend=False,
        hovertemplate="%{x}: 피해 %{y:.0f}%<extra></extra>",
    ))

    # 각 부대 상단 CP 레이블
    annotations = []
    for uid, cp in zip(bl_ids, bl_cp):
        annotations.append(dict(
            x=uid, y=102,
            text=f"{cp:.0f}%",
            showarrow=False,
            font=dict(color="#cccccc", size=9),
            xanchor="center", yanchor="bottom",
        ))
    for uid, cp in zip(op_ids, op_cp):
        annotations.append(dict(
            x=uid, y=102,
            text=f"{cp:.0f}%",
            showarrow=False,
            font=dict(color="#cccccc", size=9),
            xanchor="center", yanchor="bottom",
        ))

    # 평균 CP 박스 주석
    if bl_cp:
        avg_bl = sum(bl_cp) / len(bl_cp)
        annotations.append(dict(
            x=0.18, y=1.12, xref="paper", yref="paper",
            text=f"BLUFOR 평균 CP: {avg_bl:.0f}%",
            showarrow=False,
            font=dict(color="#4FC3F7", size=10),
            bgcolor="rgba(13,17,23,0.7)",
            bordercolor="#4FC3F7", borderwidth=1,
            xanchor="center",
        ))
    if op_cp:
        avg_op = sum(op_cp) / len(op_cp)
        annotations.append(dict(
            x=0.82, y=1.12, xref="paper", yref="paper",
            text=f"OPFOR 평균 CP: {avg_op:.0f}%",
            showarrow=False,
            font=dict(color="#EF5350", size=10),
            bgcolor="rgba(13,17,23,0.7)",
            bordercolor="#EF5350", borderwidth=1,
            xanchor="center",
        ))

    shapes = []

    fig.update_layout(
        barmode="stack",
        title=dict(
            text=f"피해 현황 | {game_time}",
            font=dict(color="#dddddd", size=13),
        ),
        xaxis=dict(
            title="",
            tickfont=dict(color="#aaaaaa", size=9),
            gridcolor="#2a3a4a",
            zeroline=False,
        ),
        yaxis=dict(
            title=dict(text="전투력 (%)", font=dict(color="#aaaaaa", size=10)),
            range=[0, 100],
            tickfont=dict(color="#aaaaaa", size=9),
            gridcolor="#2a3a4a",
            zeroline=False,
        ),
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0f1923",
        font=dict(color="#dddddd"),
        showlegend=False,
        height=220,
        margin=dict(l=50, r=20, t=45, b=40),
        annotations=annotations,
        shapes=shapes,
    )
    return fig


def _wg_status_text(state: dict) -> str:
    try:
        from wargame.scenario import get_unit_type
    except Exception:
        get_unit_type = lambda uid: ""
    lines = [f"게임 시간: {state.get('game_time_str','00:00:00')} | Tick: {state.get('tick',0)}"]
    winner = state.get("winner")
    if winner:
        lines.append(f"★ 전투 종료: {winner} 승리")
    lines.append("")
    lines.append("🔵 BLUFOR")
    for u in state.get("units", []):
        if u["side"] != "BLUFOR":
            continue
        bar = "█" * int(u["combat_power"] / 10) + "░" * (10 - int(u["combat_power"] / 10))
        utype = get_unit_type(u["id"])
        lines.append(f"  {u['id']:7s}({utype:6s}) [{bar}] {u['combat_power']:5.1f}%  {u['status']}")
    air_use = state.get("air_use_count", {})
    air_limit = state.get("air_use_limit", 5)
    air_reset = state.get("air_reset_at", 0)
    cur_tick = state.get("tick", 0)
    ticks_left = max(0, air_reset - cur_tick)
    blu_used = air_use.get("BLUFOR", 0)
    opp_used = air_use.get("OPFOR", 0)
    lines.append(
        f"✈ 공중지원: BLUFOR {air_limit - blu_used}/{air_limit} 잔여"
        f" | OPFOR {air_limit - opp_used}/{air_limit} 잔여"
        f" | 리셋까지 {ticks_left}틱"
    )
    _STATUS_KO = {"detected": "탐지됨", "approximate": "개략위치", "lost": "탐지상실"}
    lines.append("🔴 OPFOR (BLUFOR 인텔 기준)")
    for e in state.get("intelligence", {}).get("BLUFOR", []):
        det = e["status"]
        det_ko = _STATUS_KO.get(det, det)
        if det == "detected":
            utype = e.get("unit_type") or "?"
            cp = e.get("combat_power") or 0.0
            bar = "█" * int(cp / 10) + "░" * (10 - int(cp / 10))
            lines.append(f"  {e['unit_id']:7s}({utype:6s}) [{bar}] {cp:5.1f}%  [{det_ko}]")
        elif det == "approximate":
            approx_lat, approx_lon = _xy_to_latlon(e["known_x"], e["known_y"])
            lines.append(f"  ?{e['unit_id']:6s}(미확인) {'?'*10}  ?????  [{det_ko}] (lat={approx_lat:.4f},lon={approx_lon:.4f} 추정)")
        else:
            utype = e.get("unit_type") or "미확인"
            lost_lat, lost_lon = _xy_to_latlon(e["known_x"], e["known_y"])
            lines.append(f"  ({e['unit_id']:6s})({utype:6s}) {'░'*10}  ?????  [{det_ko}] 최종(lat={lost_lat:.4f},lon={lost_lon:.4f})")
    return "\n".join(lines)


def _build_opfor_alert(state: dict) -> str:
    try:
        from wargame.scenario import get_unit_type
    except Exception:
        def get_unit_type(uid): return "부대"
    opfor = [u for u in state["units"] if u["side"] == "OPFOR" and u["status"] != "destroyed"]
    blufor = [u for u in state["units"] if u["side"] == "BLUFOR" and u["status"] != "destroyed"]
    if not opfor:
        return "✅ 모든 적군 전투불능 — 전투 종료"
    lines = [f"⚠️ **OPFOR 기동 감지** (게임시간: {state['game_time_str']})"]
    lines.append("\n**적군 현황:**")
    for u in opfor:
        action_ko = {"attack":"공격","flank":"측방기동","withdraw":"후퇴","hold":"대기","defend":"방어","move":"이동"}.get(u["current_action"], u["current_action"])
        u_lat, u_lon = _xy_to_latlon(u["x"], u["y"])
        lines.append(f"  • {u['id']}({get_unit_type(u['id'])}): (lat={u_lat:.4f},lon={u_lon:.4f}) CP={u['combat_power']:.0f}% [{action_ko}]")
    if blufor and opfor:
        bl_cx = sum(u["x"] for u in blufor) / len(blufor)
        bl_cy = sum(u["y"] for u in blufor) / len(blufor)
        op_cx = sum(u["x"] for u in opfor) / len(opfor)
        op_cy = sum(u["y"] for u in opfor) / len(opfor)
        dist = ((bl_cx - op_cx)**2 + (bl_cy - op_cy)**2)**0.5
        lines.append(f"\n**위협 거리:** {dist/1000:.1f}km")
    lines.append("\n*LLM 상황 분석 중...*")
    return "\n".join(lines)


def _build_situation_query(state: dict) -> str:
    try:
        from wargame.scenario import get_unit_type
    except Exception:
        def get_unit_type(uid): return "부대"
    lines = [f"[워게임 상황 분석] 게임시간: {state['game_time_str']}"]
    for u in state["units"]:
        s = "전투불능" if u["status"] == "destroyed" else f"CP={u['combat_power']:.0f}%"
        u_lat, u_lon = _xy_to_latlon(u["x"], u["y"])
        lines.append(f"  {u['side']} {u['id']}({get_unit_type(u['id'])}): (lat={u_lat:.4f},lon={u_lon:.4f}) {s} 행동={u['current_action']}")
    lines.append("\n현재 전장 상황을 분석하고, OPFOR의 의도와 BLUFOR 즉각 대응 방안을 3가지 간결하게 제시해줘.")
    return "\n".join(lines)


def wargame_refresh():
    eng = _wg_ensure_engine()
    if eng is None:
        msg = f"워게임 모듈 로드 실패: {_wg_err if not _WARGAME_OK else '엔진 없음'}"
        return None, None, msg, ""
    state = eng.get_state()
    fig = _build_wargame_map(state)
    damage_fig = _build_damage_chart(state)
    status = _wg_status_text(state)
    events = eng.db.get_recent_events(20)
    log_text = "\n".join(f"[{e['event_type']:10s}] T={e['tick']:4d} {e['message']}" for e in events)
    return fig, damage_fig, status, log_text


def wargame_on_load():
    """페이지 새로고침 시 엔진 상태를 읽어 전체 UI를 복원."""
    eng = _wg_ensure_engine()
    state = _load_ui_state()
    wg_history   = [[m[0], m[1]] for m in state.get("wg_chat", [])   if len(m) == 2]
    plan_box     = state.get("plan_box", "")
    saved_scale  = state.get("timescale", 60.0)

    if eng is None:
        return "▶ 시뮬레이션 시작", None, None, "워게임 초기화 실패", "", wg_history, plan_box, saved_scale
    # 저장된 time_scale을 엔진에도 반영
    eng.time_scale = float(saved_scale)
    btn_label = "⏸ 일시정지" if eng.running else "▶ 시뮬레이션 시작"
    fig, damage_fig, status, log_text = wargame_refresh()
    return btn_label, fig, damage_fig, status, log_text, wg_history, plan_box, saved_scale


def wargame_start_pause():
    """Task 29C: 실행/정지 토글은 `_session.start_pause()`(dict 반환)에 위임."""
    if _session is None:
        return "워게임 초기화 실패", *wargame_refresh()
    result = _session.start_pause()
    fig, damage_fig, status, log_text = wargame_refresh()
    return result["label"], fig, damage_fig, status, log_text


def wargame_reset_sim():
    """Task 29C: 리셋 오케스트레이션은 `_session.reset()`에 위임.

    레거시 전역(`_wg_engine`/`_wg_planner`/`_wg_graph_store`/`_wg_ontology_writer`/
    `_wg_last_plan`)은 세션 상태와 동기화만 한다 (파일 하위 다른 함수들이 아직
    이 전역을 참조).
    """
    global _wg_engine, _wg_planner, _wg_graph_store, _wg_ontology_writer, _wg_last_plan
    if not _WARGAME_OK or _session is None:
        return "초기화 실패", None, None, "", ""
    _session.reset()
    _wg_engine = _session.engine
    _wg_planner = _session.planner
    _wg_graph_store = _session.graph_store
    _wg_ontology_writer = _session.ontology_writer
    _wg_last_plan = {}
    _bl = [u.id for u in _wg_engine.units if u.side == "BLUFOR"]
    _op = [u.id for u in _wg_engine.units if u.side == "OPFOR"]
    logger.warning("[시나리오] 리셋 — setup_cheorwon_bn: BLUFOR %d개%s / OPFOR %d개%s",
                   len(_bl), _bl, len(_op), _op)
    fig, damage_fig, status, log_text = wargame_refresh()
    return "▶ 시뮬레이션 시작", fig, damage_fig, status, log_text


def wargame_set_timescale(scale: float):
    """Task 29C: 배속 설정은 `_session.set_timescale()`에 위임."""
    if _session is not None:
        _session.set_timescale(scale)
        _save_ui_state(timescale=float(scale))
    return wargame_refresh()


def wargame_request_recon_plan(history: List = None):
    """정찰 임무계획 수립 — Gradio 얇은 래퍼.

    Task 29C: 오케스트레이션(에이전트 호출/플랜 파싱/엔진 적용)은 전량
    `_session.request_recon_plan()`(dict 반환)으로 이관됐다. 여기서는 그 결과
    dict로부터 Gradio 튜플(figure 포함)만 조립한다.
    """
    global _wg_last_plan
    if _session is None:
        history = list(history or [])
        history.append(("🔍 정찰 임무계획 요청", "워게임 초기화 실패"))
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, "", fig, damage_fig, status, log_text
    result = _session.request_recon_plan(history)
    history = result["history"]
    plan_text = result.get("plan_text", "")
    _wg_last_plan = result.get("plan") or {}
    if plan_text:
        _save_ui_state(wg_history=history, plan_box=plan_text)
    fig, damage_fig, status, log_text = wargame_refresh()
    return history, plan_text, fig, damage_fig, status, log_text


def wargame_request_attack_plan(history: List = None):
    """공격 임무계획 수립 — Gradio 얇은 래퍼.

    Task 29C: 오케스트레이션(에이전트 호출/플랜-with-repair/규칙 기반 폴백/엔진 적용)은
    전량 `_session.request_attack_plan()`(dict 반환)으로 이관됐다. 여기서는 그 결과
    dict로부터 Gradio 튜플(figure 포함)만 조립한다.
    """
    global _wg_last_plan
    if _session is None:
        history = list(history or [])
        history.append(("⚔️ 공격 임무계획 요청", "워게임 초기화 실패"))
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, "", fig, damage_fig, status, log_text
    result = _session.request_attack_plan(history)
    history = result["history"]
    plan_text = result.get("plan_text", "")
    _wg_last_plan = result.get("plan") or {}
    if plan_text:
        _save_ui_state(wg_history=history, plan_box=plan_text)
    fig, damage_fig, status, log_text = wargame_refresh()
    return history, plan_text, fig, damage_fig, status, log_text


# Task 29C: `_apply_chat_plan_if_any`는 `c2.application.simulation.replan`의
# 동명 함수(`apply_chat_plan_if_any`)로 이관됐다. `_session.chat_send()`가 내부적으로
# 사용한다.


def wg_chat_send(message: str, history: List) -> Tuple[List, str]:
    """전술채팅 — Gradio 얇은 래퍼. 오케스트레이션은 `_session.chat_send()`에 위임."""
    if _session is None:
        return history, ""
    result = _session.chat_send(message, history)
    _save_chat_history(result["history"])
    return result["history"], ""


def wargame_evaluate_and_learn(history: List) -> Tuple[List, str]:
    """전술 평가 & 규칙 학습 — Gradio 얇은 래퍼.

    오케스트레이션(이벤트 요약/평가 쿼리/2차 일반화/규칙 저장)은
    `_session.evaluate_and_learn()`에 위임됐다.
    """
    if _session is None:
        history = list(history or [])
        history.append(("🧠 전술 평가", "워게임 엔진 없음"))
        return history, ""
    result = _session.evaluate_and_learn(history)
    _save_chat_history(result["history"])
    return result["history"], ""


def wargame_refresh_with_alert(chatbot_history: List) -> tuple:
    global _wg_last_opfor_ai_count
    fig, damage_fig, status, log_text = wargame_refresh()
    chatbot_history = list(chatbot_history or [])
    eng = _wg_ensure_engine()
    if eng is not None:
        state = eng.get_state()
        current_count = state.get("opfor_ai_fire_count", 0)
        if current_count > _wg_last_opfor_ai_count:
            _wg_last_opfor_ai_count = current_count
            alert_msg = _build_opfor_alert(state)
            chatbot_history.append(("⚠️ 시스템 알람", alert_msg))
            _save_chat_history(chatbot_history)
    # 자동 재계획 진행 중 상태 알림 (세션이 소유)
    _ap_status = _session.auto_plan_status if _session is not None else {}
    if _ap_status.get("active"):
        import time as _ap_t
        elapsed = _ap_t.time() - _ap_status.get("started_at", 0)
        msg = _ap_status.get("message", "")
        notice = f"[자동 재계획 진행 중 — {elapsed:.0f}s 경과] {msg}"
        # 이미 동일 알림이 마지막 메시지로 있으면 갱신, 없으면 추가
        if chatbot_history and chatbot_history[-1][0] == "🔄 자동 재계획":
            chatbot_history[-1] = ("🔄 자동 재계획", notice)
        else:
            chatbot_history.append(("🔄 자동 재계획", notice))
    return fig, damage_fig, status, log_text, chatbot_history


def clear_chat_history() -> Tuple[List, str]:
    global _last_situation_analysis
    _last_situation_analysis = ""
    try:
        from tools.strategy_advisor_tool import clear_situation_memory
        clear_situation_memory()
    except Exception:
        pass
    _save_chat_history([], [])
    return [], "대화 기록과 상황 분석 메모리가 초기화되었습니다."


def get_situation_memory_status() -> str:
    try:
        from tools.strategy_advisor_tool import get_situation_memory
        memory = get_situation_memory()
        if memory.get("situation_analysis"):
            ts = memory.get("analysis_timestamp", "")
            preview = memory["situation_analysis"][:200] + "..."
            return f"상황 분석 메모리 활성 (분석 시각: {ts})\n\n미리보기:\n{preview}"
        return "상황 분석 메모리가 비어 있습니다. 채팅으로 전장 상황 분석을 먼저 요청하세요."
    except Exception as e:
        return f"메모리 상태 조회 오류: {e}"


def _init_harness_controller():
    """하네스 컨트롤러를 초기화합니다."""
    global _harness_controller
    if not _WARGAME_OK:
        return None
    try:
        from wargame.harness import HarnessController
        from wargame import WargameEngine, setup_cheorwon_bn as setup_bn_vs_bn
        from wargame.llm_planner import MissionPlanner

        def _engine_factory():
            units = setup_bn_vs_bn()
            eng = WargameEngine(units)
            eng.full_recon = True  # 철원 시나리오: UAV 완전정찰
            _wg_register_engine(eng)
            return eng

        agent = _get_agent()
        planner = _wg_planner
        _harness_controller = HarnessController(
            engine_factory=_engine_factory,
            agent=agent,
            planner=planner,
        )
        logger.info("HarnessController initialized")
        return _harness_controller
    except Exception as e:
        logger.warning(f"Failed to init HarnessController: {e}")
        return None


def harness_start_training(n_episodes: int, replan_interval: int, history: list):
    """하네스 학습을 시작합니다."""
    global _harness_controller
    history = list(history or [])

    ctrl = _harness_controller or _init_harness_controller()
    if ctrl is None:
        history.append(("🔬 하네스 학습", "HarnessController 초기화 실패"))
        return history, "초기화 실패", ""

    if ctrl._running:
        return history, "이미 실행 중", ""

    history.append(("🔬 하네스 학습 시작", f"{n_episodes}개 에피소드 학습 시작..."))

    def _progress_cb(current, total, metrics):
        pass  # 폴링 방식으로 UI 업데이트

    ctrl.start_training(
        n_episodes=int(n_episodes),
        replan_interval_ticks=int(replan_interval),
        on_progress=_progress_cb,
    )
    _save_ui_state(harness_history=history)
    return history, f"학습 시작: {n_episodes}개 에피소드", ""


def harness_get_status():
    """하네스 학습 진행 상황을 반환합니다."""
    global _harness_controller
    ctrl = _harness_controller
    if ctrl is None:
        return "하네스 미초기화", "", ""

    progress = ctrl.get_progress()
    stats = ctrl.get_db_stats()

    current = progress.get("current", 0)
    total = progress.get("total", 0)
    status = progress.get("status", "idle")
    last = progress.get("last_metrics") or {}

    status_text = {
        "idle": "대기 중",
        "running": f"실행 중 ({current}/{total})",
        "done": f"완료 ({total}개 에피소드)",
        "stopped": f"중지됨 ({current}/{total})",
    }.get(status, status)

    if last:
        last_result = (
            f"**최근 에피소드:** {last.get('winner','?')} 승리 | "
            f"생존율 {last.get('blufor_survival_rate',0):.0%} | "
            f"교환비 {last.get('combat_efficiency',0):.1f}"
        )
    else:
        last_result = "에피소드 없음"

    stats_text = (
        f"총 에피소드: {stats.get('total_episodes',0)} | "
        f"승률: {stats.get('win_rate',0):.0%} | "
        f"활성 규칙: {stats.get('active_rules',0)}개"
    )

    return status_text, last_result, stats_text


def harness_stop_training(history: list):
    global _harness_controller
    history = list(history or [])
    ctrl = _harness_controller
    if ctrl and ctrl._running:
        ctrl.stop_training()
        history.append(("🔬 하네스", "학습 중지 요청"))
    _save_ui_state(harness_history=history)
    return history


def harness_get_rules():
    """현재 활성 규칙을 마크다운으로 반환합니다."""
    global _harness_controller
    ctrl = _harness_controller
    if ctrl is None:
        try:
            from agent.battlefield_agent import get_instruction_section
            learned = get_instruction_section("LEARNED_RULES")
            recon = get_instruction_section("RECON")
            attack = get_instruction_section("ATTACK")
        except Exception:
            return "하네스 미초기화"
    else:
        rules = ctrl.get_active_rules()
        learned_list = rules.get("LEARNED_RULES", [])
        recon_list = rules.get("RECON", [])
        attack_list = rules.get("ATTACK", [])
        learned = "\n".join(f"- {r['text']} *(신뢰도 {r['confidence']:.2f}, {r['win_count']}승/{r['loss_count']}패)*" for r in learned_list)
        recon = "\n".join(f"- {r['text']}" for r in recon_list)
        attack = "\n".join(f"- {r['text']}" for r in attack_list)

    parts = []
    if recon:
        parts.append(f"**[RECON]**\n{recon}")
    if attack:
        parts.append(f"**[ATTACK]**\n{attack}")
    if learned:
        parts.append(f"**[LEARNED_RULES]**\n{learned}")

    # 전술 메모리 패널티 존 표시
    try:
        from wargame.harness.tactical_memory import get_tactical_memory
        tm = get_tactical_memory()
        zones = tm.get_penalty_zones()
        if zones:
            zone_lines = [
                f"- **({z['x']/1000:.1f}km, {z['y']/1000:.1f}km)** r={z['radius']/1000:.1f}km "
                f"패널티={z['penalty']:.2f} 피격={z.get('hit_count',1)}회: {z['reason'][:60]}"
                for z in zones[:10]
            ]
            parts.append(f"**[⚠️ 패널티 존 ({len(zones)}개)]**\n" + "\n".join(zone_lines))
    except Exception:
        pass

    return "\n\n".join(parts) if parts else "학습된 규칙 없음"


_MSIS_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700&display=swap');

:root {
    --bg: #0a0e14;
    --panel: #111820;
    --panel-border: #1e2d3d;
    --green: #00ff88;
    --amber: #ffb300;
    --red: #ff4040;
    --blue: #40aaff;
    --text: #e4eef8;
    --dim: #9db8c8;
}

body, .gradio-container {
    background: var(--bg) !important;
    font-family: 'Noto Sans KR', sans-serif !important;
    color: var(--text) !important;
    max-width: 100% !important;
}

/* Header */
.c2-header {
    background: linear-gradient(135deg, #060c18 0%, #0d1e35 60%, #060c18 100%);
    border-bottom: 1px solid #1e3a5f;
    padding: 12px 20px 8px;
    margin-bottom: 0;
}
.c2-header h1 { color: var(--blue) !important; font-size: 1.3em !important; letter-spacing: 1.5px; margin: 0 !important; }
.c2-header p  { color: var(--dim) !important; font-size: 12px !important; margin: 2px 0 0 !important; }

/* Tabs */
.tabs > .tab-nav { background: #0c1420 !important; border-bottom: 1px solid var(--panel-border) !important; }
.tab-nav button { color: var(--dim) !important; font-family: 'Noto Sans KR', sans-serif !important; font-size: 13px !important; padding: 8px 16px !important; }
.tab-nav button.selected { color: var(--green) !important; border-bottom: 2px solid var(--green) !important; background: transparent !important; }

/* Panel boxes */
.gr-box, .gr-panel, .block { background: var(--panel) !important; border: 1px solid var(--panel-border) !important; border-radius: 3px !important; }

/* Textareas & inputs */
textarea, input[type="text"], input[type="number"] {
    background: #080d14 !important;
    color: var(--text) !important;
    border: 1px solid var(--panel-border) !important;
    border-radius: 3px !important;
    font-family: 'Noto Sans KR', sans-serif !important;
    font-size: 13px !important;
}
textarea:focus, input:focus { border-color: var(--blue) !important; outline: none !important; }

/* Status textbox: monospace */
#wg_status textarea {
    font-family: 'Courier New', monospace !important;
    font-size: 11px !important;
    color: #a8c8e0 !important;
    background: #06090f !important;
    line-height: 1.55 !important;
}

/* Event log */
#wg_event_log textarea {
    font-family: 'Courier New', monospace !important;
    font-size: 11px !important;
    color: #8aacbe !important;
    background: #06090f !important;
    line-height: 1.5 !important;
}

/* Plan box */
#wg_plan_box textarea {
    font-family: 'Courier New', monospace !important;
    font-size: 11px !important;
    color: #b0d4e8 !important;
    background: #06090f !important;
}

/* Primary buttons — green */
button.primary, .primary > button {
    background: #0a2e1e !important;
    border: 1px solid var(--green) !important;
    color: var(--green) !important;
    font-family: 'Noto Sans KR', sans-serif !important;
    font-size: 13px !important;
    letter-spacing: 0.3px !important;
    border-radius: 3px !important;
    transition: background 0.15s !important;
}
button.primary:hover, .primary > button:hover { background: #143d28 !important; }

/* Secondary buttons — blue */
button.secondary, .secondary > button {
    background: #0d1e30 !important;
    border: 1px solid var(--blue) !important;
    color: var(--blue) !important;
    font-family: 'Noto Sans KR', sans-serif !important;
    font-size: 13px !important;
    border-radius: 3px !important;
    transition: background 0.15s !important;
}
button.secondary:hover, .secondary > button:hover { background: #162840 !important; }

/* Labels */
label span, .label-wrap span { color: var(--dim) !important; font-size: 11px !important; letter-spacing: 0.4px !important; text-transform: uppercase !important; }

/* Markdown */
.prose h1, .prose h2, .prose h3, .prose h4 { color: var(--text) !important; }
.prose p, .prose li { color: var(--dim) !important; font-size: 13px !important; }
.prose strong { color: var(--text) !important; }
.prose code { background: #0d1520 !important; color: var(--amber) !important; border: 1px solid var(--panel-border) !important; }

/* Chatbot */
.chatbot { background: var(--panel) !important; }
.chatbot .message.user { background: #0d1e35 !important; border-left: 3px solid var(--blue) !important; color: var(--text) !important; }
.chatbot .message.bot  { background: #0a1620 !important; border-left: 3px solid var(--green) !important; color: var(--text) !important; }

/* Sidebar section headers */
.sidebar-section {
    border-top: 1px solid var(--panel-border);
    padding-top: 10px;
    margin-top: 10px;
}
.sidebar-section-title {
    color: var(--amber) !important;
    font-size: 11px !important;
    font-weight: 700 !important;
    letter-spacing: 1.5px !important;
    text-transform: uppercase !important;
    margin-bottom: 6px !important;
}

/* Pulse animation for running state */
@keyframes pulse-glow {
    0%   { box-shadow: 0 0 4px var(--green); }
    50%  { box-shadow: 0 0 10px var(--green); }
    100% { box-shadow: 0 0 4px var(--green); }
}

/* Thin scrollbars */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--panel-border); border-radius: 2px; }
::-webkit-scrollbar-thumb:hover { background: var(--dim); }
* { scrollbar-width: thin; scrollbar-color: var(--panel-border) var(--bg); }

/* Slider */
input[type="range"] { accent-color: var(--blue) !important; }

/* Dropdown */
select, .gr-dropdown { background: #080d14 !important; color: var(--text) !important; border: 1px solid var(--panel-border) !important; }

/* Checkbox group */
.gr-checkbox-group { background: transparent !important; }
input[type="checkbox"] { accent-color: var(--green) !important; }
"""


def create_app(agent=None) -> gr.Blocks:
    global _agent
    _agent = agent
    ui_cfg = _load_ui_config()
    with gr.Blocks(
        title=ui_cfg.get("title", "C2 지휘통제 AI"),
        theme=gr.themes.Base(primary_hue="slate", secondary_hue="slate", neutral_hue="slate"),
        css=_MSIS_CSS,
    ) as app:
        # ── Header ─────────────────────────────────────────────────────
        gr.HTML(f"""
        <div class="c2-header">
            <h1>⚔ C2 지휘통제 AI — {ui_cfg.get('title', 'EXAONE4')}</h1>
            <p>{ui_cfg.get('description', 'EXAONE4: 상황 분석 · 전략/전술 추천 · 임무계획 수립')}</p>
        </div>
        """)
        with gr.Tabs():
          with gr.Tab("🎖️ AI 에이전트"):
            with gr.Row():
              with gr.Column(scale=1):
                gr.Markdown("### 상황 분석 메모리")
                memory_status_btn = gr.Button("메모리 상태 확인")
                memory_status_box = gr.Textbox(label="EXAONE4 상황 분석 메모리", lines=5, interactive=False)
              with gr.Column(scale=2):
                gr.Markdown("### AI 에이전트 채팅")
                gr.Markdown("전략/전술 관련 질문을 입력하세요.")
                chatbot = gr.Chatbot(label="대화", height=500, show_copy_button=True)
                with gr.Row():
                    query_input = gr.Textbox(label="쿼리 입력", placeholder="예: '현재 상황에서 방어 전술을 추천해줘'", lines=2, scale=5)
                    send_btn = gr.Button("전송", variant="primary", scale=1)
                with gr.Row():
                    clear_btn = gr.Button("대화 초기화", variant="secondary")
                    clear_status = gr.Textbox(label="", value="", interactive=False, scale=3)
                gr.Markdown("### 예시 쿼리")
                example_queries = ui_cfg.get("examples", ["현재 전장 상황에 대한 전략적 대응 방안을 추천해줘", "적 기갑부대에 대한 전술적 대응 방안을 제안해줘", "아군 방어 진지 구축을 위한 전략을 수립해줘"])
                gr.Examples(examples=[[q] for q in example_queries], inputs=[query_input], label="클릭하여 예시 쿼리 입력")
          with gr.Tab("⚔️ 워게임 시뮬레이터"):
            if not _WARGAME_OK:
                gr.Markdown(f"⚠️ 워게임 모듈 로드 실패: `{_wg_err}`")
            else:
                # ── 3-column MSIS layout ──────────────────────────────
                with gr.Row(equal_height=False):
                    # ── LEFT: Control Sidebar ─────────────────────────
                    with gr.Column(scale=1, min_width=220):
                        gr.HTML('<div class="sidebar-section-title" style="margin-top:6px">▶ 시뮬레이션 제어</div>')
                        wg_startstop_btn = gr.Button("▶ 시뮬레이션 시작", variant="primary")
                        wg_reset_btn     = gr.Button("⏹ 초기화", variant="secondary")
                        wg_timescale     = gr.Slider(minimum=0.1, maximum=600, value=60, step=0.1,
                                                     label="시간 배율 (1초 = X 게임초)")
                        wg_apply_scale_btn = gr.Button("배율 적용", size="sm", variant="secondary")
                        gr.HTML('<div class="sidebar-section-title sidebar-section">📋 임무계획</div>')
                        wg_recon_btn  = gr.Button("🔍 정찰 임무계획", variant="secondary")
                        wg_attack_btn = gr.Button("⚔️ 공격 임무계획", variant="primary")
                        wg_eval_btn   = gr.Button("🧠 전술 평가 & 학습", variant="secondary", size="sm")
                        gr.HTML('<div class="sidebar-section-title sidebar-section">📊 부대 전력 현황</div>')
                        wg_status = gr.Textbox(label="", lines=14, interactive=False, elem_id="wg_status")
                    # ── CENTER: Battle Map ────────────────────────────
                    with gr.Column(scale=3):
                        wg_map = gr.Plot(label="전장 지도", show_label=False)
                        wg_damage_chart = gr.Plot(label="피해 현황", show_label=False, elem_id="wg_damage_chart")
                    # ── RIGHT: Intelligence / Chat Panel ─────────────
                    with gr.Column(scale=2):
                        gr.HTML('<div class="sidebar-section-title" style="margin-top:6px">💬 전술 AI 채팅</div>')
                        wg_chatbot = gr.Chatbot(label="", height=280, show_copy_button=True, bubble_full_width=False)
                        with gr.Row():
                            wg_chat_input    = gr.Textbox(label="", placeholder="전장 상황 분석, 전술 조언, 임무계획 문의...", lines=2, scale=5)
                            wg_chat_send_btn = gr.Button("전송", variant="primary", scale=1)
                        wg_chat_clear_btn = gr.Button("대화 초기화", variant="secondary", size="sm")
                        gr.HTML('<div class="sidebar-section-title sidebar-section">📄 LLM 임무계획 (JSON)</div>')
                        wg_plan_box = gr.Textbox(label="", lines=8, interactive=False, value="", elem_id="wg_plan_box")
                        gr.HTML('<div class="sidebar-section-title sidebar-section">📟 전투 이벤트 로그</div>')
                        wg_event_log = gr.Textbox(label="", lines=6, interactive=False, elem_id="wg_event_log")
                wg_timer = gr.Timer(value=2)
          with gr.Tab("🔬 하네스 학습"):
            gr.Markdown("## 자율 전술 학습\n워게임을 반복 실행하여 전술 규칙을 자동으로 학습합니다.")
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### 학습 설정")
                    harness_n_episodes = gr.Slider(minimum=1, maximum=100, value=10, step=1, label="에피소드 수")
                    harness_replan_interval = gr.Slider(minimum=30, maximum=300, value=120, step=10, label="재계획 간격 (틱)")
                    with gr.Row():
                        harness_start_btn = gr.Button("▶ 학습 시작", variant="primary")
                        harness_stop_btn = gr.Button("⏸ 중지", variant="secondary")
                    gr.Markdown("### 현황")
                    harness_status_text = gr.Textbox(label="상태", interactive=False, lines=1)
                    harness_last_episode = gr.Textbox(label="최근 에피소드", interactive=False, lines=2)
                    harness_stats_text = gr.Textbox(label="누적 통계", interactive=False, lines=1)
                    harness_refresh_btn = gr.Button("🔄 새로고침", size="sm")
                with gr.Column(scale=2):
                    gr.Markdown("### 학습 로그")
                    harness_chatbot = gr.Chatbot(label="", height=300, show_copy_button=True)
                    gr.Markdown("### 현재 활성 규칙")
                    harness_rules_md = gr.Markdown("규칙 로드 중...")
                    harness_rules_refresh_btn = gr.Button("규칙 새로고침", size="sm")
            harness_timer = gr.Timer(value=3)
          with gr.Tab("🗺️ 전장 지도"):
            gr.Markdown("실시간 전장 데이터를 지도에 표시합니다. 10초마다 자동 갱신됩니다.")
            with gr.Row():
                with gr.Column(scale=3):
                    map_plot = gr.Plot(label="전장 상황도", show_label=False)
                with gr.Column(scale=1):
                    gr.Markdown("### 병력 현황")
                    map_status = gr.Textbox(label="", lines=6, interactive=False, elem_id="map_status")
                    map_refresh_btn = gr.Button("🔄 새로고침", variant="primary")
                    gr.Markdown("**마커 범례**\n- 🔵 원 = BLUFOR 보병\n- 🔵 사각 = BLUFOR APC/차량\n- 🔵 다이아 = BLUFOR 장갑\n- 🔴 원 = OPFOR 보병\n- 🔴 사각 = OPFOR APC/차량\n- 🔴 다이아 = OPFOR 장갑\n- 큰 다이아 = 그룹 지휘관 위치\n\n**좌표계**\nx = 동쪽(m), y = 북쪽(m)\nAltis 맵 기준 (0 ~ 30,000m)")
            map_timer = gr.Timer(value=10)
        send_btn.click(fn=chat, inputs=[query_input, chatbot], outputs=[query_input, chatbot])
        query_input.submit(fn=chat, inputs=[query_input, chatbot], outputs=[query_input, chatbot])
        clear_btn.click(fn=clear_chat_history, outputs=[chatbot, clear_status])
        memory_status_btn.click(fn=get_situation_memory_status, outputs=[memory_status_box])
        if _WARGAME_OK:
            _WG_OUTPUTS = [wg_map, wg_damage_chart, wg_status, wg_event_log]
            wg_startstop_btn.click(fn=wargame_start_pause, outputs=[wg_startstop_btn, wg_map, wg_damage_chart, wg_status, wg_event_log])
            wg_reset_btn.click(fn=wargame_reset_sim, outputs=[wg_startstop_btn, wg_map, wg_damage_chart, wg_status, wg_event_log])
            wg_apply_scale_btn.click(fn=wargame_set_timescale, inputs=[wg_timescale], outputs=_WG_OUTPUTS)
            wg_recon_btn.click(fn=wargame_request_recon_plan, inputs=[wg_chatbot], outputs=[wg_chatbot, wg_plan_box, wg_map, wg_damage_chart, wg_status, wg_event_log])
            wg_attack_btn.click(fn=wargame_request_attack_plan, inputs=[wg_chatbot], outputs=[wg_chatbot, wg_plan_box, wg_map, wg_damage_chart, wg_status, wg_event_log])
            wg_eval_btn.click(fn=wargame_evaluate_and_learn, inputs=[wg_chatbot], outputs=[wg_chatbot, wg_chat_input])
            wg_chat_send_btn.click(fn=wg_chat_send, inputs=[wg_chat_input, wg_chatbot], outputs=[wg_chatbot, wg_chat_input])
            wg_chat_input.submit(fn=wg_chat_send, inputs=[wg_chat_input, wg_chatbot], outputs=[wg_chatbot, wg_chat_input])
            wg_chat_clear_btn.click(
                fn=lambda: (_save_chat_history([]) or [], ""),
                outputs=[wg_chatbot, wg_chat_input]
            )
            wg_timer.tick(fn=wargame_refresh_with_alert, inputs=[wg_chatbot], outputs=[wg_map, wg_damage_chart, wg_status, wg_event_log, wg_chatbot])
            app.load(fn=wargame_on_load, outputs=[wg_startstop_btn, wg_map, wg_damage_chart, wg_status, wg_event_log, wg_chatbot, wg_plan_box, wg_timescale])
        harness_start_btn.click(
            fn=harness_start_training,
            inputs=[harness_n_episodes, harness_replan_interval, harness_chatbot],
            outputs=[harness_chatbot, harness_status_text, harness_last_episode]
        )
        harness_stop_btn.click(fn=harness_stop_training, inputs=[harness_chatbot], outputs=[harness_chatbot])
        harness_refresh_btn.click(fn=harness_get_status, outputs=[harness_status_text, harness_last_episode, harness_stats_text])
        harness_rules_refresh_btn.click(fn=harness_get_rules, outputs=[harness_rules_md])
        harness_timer.tick(fn=harness_get_status, outputs=[harness_status_text, harness_last_episode, harness_stats_text])
        app.load(fn=harness_get_rules, outputs=[harness_rules_md])
        app.load(
            fn=lambda: [[m[0], m[1]] for m in _load_ui_state().get("harness_chat", []) if len(m) == 2],
            outputs=[harness_chatbot],
        )
        map_refresh_btn.click(fn=get_battlefield_map, outputs=[map_plot, map_status])
        map_timer.tick(fn=get_battlefield_map, outputs=[map_plot, map_status])
        app.load(fn=get_battlefield_map, outputs=[map_plot, map_status])
        app.load(
            fn=lambda: [[m[0], m[1]] for m in _load_ui_state().get("main_chat", []) if len(m) == 2],
            outputs=[chatbot],
        )
    return app


def launch_app(agent=None, **kwargs):
    ui_cfg = _load_ui_config()
    app = create_app(agent=agent)
    app.launch(server_name=kwargs.get("server_name", ui_cfg.get("server_name", "0.0.0.0")), server_port=kwargs.get("server_port", ui_cfg.get("server_port", 7860)), share=kwargs.get("share", ui_cfg.get("share", False)))
