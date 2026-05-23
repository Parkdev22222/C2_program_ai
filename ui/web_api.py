"""
C2 군사 AI — FastAPI 웹 API 백엔드
HTML 기반 대시보드를 위한 REST API 서버
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
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

# 프로젝트 루트를 sys.path에 추가
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)

# ── 좌표 변환 유틸 ──────────────────────────────────────────────────────────
try:
    from tools.coord_utils import xy_to_latlon as _xy_to_latlon
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


# ── gradio_app 모듈 참조 (lazy) ──────────────────────────────────────────────
def _ga():
    """gradio_app 모듈을 반환. import 시점 지연."""
    import ui.gradio_app as _mod
    return _mod


def _get_engine():
    try:
        return _ga()._wg_ensure_engine()
    except Exception as e:
        logger.warning("_wg_ensure_engine 실패: %s", e)
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

    return result


# ── FastAPI 앱 ───────────────────────────────────────────────────────────────
if _FASTAPI_OK:
    app = FastAPI(title="C2 Military AI Web API", version="1.0.0")
else:
    app = None

_DASHBOARD_DIR = Path(__file__).parent / "dashboard"


# ── 요청 모델 ────────────────────────────────────────────────────────────────
class TimescaleRequest(BaseModel):
    scale: float


class ChatRequest(BaseModel):
    message: str


class ScenarioUnitDef(BaseModel):
    id: str
    unit_type: str
    x: Optional[float] = None
    y: Optional[float] = None


class ScenarioApplyRequest(BaseModel):
    blufor: List[ScenarioUnitDef]
    opfor: List[ScenarioUnitDef]


# ── 엔드포인트 ───────────────────────────────────────────────────────────────
if _FASTAPI_OK:

    @app.get("/")
    async def serve_index():
        idx = _DASHBOARD_DIR / "index.html"
        if not idx.exists():
            return JSONResponse({"error": "index.html not found"}, status_code=404)
        return FileResponse(str(idx), media_type="text/html")

    @app.get("/api/state")
    async def api_state():
        eng = _get_engine()
        if eng is None:
            return JSONResponse(
                {"error": "워게임 엔진 초기화 실패"},
                status_code=503,
            )
        try:
            state = eng.get_state()
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
        except Exception as e:
            logger.exception("api_events 오류")
            return JSONResponse([], status_code=500)

    @app.get("/api/status_text")
    async def api_status_text():
        eng = _get_engine()
        if eng is None:
            return JSONResponse({"text": "워게임 엔진 초기화 실패"})
        try:
            state = eng.get_state()
            text = _ga()._wg_status_text(state)
            return JSONResponse({"text": text})
        except Exception as e:
            logger.exception("api_status_text 오류")
            return JSONResponse({"text": f"오류: {e}"})

    @app.post("/api/control/start")
    async def api_control_start():
        try:
            label, fig, damage_fig, status, log = _ga().wargame_start_pause()
            eng = _get_engine()
            running = eng.running if eng else False
            return JSONResponse({"running": running, "label": label})
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
                eng.stop()
            return JSONResponse({"running": eng.running})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/control/reset")
    async def api_control_reset():
        try:
            label, fig, damage_fig, status, log = _ga().wargame_reset_sim()
            return JSONResponse({"ok": True, "label": label})
        except Exception as e:
            logger.exception("api_control_reset 오류")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/control/timescale")
    async def api_control_timescale(req: TimescaleRequest):
        try:
            _ga().wargame_set_timescale(req.scale)
            return JSONResponse({"ok": True, "scale": req.scale})
        except Exception as e:
            logger.exception("api_control_timescale 오류")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/chat")
    async def api_chat(req: ChatRequest):
        if not req.message.strip():
            return JSONResponse({"history": [], "response": ""})
        try:
            history, _ = _ga().wg_chat_send(req.message, [])
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
            result = _ga().wargame_request_recon_plan(history=[])
            history = result[0]
            plan_text = result[1] if len(result) > 1 else ""
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
        _job_log(jid, "공격 임무계획 수립 시작...")
        try:
            result = _ga().wargame_request_attack_plan(history=[])
            history = result[0]
            plan_text = result[1] if len(result) > 1 else ""
            last_msg = history[-1][1] if history else ""
            _job_log(jid, "공격 임무계획 완료")
            _job_set(jid, "done", {
                "plan_text": plan_text,
                "message": last_msg,
                "type": "attack",
            })
        except Exception as e:
            _job_log(jid, f"오류: {e}")
            _job_log(jid, traceback.format_exc())
            _job_set(jid, "error", {"error": str(e)})

    def _run_evaluate_job(jid: str):
        _job_set(jid, "running")
        _job_log(jid, "전술 평가 및 학습 시작...")
        try:
            result = _ga().wargame_evaluate_and_learn(history=[])
            history = result[0]
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
            s = _ga()._auto_plan_status
            return JSONResponse({
                "active":  s.get("active", False),
                "message": s.get("message", ""),
            })
        except Exception:
            return JSONResponse({"active": False, "message": ""})

    @app.get("/api/scenario/unit_types")
    async def api_scenario_unit_types():
        try:
            from wargame.scenario import UNIT_TYPE_SPECS
            return JSONResponse({
                "unit_types": list(UNIT_TYPE_SPECS.keys()),
                "specs": {k: {"firepower_index": v["firepower_index"], "max_speed": v["max_speed"]}
                          for k, v in UNIT_TYPE_SPECS.items()},
                "blufor_zone": {"x_min": 2000, "x_max": 13000, "y_min": 1500, "y_max": 12000},
                "opfor_zone":  {"x_min": 17000, "x_max": 28000, "y_min": 17000, "y_max": 28500},
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/scenario/apply")
    async def api_scenario_apply(req: ScenarioApplyRequest):
        try:
            config = {
                "blufor": [{"id": u.id, "unit_type": u.unit_type,
                             "x": u.x, "y": u.y} for u in req.blufor],
                "opfor":  [{"id": u.id, "unit_type": u.unit_type,
                             "x": u.x, "y": u.y} for u in req.opfor],
            }
            result = _ga().wargame_apply_custom_scenario(config)
            if result.get("ok"):
                return JSONResponse(result)
            return JSONResponse(result, status_code=400)
        except Exception as e:
            logger.exception("api_scenario_apply 오류")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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

    # gradio_app에 에이전트 주입
    try:
        import ui.gradio_app as _ga_mod
        if agent is not None:
            _ga_mod._agent = agent
            logger.info("에이전트 주입 완료: %s", type(agent).__name__)
        else:
            logger.info("에이전트 없이 시작 (규칙 기반 폴백)")
    except Exception as e:
        logger.warning("에이전트 주입 실패: %s", e)

    # 엔진 초기화 (탐지 워커 스레드도 gradio_app import 시 자동 시작)
    try:
        import ui.gradio_app as _ga_mod
        eng = _ga_mod._wg_ensure_engine()
        if eng is not None:
            logger.info("워게임 엔진 초기화 완료 (running=%s)", eng.running)
        else:
            logger.warning("워게임 엔진 초기화 실패 — 제한적 동작")
    except Exception as e:
        logger.warning("엔진 초기화 오류: %s", e)

    logger.info("C2 웹 대시보드 서버 시작: http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start_server()
