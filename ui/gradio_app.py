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
    return "▶ 시뮬레이션 시작", fig, status, log_text


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
        return history, "", fig, status, log_text
    try:
        from tools.wargame_recon_tool import assess_recon_need, recommend_recon_routes
        from tools.wargame_mission_tool import apply_wargame_mission_plan
    except ImportError as e:
        history.append(("🔍 정찰 임무계획 요청", f"정찰 도구 로드 실패: {e}"))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text
    assessment = assess_recon_need()
    opfor_sum = assessment.get("opfor_summary", {})
    if assessment.get("recommendation") == "공격 즉시 가능":
        msg = (f"**✅ 모든 OPFOR 위치가 이미 탐지되어 정찰이 불필요합니다.**\n\n탐지된 적군: {opfor_sum.get('detected', 0)}개\n\n→ **⚔️ 공격 임무계획** 버튼을 사용하여 공격을 시작하세요.")
        history.append(("🔍 정찰 임무계획 요청", msg))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text
    if assessment.get("recommendation") == "적 없음":
        history.append(("🔍 정찰 임무계획 요청", "탐지된 적군이 없습니다."))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text
    agent = _get_agent()
    agent_label = "BattlefieldAgent" if agent else "규칙 기반"
    state = eng.get_state()
    recon_units_info = "\n".join(f"  - {u['id']} ({u['unit_type']}): 위치=({u['x']/1000:.1f}km, {u['y']/1000:.1f}km) CP={u['combat_power']:.0f}%" for u in state["units"] if u["side"] == "BLUFOR" and u.get("unit_type") == "정찰" and u["status"] == "active") or "  없음"
    undetected_info = "\n".join(f"  - {t['unit_id']}: {t['status']} 추정위치=({t['known_x_km']}km, {t['known_y_km']}km)" for t in assessment.get("undetected_targets", [])) or "  없음"
    recon_query = (f"[정찰 임무계획 수립]\n\n현재 상황: {assessment.get('reason', '')}\n\n미탐지 OPFOR 목표:\n{undetected_info}\n\n사용 가능한 정찰부대 (unit_type='정찰'):\n{recon_units_info}\n\n반드시 다음 6단계 순서대로 수행하세요:\n1. assess_recon_need() 호출\n2. recommend_recon_routes() 호출\n3. recon_advisor_tool(recon_routes_json=apply_json, recon_summary=summary) 호출\n4. 초기 경로와 EXAONE Deep 콘실트를 종합하여 EXAONE4가 최종 JSON 직접 생성\n5. 응답에 최종 정찰 임무계획 JSON 블록 반드시 출력\n6. apply_wargame_mission_plan(final_json) 호출\n\n[CRITICAL] unit_type이 '정찰'인 부대에만 임무를 부여하세요. 공격부대(Alpha, Bravo, Charlie, Echo)는 포함하지 마세요.")
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
        json_blocks = _re.findall(r"```json\\s*(.*?)\\s*```", agent_response_text, _re.DOTALL)
        for block in reversed(json_blocks):
            try:
                parsed = _json.loads(block)
                if "mission_plans" in parsed:
                    applied_plan = parsed
                    break
            except _json.JSONDecodeError:
                pass
        if applied_plan is None:
            logger.warning("Agent response has no parseable mission plan JSON; applying via fallback")
            recon_result = recommend_recon_routes()
            if recon_result.get("status") == "success":
                apply_wargame_mission_plan(recon_result["apply_json"])
                applied_plan = {"mission_plans": [{k: v for k, v in p.items() if k != "target_unit_id"} for p in recon_result["mission_plans"]]}
            elif recon_result.get("status") == "no_recon_units":
                msg = f"**⚠️ 사용 가능한 정찰부대(unit_type=정찰)가 없습니다.**\n\n{assessment.get('reason', '')}\n\n→ **⚔️ 공격 임무계획** 버튼을 사용하거나 채팅창에서 전술 조언을 요청하세요."
                history[-1] = (history[-1][0], msg)
                fig, status, log_text = wargame_refresh()
                return history, "", fig, status, log_text
    else:
        agent_response_text = "에이전트 미초기화 — 규칙 기반으로 정찰 경로를 생성합니다."
        recon_result = recommend_recon_routes()
        if recon_result.get("status") == "no_recon_units":
            msg = f"**⚠️ 사용 가능한 정찰부대가 없습니다.**\n\n{assessment.get('reason', '')}"
            history[-1] = (history[-1][0], msg)
            fig, status, log_text = wargame_refresh()
            return history, "", fig, status, log_text
        if recon_result.get("status") == "success":
            apply_wargame_mission_plan(recon_result["apply_json"])
            applied_plan = {"mission_plans": [{k: v for k, v in p.items() if k != "target_unit_id"} for p in recon_result["mission_plans"]]}
    if applied_plan is None:
        history[-1] = (history[-1][0], "정찰 임무계획 생성 실패: 적용 가능한 계획이 없습니다.")
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text
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
    fig, status, log_text = wargame_refresh()
    return history, plan_text, fig, status, log_text


