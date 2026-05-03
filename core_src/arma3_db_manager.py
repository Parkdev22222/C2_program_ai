"""
ARMA3 전장 상태 DB 매니저

ARMA3 relay.py에서 수신된 전장 데이터를 JSON 파일에 저장하고
에이전트 툴에서 조회할 수 있게 합니다.
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ARMA3_STATE_FILE = Path(__file__).parent.parent / "data" / "arma3_state.json"

_lock = threading.Lock()


def _empty_state() -> dict:
    return {
        "last_updated": "",
        "mission_time": 0,
        "units": [],
        "groups": [],
        "summary": {
            "opfor": {"infantry": 0, "armor": 0, "helicopter": 0},
            "blufor": {"infantry": 0, "armor": 0, "helicopter": 0},
        },
        "history": [],
    }


def load_state() -> dict:
    """현재 저장된 ARMA3 전장 상태를 반환합니다."""
    with _lock:
        if ARMA3_STATE_FILE.exists():
            try:
                with open(ARMA3_STATE_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"arma3_state.json 읽기 실패: {e}")
        return _empty_state()


def save_report(report: dict) -> None:
    """
    relay.py에서 수신된 보고 데이터를 DB에 저장합니다.

    report 구조:
      {
        "t": int,           # 미션 내 경과 시간(초)
        "units": [...],     # 유닛 목록
        "groups": [...],    # 그룹 목록
        "summary": {...},   # 진영별 병력 요약
        "received_at": str, # relay.py가 붙인 수신 시각 (ISO)
      }
    """
    with _lock:
        state = _empty_state()
        if ARMA3_STATE_FILE.exists():
            try:
                with open(ARMA3_STATE_FILE, encoding="utf-8") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        state["last_updated"] = report.get("received_at", datetime.utcnow().isoformat())
        state["mission_time"] = report.get("t", state.get("mission_time", 0))
        state["units"] = report.get("units", [])
        state["groups"] = report.get("groups", [])
        state["summary"] = report.get("summary", state.get("summary", {}))

        # 히스토리: 최근 20개만 보관
        history = state.get("history", [])
        history.append({
            "t": report.get("t"),
            "received_at": state["last_updated"],
            "unit_count": len(state["units"]),
            "summary": state["summary"],
        })
        state["history"] = history[-20:]

        ARMA3_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ARMA3_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    logger.info(
        f"ARMA3 상태 저장: mission_time={state['mission_time']}  "
        f"units={len(state['units'])}  groups={len(state['groups'])}"
    )
