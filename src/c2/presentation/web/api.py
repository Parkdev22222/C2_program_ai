"""
C2 군사 AI — FastAPI 웹 API 백엔드 (Task 30)
HTML 기반 대시보드를 위한 REST API 서버.

과거 `ui/web_api.py`는 모든 엔진 접근을 `ui.gradio_app`의 전역 lazy-import 헬퍼에
위임했다. 이 모듈은 그 위임을 제거하고, `c2.composition.container.build_session()`
으로 완전히 wiring된 `WargameSession`(`c2.application.simulation.session`)을
직접 사용한다 — gradio_app을 import하지 않는다.
"""
from __future__ import annotations

import logging
import threading
import traceback
import uuid
from pathlib import Path
from c2._paths import repo_root
from typing import Any, Dict, List, Optional

# FastAPI 의존성
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    import uvicorn
    _FASTAPI_OK = True
except ImportError as _fe:
    _FASTAPI_OK = False
    _fe_msg = str(_fe)

logger = logging.getLogger(__name__)

# ── 좌표 변환 유틸 (c2.domain — presentation은 domain을 자유롭게 import 가능) ──
try:
    from c2.domain.wargame.coordinates import xy_to_latlon as _xy_to_latlon
except Exception:
    import math as _math
    _REF_LAT = 38.0
    _REF_LON = 127.0
    _MPDLAT = 111000.0
    _MPDLON = 111000.0 * _math.cos(_math.radians(_REF_LAT))

    def _xy_to_latlon(x: float, y: float):
        return (
            round(_REF_LAT + y / _MPDLAT, 6),
            round(_REF_LON + x / _MPDLON, 6),
        )


# ── 백그라운드 잡 시스템 ────────────────────────────────────────────────────
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _new_job() -> str:
    jid = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[jid] = {"status": "pending", "result": {}, "log": []}
    return jid


def _job_log(jid: str, msg: str):
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid]["log"].append(msg)
            logger.debug("[job %s] %s", jid, msg)


def _job_set(jid: str, status: str, result: dict = None):
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid]["status"] = status
            if result is not None:
                _jobs[jid]["result"] = result


def get_job_status(jid: str) -> Optional[Dict]:
    with _jobs_lock:
        return dict(_jobs[jid]) if jid in _jobs else None


# ── 세션 획득 (컨테이너 경유, gradio 비의존) ─────────────────────────────────
_session: Any = None
_agent: Any = None


def _get_agent() -> Any:
    return _agent


def set_agent(agent: Any) -> None:
    """앱 기동 전(또는 세션 생성 전)에 에이전트를 주입한다."""
    global _agent
    _agent = agent


def _get_session():
    """`c2.composition.container.build_session()`으로 wiring된 세션을 반환 (lazy, 1회)."""
    global _session
    if _session is None:
        from c2.composition.container import build_session

        _session = build_session(agent=_get_agent())
    return _session


def _get_engine():
    """엔진을 확보하고, 최초 생성 시 세션 탐지 워커를 1회 기동한다."""
    try:
        session = _get_session()
        is_new = session.engine is None
        engine = session.ensure_engine()
        if is_new and engine is not None:
            session.start_detection_worker()
        return engine
    except Exception as e:
        logger.warning("엔진 확보 실패: %s", e)
        return None


