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
    from wargame import WargameEngine, setup_bn_vs_bn
    from wargame.llm_planner import MissionPlanner
    from wargame.terrain import get_heightmap, GRID_W, GRID_H, MAP_W, MAP_H
    _WARGAME_OK = True
except Exception as _wg_err:
    _WARGAME_OK = False
    _wg_err = str(_wg_err)

logger = logging.getLogger(__name__)

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
    return _wg_engine


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
                if wps:
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
            lines.append(f"  ?{e['unit_id']:6s}(미확인) {'?'*10}  ?????  [{det_ko}] ({e['known_x']/1000:.1f}km,{e['known_y']/1000:.1f}km 추정)")
        else:
            utype = e.get("unit_type") or "미확인"
            lines.append(f"  ({e['unit_id']:6s})({utype:6s}) {'░'*10}  ?????  [{det_ko}] 최종({e['known_x']/1000:.1f}km,{e['known_y']/1000:.1f}km)")
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
        lines.append(f"  • {u['id']}({get_unit_type(u['id'])}): ({u['x']/1000:.1f}km,{u['y']/1000:.1f}km) CP={u['combat_power']:.0f}% [{action_ko}]")
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
        lines.append(f"  {u['side']} {u['id']}({get_unit_type(u['id'])}): ({u['x']/1000:.1f}km,{u['y']/1000:.1f}km) {s} 행동={u['current_action']}")
    lines.append("\n현재 전장 상황을 분석하고, OPFOR의 의도와 BLUFOR 즉각 대응 방안을 3가지 간결하게 제시해줘.")
    return "\n".join(lines)


def wargame_refresh():
    eng = _wg_ensure_engine()
    if eng is None:
        msg = f"워게임 모듈 로드 실패: {_wg_err if not _WARGAME_OK else '엔진 없음'}"
        return None, msg, ""
    state = eng.get_state()
    fig = _build_wargame_map(state)
    status = _wg_status_text(state)
    events = eng.db.get_recent_events(20)
    log_text = "\n".join(f"[{e['event_type']:10s}] T={e['tick']:4d} {e['message']}" for e in events)
    return fig, status, log_text


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
    fig, status, log_text = wargame_refresh()
    return label, fig, status, log_text


def wargame_reset_sim():
    global _wg_engine, _wg_last_plan
    if not _WARGAME_OK:
        return "초기화 실패", None, "", ""
    units = setup_bn_vs_bn()
    if _wg_engine is not None:
        _wg_engine.reset(units)
    else:
        _wg_engine = WargameEngine(units)
        _wg_register_engine(_wg_engine)
    _wg_last_plan = {}
    fig, status, log_text = wargame_refresh()
    return "▶ 시뮬레이션 시작", fig, status, log_text, ""


def wargame_set_timescale(scale: float):
    eng = _wg_ensure_engine()
    if eng:
        eng.time_scale = float(scale)
    return wargame_refresh()


