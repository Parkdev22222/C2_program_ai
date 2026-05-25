"""
C2 군사 AI - Gradio 웹 인터페이스
"""
import re
import time
import logging
import queue as _queue
import threading
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
    from wargame import WargameEngine, setup_bn_vs_bn_blufor_random as setup_bn_vs_bn
    from wargame.llm_planner import MissionPlanner
    from wargame.terrain import get_heightmap, GRID_W, GRID_H, MAP_W, MAP_H
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

CONFIG_DIR = Path(__file__).parent.parent / "config"
SAMPLES_DIR = Path(__file__).parent.parent / "samples"


def _load_ui_config() -> dict:
    with open(CONFIG_DIR / "agent_config.yaml") as f:
        return yaml.safe_load(f).get("gradio", {})


_agent = None
_video_analysis_system = None
_analyzed_videos: List[dict] = []
_active_video_ids: List[str] = []
_last_situation_analysis: str = ""

_wg_engine: Optional["WargameEngine"] = None
_wg_planner: Optional["MissionPlanner"] = None
_wg_last_plan: dict = {}
_wg_last_opfor_ai_count: int = 0
_harness_controller = None

# ── 자동 탐지 → 임무계획 수립 ────────────────────────────────────
# 엔진 탐지 콜백이 이 큐에 이벤트를 넣고, 백그라운드 워커가 처리한다
_detection_queue: _queue.Queue = _queue.Queue()
_auto_plan_lock = threading.Lock()   # 동시 자동 계획 방지

# 자동 재계획 진행 상태 (UI 폴링용)
_auto_plan_status: dict = {"active": False, "message": "", "started_at": 0.0}

# 마지막 재계획이 완료된 시점의 엔진 틱 (30틱 쿨다운용)
_last_replan_tick: int = -30


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


def _update_situation_memory_if_needed(response: str, video_ids: List[str] = None):
    global _last_situation_analysis
    if _is_situation_analysis_response(response):
        _last_situation_analysis = response
        try:
            from tools.strategy_advisor_tool import update_situation_memory
            update_situation_memory(response, video_ids or _active_video_ids)
            logger.info("Situation memory updated from EXAONE4 analysis response")
        except Exception as e:
            logger.warning(f"Failed to update situation memory: {e}")


def _is_strategy_query(text: str) -> bool:
    try:
        from agent.battlefield_agent import is_strategy_query
        return is_strategy_query(text)
    except Exception:
        strategy_kw = ["전략", "전술", "작전", "기동", "대응방안", "추천", "제안",
                       "strategy", "tactics", "maneuver", "recommend"]
        text_lower = text.lower()
        return any(kw in text_lower for kw in strategy_kw)


def analyze_video(video_file, collection_name: str, progress=gr.Progress()):
    global _video_analysis_system, _analyzed_videos, _active_video_ids
    choices = _get_video_list_choices()
    if video_file is None:
        return "영상 파일을 먼저 업로드하세요.", gr.update(choices=choices, value=choices)
    try:
        progress(0.1, desc="영상 분석 시스템 초기화 중...")
        if _video_analysis_system is None:
            from core_src.video_analysis_system import VideoAnalysisSystem
            _video_analysis_system = VideoAnalysisSystem(collection_name=collection_name or "default")
        progress(0.3, desc="비디오 분석 중...")
        summary = _video_analysis_system.analyze_video(
            video_path=video_file.name if hasattr(video_file, "name") else str(video_file),
        )
        video_id = summary["video_id"]
        _analyzed_videos.append({"video_id": video_id, "filename": Path(video_file.name if hasattr(video_file, "name") else str(video_file)).name, "summary": summary})
        _active_video_ids = [v["video_id"] for v in _analyzed_videos]
        try:
            from tools.videodb_query_tool import set_selected_video_ids, register_videodb_manager, register_video_collection
            set_selected_video_ids(_active_video_ids)
            register_videodb_manager(collection_name or "default", _video_analysis_system.videodb)
            register_video_collection(video_id, collection_name or "default")
        except Exception as e:
            logger.warning(f"Failed to update tool context: {e}")
        if _agent:
            _agent.set_video_context(_active_video_ids)
        progress(1.0, desc="분석 완료!")
        total_dets = summary.get("total_detections", 0)
        result_msg = (f"✓ 영상 분석 완료\n  - 비디오 ID: {video_id}\n  - 총 길이: {summary.get('duration', 0):.1f}초\n  - 세그먼트 수: {summary.get('segment_count', 0)}개\n  - 탐지된 객체 수: {total_dets}건\n\n이제 채팅창에서 영상에 대해 질문하거나 전략/전술 추천을 요청할 수 있습니다.")
        new_choices = _get_video_list_choices()
        return result_msg, gr.update(choices=new_choices, value=new_choices)
    except Exception as e:
        logger.error(f"Video analysis error: {e}", exc_info=True)
        choices = _get_video_list_choices()
        return f"분석 오류: {e}", gr.update(choices=choices, value=choices)


def _get_video_list_choices() -> list:
    return [f"{v['video_id']} - {v['filename']}" for v in _analyzed_videos]


def _get_sample_video_choices() -> list:
    SAMPLES_DIR.mkdir(exist_ok=True)
    exts = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
    return sorted(p.name for p in SAMPLES_DIR.iterdir() if p.suffix.lower() in exts)


def analyze_sample_video(sample_name: str, collection_name: str, progress=gr.Progress()):
    if not sample_name:
        choices = _get_video_list_choices()
        return "예시 영상을 선택하세요.", gr.update(choices=choices, value=choices)
    sample_path = SAMPLES_DIR / sample_name
    if not sample_path.exists():
        choices = _get_video_list_choices()
        return f"파일을 찾을 수 없습니다: {sample_name}", gr.update(choices=choices, value=choices)
    class _FileLike:
        def __init__(self, path): self.name = str(path)
    return analyze_video(_FileLike(sample_path), collection_name, progress)


def update_active_videos(selected_items: List[str]) -> str:
    global _active_video_ids
    _active_video_ids = []
    for item in (selected_items or []):
        vid = item.split(" - ")[0].strip()
        _active_video_ids.append(vid)
    try:
        from tools.videodb_query_tool import set_selected_video_ids
        set_selected_video_ids(_active_video_ids)
    except Exception as e:
        logger.warning(f"Failed to update video context: {e}")
    if _agent:
        _agent.set_video_context(_active_video_ids)
    return f"활성 비디오: {len(_active_video_ids)}개 선택됨"