def _convert_state_to_api(state: dict) -> dict:
    """
    엔진 state에서 미터 좌표를 위경도로 변환하여 API 응답용 dict 반환.
    """
    result = {
        "running": state.get("running", False),
        "game_time_str": state.get("game_time_str", "00:00:00"),
        "tick": state.get("tick", 0),
        "winner": state.get("winner"),
        "air_use_count": state.get("air_use_count", {}),
        "air_use_limit": state.get("air_use_limit", 5),
        "air_reset_at": state.get("air_reset_at", 0),
        "units": [],
        "intelligence": {"BLUFOR": []},
        "air_supports": [],
        "control_points": [],
    }

    # 유닛 좌표 변환
    for u in state.get("units", []):
        lat, lon = _xy_to_latlon(u.get("x", 0), u.get("y", 0))
        waypoints_ll = []
        for wp in u.get("waypoints", []):
            if isinstance(wp, (list, tuple)) and len(wp) == 2:
                wlat, wlon = _xy_to_latlon(wp[0], wp[1])
                waypoints_ll.append([wlat, wlon])
            elif isinstance(wp, dict):
                wlat, wlon = _xy_to_latlon(wp.get("x", 0), wp.get("y", 0))
                waypoints_ll.append([wlat, wlon])
        result["units"].append({
            "id": u.get("id", "?"),
            "side": u.get("side", ""),
            "unit_type": u.get("unit_type", ""),
            "status": u.get("status", "active"),
            "combat_power": u.get("combat_power", 100.0),
            "current_action": u.get("current_action", "hold"),
            "color": u.get("color", "#888888"),
            "lat": lat,
            "lon": lon,
            "waypoints": waypoints_ll,
        })

    # 인텔 (BLUFOR 기준 OPFOR 탐지 정보)
    for e in state.get("intelligence", {}).get("BLUFOR", []):
        klat, klon = _xy_to_latlon(e.get("known_x", 0), e.get("known_y", 0))
        result["intelligence"]["BLUFOR"].append({
            "unit_id": e.get("unit_id", "?"),
            "status": e.get("status", "lost"),
            "unit_type": e.get("unit_type", ""),
            "combat_power": e.get("combat_power", 0.0),
            "lat": klat,
            "lon": klon,
        })

    # 공중지원
    for air in state.get("air_supports", []):
        tlat, tlon = _xy_to_latlon(air.get("target_x", 0), air.get("target_y", 0))
        result["air_supports"].append({
            "call_sign": air.get("call_sign", ""),
            "support_type": air.get("support_type", "cas"),
            "status": air.get("status", "pending"),
            "lat": tlat,
            "lon": tlon,
            "radius": air.get("radius", 1500),
        })

    # 통제구역 좌표 변환
    for cp in state.get("control_points", []):
        clat, clon = _xy_to_latlon(cp.get("x", 0), cp.get("y", 0))
        result["control_points"].append({**cp, "lat": clat, "lon": clon})

    return result


def _status_text(state: dict) -> str:
    """상태 dict → 사람이 읽는 텍스트 요약 (과거 gradio_app._wg_status_text 이식)."""
    try:
        from c2.application.simulation.scenario import get_unit_type
    except Exception:
        def get_unit_type(uid):
            return ""

    lines = [f"게임 시간: {state.get('game_time_str', '00:00:00')} | Tick: {state.get('tick', 0)}"]
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
            lines.append(
                f"  ?{e['unit_id']:6s}(미확인) {'?' * 10}  ?????  [{det_ko}] "
                f"(lat={approx_lat:.4f},lon={approx_lon:.4f} 추정)"
            )
        else:
            utype = e.get("unit_type") or "미확인"
            lost_lat, lost_lon = _xy_to_latlon(e["known_x"], e["known_y"])
            lines.append(
                f"  ({e['unit_id']:6s})({utype:6s}) {'░' * 10}  ?????  [{det_ko}] "
                f"최종(lat={lost_lat:.4f},lon={lost_lon:.4f})"
            )
    return "\n".join(lines)


# ── FastAPI 앱 팩토리 ────────────────────────────────────────────────────────
_DASHBOARD_DIR = repo_root() / "ui" / "dashboard"


# ── 요청 모델 ────────────────────────────────────────────────────────────────
if _FASTAPI_OK:

    class TimescaleRequest(BaseModel):
        scale: float

    class ChatRequest(BaseModel):
        message: str

    class ScenarioUnitDef(BaseModel):
        id: str
        unit_type: str
        x: Optional[float] = None
        y: Optional[float] = None

    class ScenarioControlPointDef(BaseModel):
        id: str
        x: float
        y: float

    class ScenarioApplyRequest(BaseModel):
        blufor: List[ScenarioUnitDef]
        opfor: List[ScenarioUnitDef]
        control_points: Optional[List[ScenarioControlPointDef]] = None

    class CoaExecuteRequest(BaseModel):
        index: int


_app_singleton: Any = None