def wargame_request_recon_plan(history: List = None):
    global _wg_last_plan
    history = list(history or [])
    eng = _wg_ensure_engine()
    if eng is None:
        history.append(("🔍 정찰 임무계획 요청", "워게임 초기화 실패"))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text, ""
    try:
        from tools.wargame_recon_tool import assess_recon_need, recommend_recon_routes
    except ImportError as e:
        history.append(("🔍 정찰 임무계획 요청", f"정찰 도구 로드 실패: {e}"))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text, ""
    assessment = assess_recon_need()
    opfor_sum = assessment.get("opfor_summary", {})
    if assessment.get("recommendation") == "공격 즉시 가능":
        msg = (f"**✅ 모든 OPFOR 위치가 이미 탐지되어 정찰이 불필요합니다.**\n\n탐지된 적군: {opfor_sum.get('detected', 0)}개\n\n→ **⚔️ 공격 임무계획** 버튼을 사용하여 공격을 시작하세요.")
        history.append(("🔍 정찰 임무계획 요청", msg))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text, ""
    if assessment.get("recommendation") == "적 없음":
        history.append(("🔍 정찰 임무계획 요청", "탐지된 적군이 없습니다."))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text, ""
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
        f"현재 전장 상황(부대 위치·전투력·인텔 등)은 반드시 도구(tool)를 호출하여 조회하라.\n"
        f"assess_recon_need 및 recommend_recon_routes 도구를 사용하여 정찰 임무를 계획하라.\n\n"
        f"[RECON 규칙]\n{recon_rules}\n\n"
        f"[EXECUTION 규칙]\n{execution_rules}"
        f"{learned_suffix}"
    )
    logger.debug("recon_query:\n%s", recon_query)
    history.append((f"🔍 **정찰 임무계획 생성 요청** ({agent_label})", "처리 중..."))
    import json as _json, re as _re
    agent_response_text = ""
    applied_plan = None
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
                    applied_plan = parsed  # 표시용 — 적용은 agent가 tool로 처리
                    break
            except _json.JSONDecodeError:
                pass
        if applied_plan is None:
            logger.warning("Agent response has no parseable mission plan JSON; applying via fallback")
            recon_result = recommend_recon_routes()
            if recon_result.get("status") == "success":
                plan_dict = _json.loads(recon_result["apply_json"]) if isinstance(recon_result["apply_json"], str) else recon_result["apply_json"]
                eng.apply_mission_plan(plan_dict)
                applied_plan = {"mission_plans": [{k: v for k, v in p.items() if k != "target_unit_id"} for p in recon_result["mission_plans"]]}
            elif recon_result.get("status") == "no_recon_units":
                msg = f"**⚠️ 사용 가능한 정찰부대(unit_type=정찰)가 없습니다.**\n\n{assessment.get('reason', '')}\n\n→ **⚔️ 공격 임무계획** 버튼을 사용하거나 채팅창에서 전술 조언을 요청하세요."
                history[-1] = (history[-1][0], msg)
                fig, status, log_text = wargame_refresh()
                return history, "", fig, status, log_text, ""
    else:
        agent_response_text = "에이전트 미초기화 — 규칙 기반으로 정찰 경로를 생성합니다."
        recon_result = recommend_recon_routes()
        if recon_result.get("status") == "no_recon_units":
            msg = f"**⚠️ 사용 가능한 정찰부대가 없습니다.**\n\n{assessment.get('reason', '')}"
            history[-1] = (history[-1][0], msg)
            fig, status, log_text = wargame_refresh()
            return history, "", fig, status, log_text, ""
        if recon_result.get("status") == "success":
            plan_dict = _json.loads(recon_result["apply_json"]) if isinstance(recon_result["apply_json"], str) else recon_result["apply_json"]
            eng.apply_mission_plan(plan_dict)
            applied_plan = {"mission_plans": [{k: v for k, v in p.items() if k != "target_unit_id"} for p in recon_result["mission_plans"]]}
    if applied_plan is None:
        history[-1] = (history[-1][0], "정찰 임무계획 생성 실패: 적용 가능한 계획이 없습니다.")
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text, ""
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
    unit_summary = "\n".join(f"• **{p['company_id']}** → {p.get('objective', '')}" for p in plans)
    alert_md = (
        f"---\n"
        f"### 🔔 승인 요구 알람 — 정찰 임무계획 적용됨\n\n"
        f"| 항목 | 내용 |\n|---|---|\n"
        f"| 계획 ID | `{applied_plan.get('plan_id', 'N/A')}` |\n"
        f"| 파견 부대 수 | {len(plans)}개 |\n"
        f"| 승인 방식 | 버튼 클릭 = 사용자 승인 ✅ |\n\n"
        f"**파견 부대:**\n{unit_summary}\n\n"
        f"> ℹ️ 임무계획이 워게임 엔진에 즉시 적용되었습니다. 초기화하려면 **⏹ 초기화** 버튼을 사용하세요.\n\n---"
    )
    fig, status, log_text = wargame_refresh()
    return history, plan_text, fig, status, log_text, alert_md