def chat(message: str, history: List[Tuple[str, str]]) -> Tuple[str, List[Tuple[str, str]]]:
    if not message.strip():
        return "", history
    agent = _get_agent()
    if agent is None:
        history.append((message, "에이전트가 초기화되지 않았습니다. main.py를 통해 실행해주세요."))
        return "", history
    is_strategy = _is_strategy_query(message)
    if is_strategy and not _last_situation_analysis:
        warning = ("[안내] 전략/전술 추천을 위해서는 먼저 군사 영상을 분석하는 것을 권장합니다.\n")
        history.append((message, warning + "처리 중..."))
    else:
        history.append((message, "처리 중..."))
    try:
        response = agent.run(message)
        response_text = str(response)
        _update_situation_memory_if_needed(response_text, _active_video_ids)
        if is_strategy:
            response_text = _annotate_dual_model_response(response_text)
        history[-1] = (message, response_text)
    except Exception as e:
        logger.error(f"Agent run error: {e}", exc_info=True)
        history[-1] = (message, f"처리 중 오류가 발생했습니다: {e}")
    _save_ui_state(main_history=history)
    return "", history


def _annotate_dual_model_response(response: str) -> str:
    return response + "\n\n---\n*이 응답은 EXAONE4(상황 분석) + EXAONE Deep(전략/전술 추천)의 협업으로 생성되었습니다.*"


_MARKER_SYMBOL = {"infantry": "circle", "apc": "square", "armor": "diamond", "helicopter": "triangle-up", "aircraft": "triangle-up", "vehicle": "square", "truck": "square", "unknown": "circle"}
_MARKER_SIZE = {"infantry": 5, "apc": 8, "armor": 9, "helicopter": 8, "aircraft": 8, "vehicle": 6, "truck": 6, "unknown": 5}


def _build_map_figure(state: dict):
    units = state.get("units", [])
    groups = state.get("groups", [])
    mission_time = state.get("mission_time", 0)
    last_updated = state.get("last_updated", "데이터 없음")
    fig = go.Figure()
    if not units and not groups:
        fig.add_annotation(text="ARMA3 데이터 없음<br>relay.py가 실행 중인지 확인하세요", x=0.5, y=0.5, xref="paper", yref="paper", font=dict(size=16, color="#aaaaaa"), showarrow=False)
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
    try:
        from core_src.arma3_db_manager import load_state
        state = load_state()
    except Exception as e:
        return None, f"상태 로드 오류: {e}"
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


def _wg_ensure_engine() -> Optional["WargameEngine"]:
    global _wg_engine, _wg_planner
    if not _WARGAME_OK:
        return None
    if _wg_engine is None:
        units = setup_bn_vs_bn()
        _wg_engine = WargameEngine(units)
        _wg_planner = MissionPlanner()
        _wg_register_engine(_wg_engine)
        # 자동 탐지 임무계획 콜백 등록
        _wg_engine.on_new_opfor_detection = _detection_enqueue
        # BLUFOR 전투력 임계값 임무계획 콜백 등록
        _wg_engine.on_blufor_cp_threshold = _cp_threshold_enqueue
        # BLUFOR 유닛 공중지원 피격 임무계획 콜백 등록
        _wg_engine.on_blufor_air_hit = _air_hit_enqueue
    return _wg_engine


def _get_recon_unit_ids() -> list:
    """현재 워게임에서 BLUFOR 정찰부대 ID 목록 반환."""
    eng = _wg_ensure_engine()
    if eng is None:
        return ["Delta"]
    try:
        state = eng.get_state()
        ids = [u["id"] for u in state.get("units", [])
               if u.get("side") == "BLUFOR" and u.get("unit_type") == "정찰"]
        return ids if ids else ["Delta"]
    except Exception:
        return ["Delta"]


def wargame_apply_custom_scenario(scenario_config: dict) -> dict:
    """사용자 정의 시나리오 적용 — 부대 구성·배치 변경 후 엔진 리셋."""
    global _wg_engine, _wg_planner, _wg_last_plan
    if not _WARGAME_OK:
        return {"ok": False, "error": "워게임 초기화 실패"}
    try:
        from wargame.scenario import setup_custom_scenario
        from tools.mission_plan_validator import update_valid_company_ids

        blufor_defs = scenario_config.get("blufor", [])
        opfor_defs  = scenario_config.get("opfor", [])

        if not blufor_defs:
            return {"ok": False, "error": "BLUFOR 부대가 없습니다."}
        if not opfor_defs:
            return {"ok": False, "error": "OPFOR 부대가 없습니다."}

        # validator 업데이트
        update_valid_company_ids({bd["id"] for bd in blufor_defs})

        units = setup_custom_scenario(blufor_defs, opfor_defs)

        if _wg_engine is not None:
            _wg_engine.reset(units)
        else:
            _wg_engine = WargameEngine(units)
            _wg_register_engine(_wg_engine)

        if _wg_planner is None:
            _wg_planner = MissionPlanner()

        _wg_engine.on_new_opfor_detection = _detection_enqueue
        _wg_engine.on_blufor_cp_threshold = _cp_threshold_enqueue
        _wg_engine.on_blufor_air_hit      = _air_hit_enqueue
        _wg_last_plan = {}

        logger.info("사용자 정의 시나리오 적용 완료: BLUFOR %d개, OPFOR %d개",
                    len(blufor_defs), len(opfor_defs))
        return {"ok": True, "blufor": len(blufor_defs), "opfor": len(opfor_defs)}
    except Exception as e:
        logger.exception("wargame_apply_custom_scenario 오류")
        return {"ok": False, "error": str(e)}


# ── 자동 탐지 / 전투력 임계값 / 공중지원 피격 → 공격임무계획 수립 ──
# 큐 이벤트 형식:
#   ("detection",    enemy_id, unit_type, x, y)
#   ("cp_threshold", unit_id, unit_type, threshold_pct, current_cp)
#   ("air_hit",      unit_id, unit_type, call_sign, current_cp)

def _detection_enqueue(enemy_id: str, unit_type: str, x: float, y: float):
    """엔진 틱 스레드에서 호출 — 큐에만 넣고 즉시 반환."""
    _detection_queue.put_nowait(("detection", enemy_id, unit_type, x, y))