def create_app(agent: Any = None) -> Any:
    """FastAPI 앱을 생성(1회)하고 반환한다. `agent`가 주어지면 세션 에이전트로 주입."""
    global _app_singleton
    if agent is not None:
        set_agent(agent)
    if not _FASTAPI_OK:
        return None
    if _app_singleton is not None:
        return _app_singleton

    app = FastAPI(title="C2 Military AI Web API", version="1.0.0")

    # ── 엔드포인트 ───────────────────────────────────────────────────────────

    @app.get("/")
    async def serve_index():
        idx = _DASHBOARD_DIR / "index.html"
        if not idx.exists():
            return JSONResponse({"error": "index.html not found"}, status_code=404)
        return FileResponse(
            str(idx),
            media_type="text/html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/api/state")
    async def api_state():
        eng = _get_engine()
        if eng is None:
            return JSONResponse(
                {"error": "워게임 엔진 초기화 실패"},
                status_code=503,
            )
        try:
            state = _get_session().get_state()
            return JSONResponse(_convert_state_to_api(state))
        except Exception as e:
            logger.exception("api_state 오류")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/events")
    async def api_events(n: int = 40):
        eng = _get_engine()
        if eng is None:
            return JSONResponse([])
        try:
            events = eng.db.get_recent_events(n)
            result = []
            for ev in events:
                result.append({
                    "tick": ev.get("tick", 0),
                    "event_type": ev.get("event_type", ""),
                    "message": ev.get("message", ""),
                })
            return JSONResponse(result)
        except Exception:
            logger.exception("api_events 오류")
            return JSONResponse([], status_code=500)

    @app.get("/api/status_text")
    async def api_status_text():
        eng = _get_engine()
        if eng is None:
            return JSONResponse({"text": "워게임 엔진 초기화 실패"})
        try:
            state = _get_session().get_state()
            text = _status_text(state)
            return JSONResponse({"text": text})
        except Exception as e:
            logger.exception("api_status_text 오류")
            return JSONResponse({"text": f"오류: {e}"})

    @app.post("/api/control/start")
    async def api_control_start():
        try:
            _get_engine()  # 세션/엔진/탐지워커 확보 보장
            result = _get_session().start_pause()
            return JSONResponse({"running": result["running"], "label": result["label"]})
        except Exception as e:
            logger.exception("api_control_start 오류")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/control/stop")
    async def api_control_stop():
        eng = _get_engine()
        if eng is None:
            return JSONResponse({"error": "엔진 없음"}, status_code=503)
        try:
            if eng.running:
                _get_session().stop()
            return JSONResponse({"running": eng.running})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/control/reset")
    async def api_control_reset():
        try:
            _get_session().reset()
            return JSONResponse({"ok": True, "label": "▶ 시뮬레이션 시작"})
        except Exception as e:
            logger.exception("api_control_reset 오류")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/control/timescale")
    async def api_control_timescale(req: "TimescaleRequest"):
        try:
            _get_session().set_timescale(req.scale)
            return JSONResponse({"ok": True, "scale": req.scale})
        except Exception as e:
            logger.exception("api_control_timescale 오류")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/chat")
    async def api_chat(req: "ChatRequest"):
        if not req.message.strip():
            return JSONResponse({"history": [], "response": ""})
        try:
            result = _get_session().chat_send(req.message, [])
            history = result.get("history", [])
            response = history[-1][1] if history else ""
            return JSONResponse({"response": response})
        except Exception as e:
            logger.exception("api_chat 오류")
            return JSONResponse({"error": str(e), "response": f"오류: {e}"}, status_code=500)

    # ── 백그라운드 미션 잡 ──────────────────────────────────────────────────

    def _run_recon_job(jid: str):
        _job_set(jid, "running")
        _job_log(jid, "정찰 임무계획 수립 시작...")
        try:
            result = _get_session().request_recon_plan(history=[])
            history = result.get("history", [])
            plan_text = result.get("plan_text", "")
            last_msg = history[-1][1] if history else ""
            _job_log(jid, "정찰 임무계획 완료")
            _job_set(jid, "done", {
                "plan_text": plan_text,
                "message": last_msg,
                "type": "recon",
            })
        except Exception as e:
            _job_log(jid, f"오류: {e}")
            _job_log(jid, traceback.format_exc())
            _job_set(jid, "error", {"error": str(e)})

    def _run_attack_job(jid: str):
        _job_set(jid, "running")
        _job_log(jid, "공격 COA 3개 생성 시작...")
        try:
            result = _get_session().generate_attack_coas()
            coas = result.get("coas", [])
            _job_log(jid, f"COA {len(coas)}개 생성 완료")
            _job_set(jid, "done", {
                "coas": coas,
                "message": f"COA {len(coas)}개 생성 완료 — 버튼 hover로 미리보기, 클릭 시 실행",
                "type": "attack_coa",
            })
        except Exception as e:
            _job_log(jid, f"오류: {e}")
            _job_log(jid, traceback.format_exc())
            _job_set(jid, "error", {"error": str(e)})

    def _run_evaluate_job(jid: str):
        _job_set(jid, "running")
        _job_log(jid, "전술 평가 및 학습 시작...")
        try:
            result = _get_session().evaluate_and_learn(history=[])
            history = result.get("history", [])
            last_msg = history[-1][1] if history else "완료"
            _job_log(jid, "전술 평가 완료")
            _job_set(jid, "done", {
                "message": last_msg,
                "type": "evaluate",
            })
        except Exception as e:
            _job_log(jid, f"오류: {e}")
            _job_log(jid, traceback.format_exc())
            _job_set(jid, "error", {"error": str(e)})

    @app.post("/api/mission/recon")
    async def api_mission_recon():
        jid = _new_job()
        t = threading.Thread(target=_run_recon_job, args=(jid,), daemon=True, name=f"recon-{jid}")
        t.start()
        return JSONResponse({"job_id": jid})

    @app.post("/api/mission/attack")
    async def api_mission_attack():
        jid = _new_job()
        t = threading.Thread(target=_run_attack_job, args=(jid,), daemon=True, name=f"attack-{jid}")
        t.start()
        return JSONResponse({"job_id": jid})

    @app.post("/api/mission/coa/execute")
    async def api_coa_execute(req: "CoaExecuteRequest"):
        try:
            return JSONResponse(_get_session().execute_coa(req.index))
        except Exception as e:
            logger.exception("api_coa_execute 오류")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/api/mission/evaluate")
    async def api_mission_evaluate():
        jid = _new_job()
        t = threading.Thread(target=_run_evaluate_job, args=(jid,), daemon=True, name=f"eval-{jid}")
        t.start()
        return JSONResponse({"job_id": jid})

    @app.get("/api/job/{job_id}")
    async def api_job_status(job_id: str):
        job = get_job_status(job_id)
        if job is None:
            return JSONResponse({"error": "잡을 찾을 수 없습니다"}, status_code=404)
        return JSONResponse(job)

    @app.get("/api/auto_plan_status")
    async def api_auto_plan_status():
        try:
            s = _get_session().auto_plan_status
            return JSONResponse({
                "active": s.get("active", False),
                "message": s.get("message", ""),
            })
        except Exception:
            return JSONResponse({"active": False, "message": ""})

    @app.get("/api/scenario/unit_types")
    async def api_scenario_unit_types():
        try:
            from c2.application.simulation.scenario import (
                UNIT_TYPE_SPECS,
                _BLUFOR_ZONE,
                _OPFOR_ZONE,
            )
            return JSONResponse({
                "unit_types": list(UNIT_TYPE_SPECS.keys()),
                "specs": {k: {"firepower_index": v["firepower_index"], "max_speed": v["max_speed"]}
                          for k, v in UNIT_TYPE_SPECS.items()},
                "blufor_zone": dict(_BLUFOR_ZONE),
                "opfor_zone": dict(_OPFOR_ZONE),
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/scenario/apply")
    async def api_scenario_apply(req: "ScenarioApplyRequest"):
        try:
            config = {
                "blufor": [{"id": u.id, "unit_type": u.unit_type,
                             "x": u.x, "y": u.y} for u in req.blufor],
                "opfor": [{"id": u.id, "unit_type": u.unit_type,
                            "x": u.x, "y": u.y} for u in req.opfor],
                "control_points": [{"id": c.id, "x": c.x, "y": c.y}
                                   for c in (req.control_points or [])],
            }
            result = _get_session().apply_custom_scenario(config)
            if result.get("ok"):
                return JSONResponse(result)
            return JSONResponse(result, status_code=400)
        except Exception as e:
            logger.exception("api_scenario_apply 오류")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    _app_singleton = app
    return app


# 모듈 로드시 기본 앱(agent 없음) — 하위 호환(`from ...api import app`)을 위해 생성.
app = create_app() if _FASTAPI_OK else None


# ── 서버 시작 ────────────────────────────────────────────────────────────────
def start_server(agent=None, host: str = "0.0.0.0", port: int = 7861):
    """
    FastAPI 서버를 시작합니다.

    Args:
        agent: BattlefieldAgent 인스턴스 (None이면 규칙 기반 동작)
        host: 바인드 주소
        port: 포트 번호
    """
    if not _FASTAPI_OK:
        raise ImportError(
            f"FastAPI/uvicorn 패키지가 필요합니다: pip install fastapi uvicorn[standard]\n원래 오류: {_fe_msg}"
        )

    if agent is not None:
        set_agent(agent)
        logger.info("에이전트 주입 완료: %s", type(agent).__name__)
    else:
        logger.info("에이전트 없이 시작 (규칙 기반 폴백)")

    # 엔진 초기화 (탐지 워커 스레드도 함께 기동)
    try:
        eng = _get_engine()
        if eng is not None:
            logger.info("워게임 엔진 초기화 완료 (running=%s)", eng.running)
        else:
            logger.warning("워게임 엔진 초기화 실패 — 제한적 동작")
    except Exception as e:
        logger.warning("엔진 초기화 오류: %s", e)

    app_ = create_app(agent)
    logger.info("C2 웹 대시보드 서버 시작: http://%s:%d", host, port)
    uvicorn.run(app_, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start_server()