def wargame_request_attack_plan(history: List = None):
    global _wg_last_plan
    history = list(history or [])
    eng = _wg_ensure_engine()
    if eng is None:
        history.append(("⚔️ 공격 임무계획 요청", "워게임 초기화 실패"))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text, ""
    if _wg_planner is None:
        history.append(("⚔️ 공격 임무계획 요청", "Planner 없음"))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text, ""
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
    base_query = build_mission_query(state)
    attack_suffix = (
        f"\n\n[ATTACK 규칙]\n{attack_rules}\n\n"
        f"[EXECUTION 규칙]\n{execution_rules_atk}"
        f"{learned_suffix_atk}"
    )
    full_query = base_query + attack_suffix
    header_msg = f"⚔️ **공격 임무계획 생성 요청** ({agent_label}){warning_msg}"
    history.append((header_msg, "처리 중..."))
    plan = _wg_planner.plan(state, agent=agent) if agent is None else None
    if plan is None:
        if agent is not None:
            try:
                raw = agent.agent.run(full_query, reset=False)
                plan = _wg_planner._parse_json(str(raw))
                if not (plan and "mission_plans" in plan):
                    # agent가 JSON 파싱 실패 → rule-based fallback 적용
                    plan = _wg_planner._rule_based(state)
                    eng.apply_mission_plan(plan)
                    if plan.get("air_support_plans"):
                        eng.apply_air_support_plan(plan)
                # agent가 JSON 생성 성공 → tool이 이미 엔진에 적용함
            except Exception:
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
    mission_summary = "\n".join(
        f"• **{p['company_id']}** → {p.get('mission_type', '')} / {p.get('objective', '')}"
        for p in plan.get("mission_plans", [])
    )
    air_summary = (
        f"\n**공중지원 {n_air}건 등록됨**\n" +
        "\n".join(f"• {a.get('call_sign', '?')} ({a.get('support_type', '?')})" for a in plan.get("air_support_plans", []))
        if n_air else ""
    )
    alert_md = (
        f"---\n"
        f"### 🔔 승인 요구 알람 — 공격 임무계획 적용됨\n\n"
        f"| 항목 | 내용 |\n|---|---|\n"
        f"| 지상 임무 | {n_plans}개 중대 |\n"
        f"| 공중지원 | {n_air}건 |\n"
        f"| 승인 방식 | 버튼 클릭 = 사용자 승인 ✅ |\n\n"
        f"**임무 배분:**\n{mission_summary}{air_summary}\n\n"
        f"> ℹ️ 임무계획이 워게임 엔진에 즉시 적용되었습니다. 초기화하려면 **⏹ 초기화** 버튼을 사용하세요.\n\n---"
    )
    fig, status, log_text = wargame_refresh()
    return history, plan_text, fig, status, log_text, alert_md


def wg_chat_send(message: str, history: List) -> Tuple[List, str]:
    if not message.strip():
        return history, ""
    history = list(history)
    agent = _get_agent()
    eng = _wg_ensure_engine()
    context = ""
    if eng is not None:
        state = eng.get_state()
        context = (f"[현재 워게임 상황] 게임시간={state['game_time_str']}\n" + "\n".join(f"  {u['side']} {u['id']}: CP={u['combat_power']:.0f}% 위치=({u['x']/1000:.1f}km,{u['y']/1000:.1f}km) {u['status']}" for u in state["units"]) + "\n\n")
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
    return history, ""