def wargame_request_attack_plan(history: List = None):
    global _wg_last_plan
    history = list(history or [])
    eng = _wg_ensure_engine()
    if eng is None:
        history.append(("⚔️ 공격 임무계획 요청", "워게임 초기화 실패"))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text
    if _wg_planner is None:
        history.append(("⚔️ 공격 임무계획 요청", "Planner 없음"))
        fig, status, log_text = wargame_refresh()
        return history, "", fig, status, log_text
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
    base_query = build_mission_query(state)
    attack_suffix = ("\n\n[공격 임무계획 지시]\n- 탐지 상태가 'detected'인 OPFOR만 공격 목표로 설정하라.\n- 정찰부대(unit_type=정찰, Delta)는 측방 경계 또는 탐지 미확인 방향 감시 임무를 부여하라.\n- 기계화보병·전차·대전차 부대에게 탐지된 적 격멸 임무를 부여하라.\n- 자주포가 있으면 탐지된 적 위치에 포격 지원 임무를 부여하라.\n- 미탐지 적군 방향으로 공격부대를 돌출시키지 마라.\n\n[절대 금지] assess_recon_need(), recommend_recon_routes(), recon_advisor_tool(), strategy_advisor_tool() 등 정찰·전략 도구는 절대 호출하지 마라.\n위 JSON 형식으로만 즉시 응답하라.")
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
                    plan = _wg_planner._rule_based(state)
            except Exception:
                plan = _wg_planner._rule_based(state)
        else:
            plan = _wg_planner._rule_based(state)
    _wg_last_plan = plan
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
    fig, status, log_text = wargame_refresh()
    return history, plan_text, fig, status, log_text


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
                        gr.Markdown("### 부대 전력 현황")
                        wg_status = gr.Textbox(label="", lines=5, interactive=False, elem_id="wg_status")
                    with gr.Column(scale=2):
                        wg_plan_box = gr.Code(language="json", lines=8, interactive=False, label="LLM 생성 임무계획 (JSON)")
                    with gr.Column(scale=2):
                        wg_event_log = gr.Textbox(label="전투 이벤트 로그", lines=8, interactive=False)
                wg_timer = gr.Timer(value=2)
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
            wg_reset_btn.click(fn=wargame_reset_sim, outputs=[wg_startstop_btn, wg_map, wg_status, wg_event_log])
            wg_apply_scale_btn.click(fn=wargame_set_timescale, inputs=[wg_timescale], outputs=_WG_OUTPUTS)
            wg_recon_btn.click(fn=wargame_request_recon_plan, inputs=[wg_chatbot], outputs=[wg_chatbot, wg_plan_box, wg_map, wg_status, wg_event_log])
            wg_attack_btn.click(fn=wargame_request_attack_plan, inputs=[wg_chatbot], outputs=[wg_chatbot, wg_plan_box, wg_map, wg_status, wg_event_log])
            wg_chat_send_btn.click(fn=wg_chat_send, inputs=[wg_chat_input, wg_chatbot], outputs=[wg_chatbot, wg_chat_input])
            wg_chat_input.submit(fn=wg_chat_send, inputs=[wg_chat_input, wg_chatbot], outputs=[wg_chatbot, wg_chat_input])
            wg_chat_clear_btn.click(fn=lambda: ([], ""), outputs=[wg_chatbot, wg_chat_input])
            wg_timer.tick(fn=wargame_refresh_with_alert, inputs=[wg_chatbot], outputs=[wg_map, wg_status, wg_event_log, wg_chatbot])
            app.load(fn=wargame_refresh, outputs=_WG_OUTPUTS)
        map_refresh_btn.click(fn=get_battlefield_map, outputs=[map_plot, map_status])
        map_timer.tick(fn=get_battlefield_map, outputs=[map_plot, map_status])
        app.load(fn=get_battlefield_map, outputs=[map_plot, map_status])
    return app


def launch_app(agent=None, **kwargs):
    ui_cfg = _load_ui_config()
    app = create_app(agent=agent)
    app.launch(server_name=kwargs.get("server_name", ui_cfg.get("server_name", "0.0.0.0")), server_port=kwargs.get("server_port", ui_cfg.get("server_port", 7860)), share=kwargs.get("share", ui_cfg.get("share", False)))