def _cp_threshold_enqueue(unit_id: str, unit_type: str,
                          threshold_pct: float, current_cp: float):
    """BLUFOR CP 임계값 도달 시 엔진 틱 스레드에서 호출 — 큐에만 넣고 즉시 반환."""
    _detection_queue.put_nowait(("cp_threshold", unit_id, unit_type,
                                 threshold_pct, current_cp))


def _air_hit_enqueue(unit_id: str, unit_type: str,
                     call_sign: str, current_cp: float):
    """BLUFOR 유닛이 OPFOR 공중지원에 피격 시 엔진 틱 스레드에서 호출."""
    _detection_queue.put_nowait(("air_hit", unit_id, unit_type,
                                 call_sign, current_cp))


def _execute_auto_attack_plan(event_type: str, *args):
    """
    신규 OPFOR 탐지 / BLUFOR CP 임계값 / BLUFOR 공중지원 피격 시 공격임무계획 재수립.
    별도 백그라운드 스레드에서 실행됨.

    event_type == "detection"    : args = (enemy_id, unit_type, x, y)
    event_type == "cp_threshold" : args = (unit_id, unit_type, threshold_pct, current_cp)
    event_type == "air_hit"      : args = (unit_id, unit_type, call_sign, current_cp)
    """
    eng = _wg_engine
    if eng is None:
        logger.warning("[자동임무계획] 엔진 없음 — 건너뜀")
        return

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

    if _wg_planner is None:
        logger.warning("[자동임무계획] planner 없음 → 재개")
        _auto_plan_status["active"] = False
        _auto_plan_status["message"] = ""
        if was_running:
            eng.start()
        return

    agent = _get_agent()

    try:
        state = eng.get_state()
        import json as _j
        from wargame.llm_planner import build_mission_query

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
            from agent.battlefield_agent import get_instruction_section
            attack_rules      = get_instruction_section("ATTACK")
            execution_rules   = get_instruction_section("EXECUTION")
            learned_rules     = get_instruction_section("LEARNED_RULES")
        except Exception:
            attack_rules = execution_rules = learned_rules = ""

        learned_suffix = f"\n\n[학습된 규칙]\n{learned_rules}" if learned_rules else ""
        _recon_ids = _get_recon_unit_ids()
        _recon_id_str = ", ".join(_recon_ids) if _recon_ids else "정찰부대"
        base_query = build_mission_query(state)

        # get_wargame_situation() 결과를 미리 계산해 쿼리에 포함 → LLM Step 1 호출 생략
        _sit_block = ""
        try:
            import json as _sit_j
            from tools.wargame_query_tool import get_wargame_situation as _get_sit_fn
            _precomputed_sit = _get_sit_fn()
            _sit_block = (
                "\n[현재 전장 상황 — 미리 수집됨]\n"
                "아래 데이터를 situation 변수로 간주하고 get_wargame_situation() 호출 생략.\n"
                f"```json\n{_sit_j.dumps(_precomputed_sit, ensure_ascii=False)}\n```\n"
            )
        except Exception as _sit_err:
            logger.debug(f"[자동임무계획] 전장상황 사전 수집 실패 (무시): {_sit_err}")

        full_query = (
            f"⛔ [최우선 지시 — 반드시 준수]\n"
            f"1. 모든 툴 호출을 완료하기 전에 절대 final_answer()를 호출하지 말 것.\n"
            f"2. {_recon_id_str}는 recon 임무로 mission_plans에 포함. 나머지 부대는 공격임무(attack/defend/flank/withdraw/hold) 부여.\n"
            f"3. recon_advisor_tool 호출 금지. recommend_recon_routes는 반드시 호출하여 {_recon_id_str} 경로 생성에 사용.\n"
            f"4. get_wargame_situation() 호출 금지 — 전장 상황이 아래 [현재 전장 상황]에 이미 제공됨.\n"
            f"5. apply_wargame_mission_plan(dry_run=False) 호출 후 즉시 final_answer() 호출하고 종료. 추가 툴 호출 절대 금지.\n"
            f"{_sit_block}\n"
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
                    from tools.wargame_mission_tool import reset_apply_tracker
                    reset_apply_tracker()
                except Exception:
                    pass

                # 이전 실행(정찰 임무 등)의 Python 네임스페이스 잔류 변수 제거
                # smolagents CodeAgent.run(reset=True)는 대화 기록만 초기화하고
                # python_executor.state(로컬 변수 공간)는 초기화하지 않는다.
                # recon_routes 등 이전 변수가 남아있으면 final_answer()를 즉시 호출해버림.
                try:
                    _inner_agent = agent.agent
                    for _exec_attr in ('python_executor', 'python_interpreter', '_executor'):
                        _exec = getattr(_inner_agent, _exec_attr, None)
                        if _exec is not None:
                            for _state_attr in ('state', '_state', 'local_vars', '_local_vars'):
                                _ns = getattr(_exec, _state_attr, None)
                                if isinstance(_ns, dict):
                                    _ns.clear()
                                    logger.info(f"[자동임무계획] Python executor namespace cleared ({_exec_attr}.{_state_attr})")
                                    break
                            break
                except Exception as _cls_err:
                    logger.debug(f"[자동임무계획] namespace clear 실패 (무시): {_cls_err}")

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
                    logger.warning(f"[자동임무계획] 에이전트 타임아웃 ({_AGENT_TIMEOUT}s) → 규칙 기반 폴백")
                    raise RuntimeError("agent timeout")
                if _err_holder[0]:
                    raise _err_holder[0]
                raw = _raw_holder[0]

                # 툴 호출로 이미 적용됐는지 타임스탬프로 판단
                try:
                    from tools.wargame_mission_tool import was_plan_applied_since
                    _tool_applied = was_plan_applied_since(_session_start)
                except Exception:
                    _tool_applied = False

                plan = _wg_planner._parse_json(str(raw))
                if plan and "mission_plans" in plan:
                    # 에이전트가 JSON을 반환한 경우 → 위경도→미터 변환 후 직접 적용
                    try:
                        plan = _convert_latlon_plan_to_meters(plan)
                        eng.apply_mission_plan(plan)
                        if plan.get("air_support_plans"):
                            eng.apply_air_support_plan(plan)
                        logger.info(f"[자동임무계획] 에이전트 계획 적용 완료 "
                                    f"— {len(plan['mission_plans'])}개 중대 재배정")
                    except Exception as _ae:
                        logger.warning(f"[자동임무계획] 계획 적용 오류: {_ae}")
                elif _tool_applied:
                    # 에이전트가 apply_wargame_mission_plan 툴을 직접 호출해 이미 적용 완료
                    logger.info("[자동임무계획] 에이전트가 툴로 계획 직접 적용 완료 — 폴백 불필요")
                else:
                    logger.warning(f"[자동임무계획] 에이전트 미적용 → 규칙 기반 폴백")
                    plan = _wg_planner._rule_based(state)
                    eng.apply_mission_plan(plan)
                    if plan.get("air_support_plans"):
                        eng.apply_air_support_plan(plan)
                        logger.info(f"[자동임무계획] 규칙 기반 공중지원 {len(plan['air_support_plans'])}회 적용")
            except Exception as _e:
                logger.warning(f"[자동임무계획] 에이전트 실행 실패: {_e} → 규칙 기반 폴백")
                plan = _wg_planner._rule_based(state)
                eng.apply_mission_plan(plan)
                if plan.get("air_support_plans"):
                    eng.apply_air_support_plan(plan)
                    logger.info(f"[자동임무계획] 규칙 기반 공중지원 {len(plan['air_support_plans'])}회 적용")
        else:
            plan = _wg_planner._rule_based(state)
            eng.apply_mission_plan(plan)
            if plan.get("air_support_plans"):
                eng.apply_air_support_plan(plan)
                logger.info(f"[자동임무계획] 규칙 기반 공중지원 {len(plan['air_support_plans'])}회 적용")
            logger.info("[자동임무계획] 규칙 기반 계획 적용")

    except Exception as _ex:
        logger.error(f"[자동임무계획] 오류: {_ex}", exc_info=True)
        try:
            state = eng.get_state()
            plan = _wg_planner._rule_based(state)
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
        if was_running and not eng.running:
            eng.start()
            logger.info("[자동임무계획] 시뮬레이션 재개")
        try:
            from tools.wargame_mission_tool import set_resume_on_apply
            set_resume_on_apply(False)
        except Exception:
            pass


def _detection_worker():
    """백그라운드 데몬 스레드 — 탐지 큐를 소비하여 자동 임무계획 수립.

    30틱 쿨다운: 마지막 재계획 완료 후 30틱 이내에 발생한 이벤트는 모두 무시한다.
    동시 발생 이벤트 배치: 큐에 쌓인 추가 이벤트를 한번에 드레인하여 중복 재계획 방지.
    """
    global _last_replan_tick
    while True:
        try:
            event = _detection_queue.get(timeout=2.0)
        except _queue.Empty:
            continue

        # 큐에 쌓인 추가 이벤트를 모두 드레인 (30틱 내 복수 이벤트 → 1회 처리)
        extra_count = 0
        try:
            while True:
                _detection_queue.get_nowait()
                extra_count += 1
        except _queue.Empty:
            pass
        if extra_count:
            logger.info(f"[자동임무계획] 동시 발생 이벤트 {extra_count}개 병합 → 1회 재계획 처리")

        # 30틱 쿨다운 확인
        eng = _wg_engine
        if eng is not None:
            ticks_since_last = eng.tick - _last_replan_tick
            if ticks_since_last < 30:
                logger.info(
                    f"[자동임무계획] 30틱 쿨다운 — 마지막 재계획 후 {ticks_since_last}틱 경과, "
                    f"{event[0]} 이벤트 건너뜀"
                )
                continue

        # 동시 계획 방지: 이미 계획 중이면 이벤트 무시
        if not _auto_plan_lock.acquire(blocking=False):
            logger.info(f"[자동임무계획] 계획 수립 중 — {event[0]} 이벤트 건너뜀")
            continue
        try:
            _execute_auto_attack_plan(*event)
        finally:
            if eng is not None:
                _last_replan_tick = eng.tick
            _auto_plan_lock.release()


# 백그라운드 워커 스레드 시작 (앱 로드 시 1회)
threading.Thread(target=_detection_worker, daemon=True, name="DetectionWorker").start()


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
            title="전투력 (%)",
            range=[0, 100],
            tickfont=dict(color="#aaaaaa", size=9),
            gridcolor="#2a3a4a",
            zeroline=False,
            titlefont=dict(color="#aaaaaa", size=10),
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
    eng = _wg_ensure_engine()
    if eng is None:
        return "워게임 초기화 실패", *wargame_refresh()
    if eng.running:
        eng.stop()
        label = "▶ 시뮬레이션 시작"
    else:
        eng.start()
        label = "⏸ 일시정지"
    fig, damage_fig, status, log_text = wargame_refresh()
    return label, fig, damage_fig, status, log_text


def wargame_reset_sim():
    global _wg_engine, _wg_planner, _wg_last_plan
    if not _WARGAME_OK:
        return "초기화 실패", None, None, "", ""
    units = setup_bn_vs_bn()
    if _wg_engine is not None:
        _wg_engine.reset(units)
    else:
        _wg_engine = WargameEngine(units)
        _wg_register_engine(_wg_engine)
    # planner가 없으면 항상 초기화
    if _wg_planner is None:
        _wg_planner = MissionPlanner()
    # 콜백 항상 재등록 — 초기화 순서에 무관하게 보장
    _wg_engine.on_new_opfor_detection = _detection_enqueue
    _wg_engine.on_blufor_cp_threshold = _cp_threshold_enqueue
    _wg_engine.on_blufor_air_hit      = _air_hit_enqueue
    _wg_last_plan = {}
    fig, damage_fig, status, log_text = wargame_refresh()
    return "▶ 시뮬레이션 시작", fig, damage_fig, status, log_text


def wargame_set_timescale(scale: float):
    eng = _wg_ensure_engine()
    if eng:
        eng.time_scale = float(scale)
        _save_ui_state(timescale=float(scale))
    return wargame_refresh()


def wargame_request_recon_plan(history: List = None):
    """
    정찰 임무계획 수립 — 에이전트 툴 활용 순서
    ─────────────────────────────────────────────
    Step 1. assess_recon_need()
            └─ OPFOR 탐지 현황(detected / approximate / lost) 확인
               → 정찰 불필요 시 즉시 반환
    Step 2. recommend_recon_routes()
            └─ 정찰부대(unit_type=정찰) 경로 생성
               → apply_json, summary, mission_plans 반환
    Step 3. recon_advisor_tool(recon_routes_json=..., recon_summary=...)   [선택]
            └─ EXAONE Deep 전술 검토 — 경로 개선 의견 수신
    Step 4. 최종 정찰 임무계획 JSON 직접 생성
            └─ Step 2·3 결과 종합 / unit_type=정찰 부대만 포함
    Step 5. apply_wargame_mission_plan(plan_json=<JSON>, dry_run=False)
            └─ 워게임 엔진에 즉시 적용 (dry_run=True 사용 금지)
    Step 6. 응답에 최종 JSON 블록 출력
    ─────────────────────────────────────────────
    금지: validate/approve 툴 호출, 공격부대(Alpha/Bravo/Charlie/Echo) 임무 부여,
          정찰+공격 임무 동시 생성
    """
    global _wg_last_plan
    history = list(history or [])
    eng = _wg_ensure_engine()
    if eng is None:
        history.append(("🔍 정찰 임무계획 요청", "워게임 초기화 실패"))
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, "", fig, damage_fig, status, log_text, ""
    try:
        from tools.wargame_recon_tool import assess_recon_need, recommend_recon_routes
    except ImportError as e:
        history.append(("🔍 정찰 임무계획 요청", f"정찰 도구 로드 실패: {e}"))
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, "", fig, damage_fig, status, log_text, ""
    assessment = assess_recon_need()
    opfor_sum = assessment.get("opfor_summary", {})
    if assessment.get("recommendation") == "공격 즉시 가능":
        msg = (f"**✅ 모든 OPFOR 위치가 이미 탐지되어 정찰이 불필요합니다.**\n\n탐지된 적군: {opfor_sum.get('detected', 0)}개\n\n→ **⚔️ 공격 임무계획** 버튼을 사용하여 공격을 시작하세요.")
        history.append(("🔍 정찰 임무계획 요청", msg))
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, "", fig, damage_fig, status, log_text, ""
    if assessment.get("recommendation") == "적 없음":
        history.append(("🔍 정찰 임무계획 요청", "탐지된 적군이 없습니다."))
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, "", fig, damage_fig, status, log_text, ""
    agent = _get_agent()
    agent_label = "BattlefieldAgent" if agent else "규칙 기반"
    try:
        from agent.battlefield_agent import get_instruction_section
        recon_rules = get_instruction_section("RECON")
        execution_rules = get_instruction_section("EXECUTION")
        learned_rules = get_instruction_section("LEARNED_RULES")
    except Exception:
        recon_rules = execution_rules = learned_rules = ""
    learned_suffix = f"\n\n[학습된 규칙]\n{learned_rules}" if learned_rules else ""
    # ── 정찰 임무 쿼리 ────────────────────────────────────────────
    # 전장 상황(부대 위치·전투력·인텔)은 쿼리에 직접 포함하지 않는다.
    # 에이전트가 아래 tool을 순서대로 호출하여 직접 조회한다:
    #   1) assess_recon_need()        → OPFOR 탐지 현황 및 정찰 필요 여부
    #   2) recommend_recon_routes()   → 교전 회피 정찰 경로 + apply_json
    #   3) recon_advisor_tool(...)    → EXAONE Deep 전술 검토
    #   4) apply_wargame_mission_plan(plan_json=..., dry_run=False) → 즉시 적용
    #
    # [생성 예시]
    # ┌─────────────────────────────────────────────────────────────────┐
    # │ [정찰 임무계획 수립]                                             │
    # │                                                                  │
    # │ 현재 전장 상황(부대 위치·전투력·인텔 등)은 반드시 도구(tool)를   │
    # │ 호출하여 조회하라.                                               │
    # │ assess_recon_need 및 recommend_recon_routes 도구를 사용하여      │
    # │ 정찰 임무를 계획하라.                                            │
    # │                                                                  │
    # │ [RECON 규칙]                                                     │
    # │ - unit_type이 '정찰'인 부대에만 임무 부여 (Delta 부대)           │
    # │ - 공격부대(Alpha, Bravo, Charlie, Echo)는 정찰 임무 금지         │
    # │ - 정찰 임무계획과 공격 임무계획을 동시에 생성 금지               │
    # │ - 금지 툴: validate_mission_plan_tool, approve_mission_plan_tool │
    # │                                                                  │
    # │ [EXECUTION 규칙]                                                 │
    # │ - apply_wargame_mission_plan 호출 시 항상 dry_run=False 사용     │
    # │ - 별도 승인 절차 없이 dry_run=False로 직접 호출                  │
    # │                                                                  │
    # │ (학습된 규칙 있을 경우 [학습된 규칙] 섹션 추가됨)               │
    # └─────────────────────────────────────────────────────────────────┘
    recon_query = (
        f"[정찰 임무계획 수립]\n\n"
        f"현재 전장 상황(부대 위치·전투력·인텔 등)은 반드시 도구(tool)를 호출하여 조회하라.\n\n"
        f"[툴 활용 순서 — 반드시 이 순서대로 호출]\n"
        f"1. assess_recon_need()\n"
        f"   → OPFOR 탐지 현황(detected/approximate/lost) 및 정찰 필요 여부 확인\n"
        f"   → 탐지 상실(lost) 또는 개략위치(approximate) OPFOR 식별\n"
        f"2. recommend_recon_routes()\n"
        f"   → 정찰부대(unit_type=정찰) 기준 교전 회피 경로 자동 생성\n"
        f"   → 반환값: apply_json(엔진 적용용 JSON — 미터 좌표), summary, mission_plans(위경도 표시용)\n"
        f"   ※ apply_json은 이미 엔진 내부 미터 좌표로 작성되어 있음 — 좌표 수정 금지\n"
        f"3. recon_advisor_tool(recon_routes_json=<apply_json>, recon_summary=<summary>)  [선택]\n"
        f"   → EXAONE Deep에게 경로 전술 검토 요청 → 개선 의견 수신\n"
        f"4. apply_wargame_mission_plan(plan_json=<apply_json>, dry_run=False)\n"
        f"   → Step 2의 apply_json을 그대로 사용 (좌표 수정 금지, dry_run=True 절대 금지)\n"
        f"   → 워게임 엔진에 즉시 적용\n"
        f"5. 응답에 apply_json의 JSON 블록 출력\n\n"
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
            from tools.wargame_mission_tool import set_resume_on_apply
            set_resume_on_apply(True)  # apply_wargame_mission_plan 호출 시 자동 재개
        except Exception:
            pass

    import json as _json, re as _re
    agent_response_text = ""
    applied_plan = None
    # 정찰 경로를 미리 생성 — apply_json은 미터 좌표로 작성되어 있음
    _base_recon_result = recommend_recon_routes()
    if _base_recon_result.get("status") == "no_recon_units":
        msg = f"**⚠️ 사용 가능한 정찰부대(unit_type=정찰)가 없습니다.**\n\n{assessment.get('reason', '')}\n\n→ **⚔️ 공격 임무계획** 버튼을 사용하거나 채팅창에서 전술 조언을 요청하세요."
        history[-1] = (history[-1][0], msg)
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, "", fig, damage_fig, status, log_text, ""
    _base_apply_json = _base_recon_result.get("apply_json", "")
    try:
        if agent is not None:
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

            # 에이전트가 tool로 적용했더라도 gradio에서도 안전망으로 적용.
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
            fig, damage_fig, status, log_text = wargame_refresh()
            return history, "", fig, damage_fig, status, log_text, ""
        _wg_last_plan = applied_plan
        plans = applied_plan.get("mission_plans", [])
        plan_text = _json.dumps(applied_plan, ensure_ascii=False, indent=2)
        unit_lines = "\n".join(f"  - **{p['company_id']}** (정찰) → {p.get('objective', '')} ({len(p.get('waypoints', []))}개 경유지)" for p in plans)
        deep_review = ""
        review_match = _re.search(r"### 정찰 임무계획 검토 의견\\s*(.*?)(?=###|\\Z)", agent_response_text, _re.DOTALL)
        if review_match:
            deep_review = f"**EXAONE Deep 검토 의견:**\n{review_match.group(1).strip()[:600]}\n\n"
        result_msg = (f"**🔍 정찰 임무계획 생성 완료** ({agent_label})\n\n{deep_review}**OPFOR 탐지 현황:**\n  - 정확히 탐지됨: {opfor_sum.get('detected', 0)}개\n  - 개략위치 파악: {opfor_sum.get('approximate', 0)}개\n  - 탐지 상실: {opfor_sum.get('lost', 0)}개\n\n**파견 정찰부대 (unit_type=정찰 한정):** {len(plans)}개\n{unit_lines}\n\n⚠️ **공격부대(Alpha/Bravo/Charlie/Echo)는 대기 중입니다.** 정찰 완료로 적 위치가 탐지되면 **⚔️ 공격 임무계획** 버튼을 눌러 공격을 개시하세요.\n\n```json\n{plan_text}\n```")
        history[-1] = (history[-1][0], result_msg)
        _save_ui_state(wg_history=history, plan_box=plan_text)
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, plan_text, fig, damage_fig, status, log_text
    finally:
        # 에이전트가 apply_wargame_mission_plan을 호출하지 않은 경우 안전망
        if was_running and not eng.running:
            eng.start()
            logger.info("시뮬레이션 재개 (finally 안전망) — 정찰 임무계획 함수 종료")
        try:
            from tools.wargame_mission_tool import set_resume_on_apply
            set_resume_on_apply(False)
        except Exception:
            pass


def wargame_request_attack_plan(history: List = None):
    """
    공격 임무계획 수립 — 에이전트 툴 활용 순서
    ─────────────────────────────────────────────
    Step 1. get_wargame_situation()
            └─ 현재 전장 상황(부대 위치·전투력·행동) 조회
    Step 2. assess_recon_need()
            └─ OPFOR 탐지 현황 확인 → detected / approximate / lost 분류
               → detected 목표만 공격 대상, approximate/lost는 제외
    Step 3. get_optimal_attack_positions()
            └─ 탐지된 OPFOR 기준 최적 공격 위치·기동 방향 추천
               → 결과를 변수에 저장 (Step 4 additional_context로 전달)
    Step 4. strategy_advisor_tool(
              query="공격 임무계획 전술 검토 요청",
              additional_context=<Step 3 결과>
            )
            └─ EXAONE Deep이 공격 위치 결과를 검토하여 전술 조언 제공
               → 조언을 변수에 저장 (Step 5 JSON 생성에 반영)
    Step 5. 최종 임무계획 JSON 생성
            └─ Step 3 공격 위치 + Step 4 EXAONE Deep 조언 종합
               detected OPFOR만 목표 / 공중지원도 detected 위치에만
               CP < 30% 부대 → defend/withdraw / 나머지 → attack/flank
    Step 6. apply_wargame_mission_plan(plan_json=<JSON>, dry_run=False)
            └─ 워게임 엔진에 즉시 적용 (dry_run=True 절대 금지)
    Step 7. 응답에 최종 JSON 블록 출력
    ─────────────────────────────────────────────
    금지: validate/approve 툴 호출, approximate/lost OPFOR 공중지원 목표 지정,
          정찰부대(unit_type=정찰) 공격/flank 임무 부여 (recon 임무는 필수 포함)
    """
    global _wg_last_plan
    history = list(history or [])
    eng = _wg_ensure_engine()
    if eng is None:
        history.append(("⚔️ 공격 임무계획 요청", "워게임 초기화 실패"))
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, "", fig, damage_fig, status, log_text, ""
    if _wg_planner is None:
        history.append(("⚔️ 공격 임무계획 요청", "Planner 없음"))
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, "", fig, damage_fig, status, log_text, ""
    warning_msg = ""
    try:
        from tools.wargame_recon_tool import assess_recon_need
        assessment = assess_recon_need()
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
    agent = _get_agent()
    agent_label = "BattlefieldAgent" if agent else "규칙 기반"
    import json
    from wargame.llm_planner import build_mission_query
    try:
        from agent.battlefield_agent import get_instruction_section
        attack_rules = get_instruction_section("ATTACK")
        execution_rules_atk = get_instruction_section("EXECUTION")
        learned_rules_atk = get_instruction_section("LEARNED_RULES")
    except Exception:
        attack_rules = execution_rules_atk = learned_rules_atk = ""
    learned_suffix_atk = f"\n\n[학습된 규칙]\n{learned_rules_atk}" if learned_rules_atk else ""
    _atk_recon_ids = _get_recon_unit_ids()
    _atk_recon_str = ", ".join(_atk_recon_ids) if _atk_recon_ids else "정찰부대"
    base_query = build_mission_query(state)
    attack_suffix = (
        f"\n\n⚠️ 예시의 좌표·부대명·호출부호를 절대 그대로 사용 금지. "
        f"모든 값은 반드시 툴 호출 결과에서 가져와야 한다.\n"
        f"⚠️ {_atk_recon_str}(정찰부대)는 반드시 recon 임무로 포함. recommend_recon_routes() 결과의 waypoints 사용.\n\n"
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
            from tools.wargame_mission_tool import set_resume_on_apply
            set_resume_on_apply(True)  # apply_wargame_mission_plan 호출 시 자동 재개
        except Exception:
            pass

    try:
        plan = _wg_planner.plan(state, agent=agent) if agent is None else None
        if plan is None:
            if agent is not None:
                try:
                    # 이전 실행(정찰 등)의 Python namespace 잔류 변수 제거
                    try:
                        _inner_agent = agent.agent
                        for _exec_attr in ('python_executor', 'python_interpreter', '_executor'):
                            _exec = getattr(_inner_agent, _exec_attr, None)
                            if _exec is not None:
                                for _state_attr in ('state', '_state', 'local_vars', '_local_vars'):
                                    _ns = getattr(_exec, _state_attr, None)
                                    if isinstance(_ns, dict):
                                        _ns.clear()
                                        break
                                break
                    except Exception:
                        pass
                    raw = agent.agent.run(full_query, reset=True)
                    plan = _wg_planner._parse_json(str(raw))
                    if plan and "mission_plans" in plan:
                        # 에이전트가 JSON 반환 → 위경도→미터 변환 후 직접 적용
                        try:
                            plan = _convert_latlon_plan_to_meters(plan)
                            eng.apply_mission_plan(plan)
                            if plan.get("air_support_plans"):
                                eng.apply_air_support_plan(plan)
                        except Exception as _ae:
                            logger.warning(f"apply attack plan error: {_ae}")
                    elif (isinstance(raw, dict) and raw.get("status") == "success") or \
                         (isinstance(raw, str) and '"status": "success"' in raw):
                        # 에이전트가 apply_wargame_mission_plan 툴을 직접 호출해 이미 적용 완료
                        logger.info("[공격임무계획] 에이전트가 툴로 계획 직접 적용 완료")
                        plan = {"mission_plans": [], "_tool_applied": True}
                    else:
                        logger.warning(f"[공격임무계획] JSON 파싱 실패 (raw={str(raw)[:120]}) → 규칙 기반 폴백")
                        plan = _wg_planner._rule_based(state)
                        eng.apply_mission_plan(plan)
                        if plan.get("air_support_plans"):
                            eng.apply_air_support_plan(plan)
                except Exception as _ex:
                    logger.warning(f"[공격임무계획] 에이전트 실행 실패: {_ex} → 규칙 기반 폴백")
                    plan = _wg_planner._rule_based(state)
                    eng.apply_mission_plan(plan)
                    if plan.get("air_support_plans"):
                        eng.apply_air_support_plan(plan)
            else:
                plan = _wg_planner._rule_based(state)
                eng.apply_mission_plan(plan)
                if plan.get("air_support_plans"):
                    eng.apply_air_support_plan(plan)
        else:
            # agent is None 경로 — planner가 직접 계획
            eng.apply_mission_plan(plan)
            if plan.get("air_support_plans"):
                eng.apply_air_support_plan(plan)
        _wg_last_plan = plan
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
        _save_ui_state(wg_history=history, plan_box=plan_text)
        fig, damage_fig, status, log_text = wargame_refresh()
        return history, plan_text, fig, damage_fig, status, log_text
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
            from tools.wargame_mission_tool import set_resume_on_apply
            set_resume_on_apply(False)
        except Exception:
            pass


def wg_chat_send(message: str, history: List) -> Tuple[List, str]:
    if not message.strip():
        return history, ""
    history = list(history)
    agent = _get_agent()
    eng = _wg_ensure_engine()
    context = ""
    if eng is not None:
        state = eng.get_state()
        def _fmt_unit(u):
            lat, lon = _xy_to_latlon(u["x"], u["y"])
            return f"  {u['side']} {u['id']}: CP={u['combat_power']:.0f}% 위치=(lat={lat:.4f},lon={lon:.4f}) {u['status']}"
        context = (f"[현재 워게임 상황] 게임시간={state['game_time_str']}\n" + "\n".join(_fmt_unit(u) for u in state["units"]) + "\n\n")
    history.append((message, "처리 중..."))
    if agent is None:
        history[-1] = (message, "에이전트가 초기화되지 않았습니다. main.py를 통해 실행해주세요.")
        return history, ""
    try:
        full_query = context + message if context else message
        response = agent.run(full_query, reset=False)
        history[-1] = (message, str(response))
    except Exception as e:
        logger.error(f"WG chat error: {e}", exc_info=True)
        history[-1] = (message, f"오류: {e}")
    _save_chat_history(history)
    return history, ""


def wargame_evaluate_and_learn(history: List) -> Tuple[List, str]:
    """워게임 현재 상태를 평가하고 학습된 규칙을 agent_custom_instructions.txt에 추가합니다."""
    import re as _re
    history = list(history or [])
    eng = _wg_ensure_engine()
    agent = _get_agent()
    if eng is None:
        history.append(("🧠 전술 평가", "워게임 엔진 없음"))
        return history, ""
    state = eng.get_state()
    try:
        from agent.battlefield_agent import append_learned_rule
    except ImportError:
        history.append(("🧠 전술 평가", "battlefield_agent 로드 실패"))
        return history, ""

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

    # 유닛별 생존/격멸 상태 및 타입 맵
    unit_type_map = {u["id"]: u["unit_type"] for u in state["units"]}
    unit_side_map = {u["id"]: u["side"]      for u in state["units"]}
    unit_cp_map   = {u["id"]: u["combat_power"] for u in state["units"]}

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
        "  2. 특정 좌표([x,y]), 고도 수치(m), 거리 수치(km), 특정 부대명(Red1, Alpha 등) 절대 포함 금지",
        "  3. 부대명 대신 병종(전차, 자주포, 기계화보병, 정찰, 대전차)으로 표현",
        "  4. 수치 대신 상대적 표현 사용: '고지대', '근거리', '측방', '전방', '후방', '우세', '취약'",
        "",
        "  ✗ 나쁜 예: '고도 226m의 [13723, 14083]에서 Red5(자주포) 격멸'",
        "  ✓ 좋은 예: '적 자주포보다 고지대를 선점하여 화력 우위 확보 시 자주포 격멸 효과적'",
        "",
        "  ✗ 나쁜 예: 'Alpha가 (12.3km, 4.5km)에서 Red3와 2km 거리 교전 시 효과적'",
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
        r'|(?:Red|Blue|Alpha|Bravo|Charlie|Delta|Echo|Foxtrot)\d*'  # 특정 부대명
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

    final_rules = []
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
        append_learned_rule(rule_text)
        learned_count += 1

    result_msg = (
        f"**🧠 전술 평가 완료** — {learned_count}개 규칙이 `agent_custom_instructions.txt`에 추가됨\n\n"
        f"{response_text}"
    )
    history.append(("🧠 전술 평가 & 규칙 학습", result_msg))
    _save_chat_history(history)
    return history, ""


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
        return "상황 분석 메모리가 비어 있습니다. 먼저 영상 분석을 수행하세요."
    except Exception as e:
        return f"메모리 상태 조회 오류: {e}"


def _init_harness_controller():
    """하네스 컨트롤러를 초기화합니다."""
    global _harness_controller
    if not _WARGAME_OK:
        return None
    try:
        from wargame.harness import HarnessController
        from wargame import WargameEngine, setup_bn_vs_bn_blufor_random as setup_bn_vs_bn
        from wargame.llm_planner import MissionPlanner

        def _engine_factory():
            units = setup_bn_vs_bn()
            eng = WargameEngine(units)
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
            <h1>⚔ C2 지휘통제 AI — {ui_cfg.get('title', 'EXAONE4 + EXAONE Deep')}</h1>
            <p>{ui_cfg.get('description', '듀얼 모델 아키텍처 | EXAONE4: 상황 분석 · 응답 생성 &nbsp;|&nbsp; EXAONE Deep: 전략/전술 자문')}</p>
        </div>
        """)
        with gr.Tabs():
          with gr.Tab("🎖️ AI 에이전트"):
            with gr.Row():
              with gr.Column(scale=1):
                gr.Markdown("### 영상 분석")
                gr.Markdown("##### 직접 업로드")
                video_upload = gr.File(label="군사 영상 업로드 (mp4, avi, mov)", file_types=[".mp4", ".avi", ".mov", ".mkv"])
                collection_input = gr.Textbox(label="콜렉션명", value="default", placeholder="콜렉션 이름 입력")
                analyze_btn = gr.Button("영상 분석 시작", variant="primary")
                gr.Markdown("##### 예시 영상")
                sample_dropdown = gr.Dropdown(label="예시 영상 선택", choices=_get_sample_video_choices(), value=None, interactive=True)
                with gr.Row():
                    sample_refresh_btn = gr.Button("목록 새로고침", scale=1)
                    sample_analyze_btn = gr.Button("예시 영상 분석", variant="primary", scale=2)
                analysis_status = gr.Textbox(label="분석 상태", lines=6, interactive=False)
                gr.Markdown("### 분석된 영상 목록")
                video_list = gr.CheckboxGroup(label="쿼리할 영상 선택", choices=[], value=[])
                video_select_status = gr.Textbox(label="선택 상태", value="선택된 비디오 없음", interactive=False)
                gr.Markdown("### 상황 분석 메모리")
                memory_status_btn = gr.Button("메모리 상태 확인")
                memory_status_box = gr.Textbox(label="EXAONE4 상황 분석 메모리", lines=5, interactive=False)
              with gr.Column(scale=2):
                gr.Markdown("### AI 에이전트 채팅")
                gr.Markdown("영상 분석 및 전략/전술 관련 질문을 입력하세요. 전략/전술 쿼리는 자동으로 **EXAONE Deep** 모델이 추가 분석합니다.")
                chatbot = gr.Chatbot(label="대화", height=500, show_copy_button=True)
                with gr.Row():
                    query_input = gr.Textbox(label="쿼리 입력", placeholder="예: '영상에서 탐지된 적 기갑부대를 분석해줘' 또는 '현재 상황에서 방어 전술을 추천해줘'", lines=2, scale=5)
                    send_btn = gr.Button("전송", variant="primary", scale=1)
                with gr.Row():
                    clear_btn = gr.Button("대화 초기화", variant="secondary")
                    clear_status = gr.Textbox(label="", value="", interactive=False, scale=3)
                gr.Markdown("### 예시 쿼리")
                example_queries = ui_cfg.get("examples", ["영상에서 탐지된 적 전력을 분석해줘", "현재 전장 상황에 대한 전략적 대응 방안을 추천해줘", "적 기갑부대에 대한 전술적 대응 방안을 제안해줘", "아군 방어 진지 구축을 위한 전략을 수립해줘"])
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
            gr.Markdown("ARMA3에서 수신된 실시간 전장 데이터를 지도에 표시합니다. relay.py 실행 중일 때 10초마다 자동 갱신됩니다.")
            with gr.Row():
                with gr.Column(scale=3):
                    map_plot = gr.Plot(label="전장 상황도", show_label=False)
                with gr.Column(scale=1):
                    gr.Markdown("### 병력 현황")
                    map_status = gr.Textbox(label="", lines=6, interactive=False, elem_id="map_status")
                    map_refresh_btn = gr.Button("🔄 새로고침", variant="primary")
                    gr.Markdown("**마커 범례**\n- 🔵 원 = BLUFOR 보병\n- 🔵 사각 = BLUFOR APC/차량\n- 🔵 다이아 = BLUFOR 장갑\n- 🔴 원 = OPFOR 보병\n- 🔴 사각 = OPFOR APC/차량\n- 🔴 다이아 = OPFOR 장갑\n- 큰 다이아 = 그룹 지휘관 위치\n\n**좌표계**\nx = 동쪽(m), y = 북쪽(m)\nAltis 맵 기준 (0 ~ 30,000m)")
            map_timer = gr.Timer(value=10)
        analyze_btn.click(fn=analyze_video, inputs=[video_upload, collection_input], outputs=[analysis_status, video_list])
        sample_analyze_btn.click(fn=analyze_sample_video, inputs=[sample_dropdown, collection_input], outputs=[analysis_status, video_list])
        sample_refresh_btn.click(fn=lambda: gr.update(choices=_get_sample_video_choices()), outputs=[sample_dropdown])
        video_list.change(fn=update_active_videos, inputs=[video_list], outputs=[video_select_status])
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