def wargame_evaluate_and_learn(history: List) -> Tuple[List, str]:
    """워게임 현재 상태를 평가하고 학습된 규칙을 agent_custom_instructions.txt에 추가합니다."""
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
    winner = state.get("winner")
    last_plan = _wg_last_plan or {}
    summary_lines = [
        f"[워게임 평가 요청]",
        f"게임시간: {state['game_time_str']} | 승자: {winner or '미결'}",
        f"BLUFOR 생존: {len(bf_alive)}/{len(blufor)} | OPFOR 생존: {len(op_alive)}/{len(opfor)}",
        f"마지막 임무계획 부대 수: {len(last_plan.get('mission_plans', []))}",
        "",
        "위 워게임 결과를 분석하여 다음을 수행하세요:",
        "1. 이번 전투에서 효과적이었던 전술 패턴 1~3가지를 간결한 규칙 문장으로 작성",
        "2. 개선이 필요한 전술 패턴 1~2가지를 규칙 문장으로 작성",
        "3. 각 규칙을 '- <규칙 내용>' 형식으로 줄별로 출력 (JSON 불필요)",
        "4. 규칙은 다음 워게임에서 바로 적용 가능한 구체적 내용으로 작성",
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
    learned_count = 0
    for line in response_text.splitlines():
        line = line.strip()
        if line.startswith("- ") and len(line) > 5:
            rule_text = line[2:].strip()
            if rule_text:
                append_learned_rule(rule_text)
                learned_count += 1
    result_msg = (
        f"**🧠 전술 평가 완료** — {learned_count}개 규칙이 `agent_custom_instructions.txt`에 추가됨\n\n"
        f"{response_text}"
    )
    history.append(("🧠 전술 평가 & 규칙 학습", result_msg))
    return history, ""


def wargame_refresh_with_alert(chatbot_history: List) -> tuple:
    global _wg_last_opfor_ai_count
    fig, status, log_text = wargame_refresh()
    chatbot_history = list(chatbot_history or [])
    eng = _wg_ensure_engine()
    if eng is not None:
        state = eng.get_state()
        current_count = state.get("opfor_ai_fire_count", 0)
        if current_count > _wg_last_opfor_ai_count:
            _wg_last_opfor_ai_count = current_count
            alert_msg = _build_opfor_alert(state)
            chatbot_history.append(("⚠️ 시스템 알람", alert_msg))
    return fig, status, log_text, chatbot_history


def clear_chat_history() -> Tuple[List, str]:
    global _last_situation_analysis
    _last_situation_analysis = ""
    try:
        from tools.strategy_advisor_tool import clear_situation_memory
        clear_situation_memory()
    except Exception:
        pass
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
        from wargame import WargameEngine, setup_bn_vs_bn
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


def create_app(agent=None) -> gr.Blocks:
    global _agent
    _agent = agent
    ui_cfg = _load_ui_config()
    with gr.Blocks(title=ui_cfg.get("title", "C2 군사 전략 AI"), theme=gr.themes.Base(primary_hue="slate", secondary_hue="gray")) as app:
        gr.Markdown(f"""
# {ui_cfg.get('title', 'C2 군사 전략 AI - EXAONE4 + EXAONE Deep')}
{ui_cfg.get('description', '')}

**듀얼 모델 아키텍처:**
- **EXAONE4**: 영상 분석, 상황 판단, 최종 응답 생성
- **EXAONE Deep**: 전략/전술 전문 추천 (EXAONE4의 상황 분석을 바탕으로 호출됨)
        """)
        with gr.Tabs():
          with gr.Tab("🎖️ AI 에이전트"):
            with gr.Row():
              with gr.Column(scale=1):
                gr.Markdown("## 영상 분석")
                gr.Markdown("#### 직접 업로드")
                video_upload = gr.File(label="군사 영상 업로드 (mp4, avi, mov)", file_types=[".mp4", ".avi", ".mov", ".mkv"])
                collection_input = gr.Textbox(label="콜렉션명", value="default", placeholder="콜렉션 이름 입력")
                analyze_btn = gr.Button("영상 분석 시작", variant="primary")
                gr.Markdown("#### 예시 영상")
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
                gr.Markdown("## AI 에이전트 채팅")
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
                gr.Markdown("## 파이썬 워게임 시뮬레이터\nLLM이 JSON 임무계획을 생성하면 각 중대가 자동으로 기동·교전합니다.")
                with gr.Row():
                    with gr.Column(scale=3):
                        wg_map = gr.Plot(label="전장 지도", show_label=False)
                    with gr.Column(scale=2):
                        wg_alert_md = gr.Markdown("", visible=True)
                        gr.Markdown("### 전술 AI 채팅")
                        wg_chatbot = gr.Chatbot(label="", height=220, show_copy_button=True, bubble_full_width=False)
                        with gr.Row():
                            wg_chat_input = gr.Textbox(label="", placeholder="워게임 상황 분석, 전술 조언, 임무계획 수정 등 질문하세요...", lines=2, scale=5)
                            wg_chat_send_btn = gr.Button("전송", variant="primary", scale=1)
                        wg_chat_clear_btn = gr.Button("대화 초기화", variant="secondary", size="sm")
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 시뮬레이션 제어")
                        wg_startstop_btn = gr.Button("▶ 시뮬레이션 시작", variant="primary")
                        wg_reset_btn = gr.Button("⏹ 초기화", variant="secondary")
                        wg_timescale = gr.Slider(minimum=0.1, maximum=600, value=60, step=0.1, label="시간 배율 (실제 1초 = X 게임 초)")
                        wg_apply_scale_btn = gr.Button("배율 적용", size="sm")
                        gr.Markdown("### 임무계획")
                        wg_recon_btn = gr.Button("🔍 정찰 임무계획", variant="secondary")
                        wg_attack_btn = gr.Button("⚔️ 공격 임무계획", variant="primary")
                        wg_eval_btn = gr.Button("🧠 전술 평가 & 규칙 학습", variant="secondary", size="sm")
                        gr.Markdown("### 부대 전력 현황")
                        wg_status = gr.Textbox(label="", lines=5, interactive=False, elem_id="wg_status")
                    with gr.Column(scale=2):
                        wg_plan_box = gr.Textbox(label="LLM 생성 임무계획 (JSON)", lines=8, interactive=False, value="")
                    with gr.Column(scale=2):
                        wg_event_log = gr.Textbox(label="전투 이벤트 로그", lines=8, interactive=False)
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
            _WG_OUTPUTS = [wg_map, wg_status, wg_event_log]
            wg_startstop_btn.click(fn=wargame_start_pause, outputs=[wg_startstop_btn, wg_map, wg_status, wg_event_log])
            wg_reset_btn.click(fn=wargame_reset_sim, outputs=[wg_startstop_btn, wg_map, wg_status, wg_event_log, wg_alert_md])
            wg_apply_scale_btn.click(fn=wargame_set_timescale, inputs=[wg_timescale], outputs=_WG_OUTPUTS)
            wg_recon_btn.click(fn=wargame_request_recon_plan, inputs=[wg_chatbot], outputs=[wg_chatbot, wg_plan_box, wg_map, wg_status, wg_event_log, wg_alert_md])
            wg_attack_btn.click(fn=wargame_request_attack_plan, inputs=[wg_chatbot], outputs=[wg_chatbot, wg_plan_box, wg_map, wg_status, wg_event_log, wg_alert_md])
            wg_eval_btn.click(fn=wargame_evaluate_and_learn, inputs=[wg_chatbot], outputs=[wg_chatbot, wg_chat_input])
            wg_chat_send_btn.click(fn=wg_chat_send, inputs=[wg_chat_input, wg_chatbot], outputs=[wg_chatbot, wg_chat_input])
            wg_chat_input.submit(fn=wg_chat_send, inputs=[wg_chat_input, wg_chatbot], outputs=[wg_chatbot, wg_chat_input])
            wg_chat_clear_btn.click(fn=lambda: ([], ""), outputs=[wg_chatbot, wg_chat_input])
            wg_timer.tick(fn=wargame_refresh_with_alert, inputs=[wg_chatbot], outputs=[wg_map, wg_status, wg_event_log, wg_chatbot])
            app.load(fn=wargame_refresh, outputs=_WG_OUTPUTS)
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
        map_refresh_btn.click(fn=get_battlefield_map, outputs=[map_plot, map_status])
        map_timer.tick(fn=get_battlefield_map, outputs=[map_plot, map_status])
        app.load(fn=get_battlefield_map, outputs=[map_plot, map_status])
    return app


def launch_app(agent=None, **kwargs):
    ui_cfg = _load_ui_config()
    app = create_app(agent=agent)
    app.launch(server_name=kwargs.get("server_name", ui_cfg.get("server_name", "0.0.0.0")), server_port=kwargs.get("server_port", ui_cfg.get("server_port", 7860)), share=kwargs.get("share", ui_cfg.get("share", False)))
