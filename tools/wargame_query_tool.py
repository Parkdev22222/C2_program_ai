"""
파이썬 워게임 시뮬레이터 실시간 쿼리 도구 (smolagents Tool)

WargameEngine SQLite DB를 통해 전장 상황을 조회합니다.
"""
import logging
from smolagents import tool

logger = logging.getLogger(__name__)

_wargame_engine = None


def register_wargame_engine(engine):
    """UI에서 WargameEngine 인스턴스를 등록."""
    global _wargame_engine
    _wargame_engine = engine


@tool
def get_wargame_situation() -> dict:
    """
    현재 워게임 시뮬레이터의 전장 상황을 반환합니다.
    아군(BLUFOR)은 실제 위치/전투력을 제공하고,
    적군(OPFOR)은 탐지 상태에 따라 인텔 필터가 적용됩니다.

    탐지 상태:
      - "detected"   : 정찰·근접 조우로 정확한 위치·종류·전투력 확인됨
      - "approximate": 초기 개략 정보 (위치 오차 ±수km, 종류·전투력 미확인)
      - "lost"       : 이전에 탐지됐으나 현재 탐지 범위 밖 (최종 탐지 위치 유지)

    Returns:
        {
            "status": "success" | "engine_not_ready",
            "game_time": str,
            "tick": int,
            "blufor_units": [
                { "unit_id", "unit_type", "x_m", "y_m",
                  "combat_power_pct", "status", "current_action" }, ...
            ],
            "opfor_intel": [
                { "unit_id", "detection_status",
                  "known_x_m", "known_y_m",
                  "unit_type",       # 탐지 전: "미확인"
                  "combat_power",    # 탐지 전: null
                  "detected_by" }, ...
            ],
            "summary": {
                "blufor_alive": int, "opfor_detected": int,
                "blufor_avg_cp": float
            }
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        state = _wargame_engine.get_state()

        # BLUFOR 아군 실제 정보
        blufor_units = [
            {
                "unit_id":         u["id"],
                "unit_type":       u.get("unit_type", ""),
                "x_m":             int(u["x"]),
                "y_m":             int(u["y"]),
                "combat_power_pct": round(u["combat_power"], 1),
                "status":          u["status"],
                "current_action":  u.get("current_action", "hold"),
            }
            for u in state.get("units", []) if u["side"] == "BLUFOR"
        ]

        # OPFOR 인텔 필터 적용
        blufor_intel_entries = state.get("intelligence", {}).get("BLUFOR", [])
        opfor_intel = [
            {
                "unit_id":          e["unit_id"],
                "detection_status": e["status"],
                "known_x_m":      int(e["known_x"]),
                "known_y_m":      int(e["known_y"]),
                "unit_type":        e["unit_type"] or "미확인",
                "combat_power":     e["combat_power"],
                "detected_by":      e["detected_by"],
            }
            for e in blufor_intel_entries
        ]

        blufor_alive = [u for u in blufor_units if u["status"] != "destroyed"]
        opfor_detected = [e for e in opfor_intel if e["detection_status"] == "detected"]

        return {
            "status":      "success",
            "game_time":   state.get("game_time_str", "00:00:00"),
            "tick":        state.get("tick", 0),
            "blufor_units": blufor_units,
            "opfor_intel":  opfor_intel,
            "summary": {
                "blufor_alive":    len(blufor_alive),
                "opfor_detected":  len(opfor_detected),
                "blufor_avg_cp":   round(
                    sum(u["combat_power_pct"] for u in blufor_alive) / len(blufor_alive), 1
                ) if blufor_alive else 0.0,
            },
        }
    except Exception as e:
        logger.error(f"get_wargame_situation error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@tool
def get_intelligence_report(side: str = "BLUFOR") -> dict:
    """
    특정 진영의 적 탐지 인텔 보고서를 반환합니다.
    탐지 상태에 따라 적 위치 정확도가 다릅니다.

    탐지 상태:
      - "detected"   : 정찰부대 탐지 또는 근접 조우 → 정확한 위치·종류·전투력
      - "approximate": 초기 개략 정보 → 위치 오차 ±수km, 종류·전투력 미확인
      - "lost"       : 탐지 해제 → 마지막 탐지 위치 유지, 현재 위치 미확인

    Args:
        side: 조회할 진영 ("BLUFOR" | "OPFOR"), 기본값 "BLUFOR"

    Returns:
        {
            "status": "success" | "engine_not_ready",
            "side": str,
            "game_time": str,
            "enemy_intel": [
                {
                    "unit_id": str,
                    "status": "detected" | "approximate" | "lost",
                    "known_x_m": int,
                    "known_y_m": int,
                    "unit_type": str,           # 탐지 전: "미확인"
                    "combat_power": float | None,  # 탐지 전: null
                    "detected_by": str | None,
                    "last_detected_tick": int
                }, ...
            ]
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}
    try:
        report = _wargame_engine.get_intelligence_report(side)
        return {"status": "success", **report}
    except Exception as e:
        logger.error(f"get_intelligence_report error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@tool
def get_wargame_unit_detail(unit_id: str) -> dict:
    """
    특정 부대의 상세 정보와 최근 이동 이력을 반환합니다.

    Args:
        unit_id: 부대 ID (예: "Alpha", "Charlie", "Red1", "Red3")

    Returns:
        {
            "status": "success" | "not_found" | "engine_not_ready",
            "unit": { unit_id, side, unit_type, x_m, y_m, combat_power_pct, status, current_action },
            "history": [ {tick, game_time, x_m, y_m, combat_power_pct, status}, ... ]
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        rows = _wargame_engine.db.get_latest_unit_states()
        unit_row = next((r for r in rows if r.get("unit_id", r.get("id", "")) == unit_id), None)

        if unit_row is None:
            state = _wargame_engine.get_state()
            u_data = next((u for u in state.get("units", []) if u["id"] == unit_id), None)
            if u_data is None:
                return {"status": "not_found", "message": f"부대 '{unit_id}'를 찾을 수 없습니다."}
            unit_row = {**u_data, "unit_id": u_data["id"]}

        unit_info = {
            "unit_id": unit_row.get("unit_id", unit_row.get("id", "")),
            "side": unit_row["side"],
            "unit_type": unit_row.get("unit_type", ""),
            "x_m": int(unit_row["x"]),
            "y_m": int(unit_row["y"]),
            "combat_power_pct": round(unit_row["combat_power"], 1),
            "status": unit_row["status"],
            "current_action": unit_row.get("current_action", "hold"),
        }

        history_rows = _wargame_engine.db.get_unit_history(unit_id, limit=20)
        history = [
            {
                "tick": r["tick"],
                "game_time": round(r["game_time"], 1),
                "x_m": int(r["x"]),
                "y_m": int(r["y"]),
                "combat_power_pct": round(r["combat_power"], 1),
                "status": r["status"],
            }
            for r in history_rows
        ]

        return {"status": "success", "unit": unit_info, "history": history}
    except Exception as e:
        logger.error(f"get_wargame_unit_detail error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@tool
def get_wargame_battle_log(n: int = 20) -> dict:
    """
    최근 전투 이벤트 로그를 반환합니다.
    교전 결과, 웨이포인트 도착, 공중지원, 임무명령, 게임 종료 등이 포함됩니다.

    Args:
        n: 가져올 이벤트 수 (기본 20, 최대 50)

    Returns:
        {
            "status": "success" | "engine_not_ready",
            "events": [ {"tick", "game_time", "event_type", "message"}, ... ]
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        events = _wargame_engine.db.get_recent_events(min(n, 50))
        return {"status": "success", "events": events}
    except Exception as e:
        return {"status": "error", "message": str(e)}
