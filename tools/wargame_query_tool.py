"""
워게임 전술 지도 쿼리 도구 모음
전술 지도의 아군/적군 부대 위치, 상태, 이동 경로를 조회합니다.
(상단) ARMA3 릴레이 데이터 쿼리 도구
(하단) 파이썬 워게임 시뮬레이터 실시간 쿼리 도구
"""
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from smolagents import tool

logger = logging.getLogger(__name__)

WARGAME_STATE_FILE = Path(__file__).parent.parent / "data" / "wargame_state.json"

_wargame_state: Optional[dict] = None


def _load_wargame_state() -> dict:
    global _wargame_state
    if _wargame_state is None:
        if WARGAME_STATE_FILE.exists():
            with open(WARGAME_STATE_FILE) as f:
                _wargame_state = json.load(f)
        else:
            _wargame_state = _default_wargame_state()
    return _wargame_state


def set_wargame_state(state: dict):
    global _wargame_state
    _wargame_state = state


def _default_wargame_state() -> dict:
    return {
        "map": {"center": [37.5, 127.0], "zoom": 10},
        "units": [],
        "timestamp": "",
    }


def _parse_location(loc) -> Dict[str, float]:
    if isinstance(loc, list) and len(loc) >= 2:
        return {"lat": loc[0], "lon": loc[1]}
    if isinstance(loc, dict):
        return {"lat": loc.get("lat", 0.0), "lon": loc.get("lon", 0.0)}
    return {"lat": 0.0, "lon": 0.0}


# ─────────────────────────────────────────────
# smolagents 도구 함수들
# ─────────────────────────────────────────────

@tool
def get_tactical_situation() -> dict:
    """
    현재 전술 지도의 전반적인 상황을 반환합니다.
    아군/적군/중립 부대 수, 지도 중심 좌표를 포함합니다.

    Returns:
        {
            "unit_counts": {"friendly": int, "hostile": int, "neutral": int, "unknown": int},
            "map_center": {"lat": float, "lon": float},
            "total_units": int
        }
    """
    state = _load_wargame_state()
    units = state.get("units", [])
    counts: Dict[str, int] = {"friendly": 0, "hostile": 0, "neutral": 0, "unknown": 0}
    for u in units:
        aff = u.get("affiliation", "unknown").lower()
        counts[aff] = counts.get(aff, 0) + 1

    return {
        "status": "success",
        "unit_counts": counts,
        "total_units": len(units),
        "map_center": _parse_location(state.get("map", {}).get("center", [0, 0])),
        "map_zoom": state.get("map", {}).get("zoom", 10),
        "timestamp": state.get("timestamp", ""),
    }


@tool
def get_friendly_units() -> dict:
    """
    모든 아군(청군) 부대의 정보를 반환합니다.
    부대명, 유형, 제대, 위치, 웨이포인트를 포함합니다.

    Returns:
        {
            "status": "success",
            "units": [{"name", "type", "echelon", "location", "waypoints"}, ...],
            "count": int
        }
    """
    state = _load_wargame_state()
    friendly = [u for u in state.get("units", []) if u.get("affiliation", "").lower() == "friendly"]
    formatted = [
        {
            "name": u.get("name", ""),
            "type": u.get("type", ""),
            "echelon": u.get("echelon", ""),
            "location": _parse_location(u.get("location")),
            "waypoints": [_parse_location(wp) for wp in u.get("waypoints", [])],
            "status": u.get("status", "active"),
        }
        for u in friendly
    ]
    return {"status": "success", "units": formatted, "count": len(formatted)}


@tool
def get_hostile_units() -> dict:
    """
    모든 적군(홍군) 부대의 정보를 반환합니다.
    부대명, 유형, 제대, 위치, 웨이포인트를 포함합니다.

    Returns:
        {
            "status": "success",
            "units": [{"name", "type", "echelon", "location", "waypoints"}, ...],
            "count": int
        }
    """
    state = _load_wargame_state()
    hostile = [u for u in state.get("units", []) if u.get("affiliation", "").lower() == "hostile"]
    formatted = [
        {
            "name": u.get("name", ""),
            "type": u.get("type", ""),
            "echelon": u.get("echelon", ""),
            "location": _parse_location(u.get("location")),
            "waypoints": [_parse_location(wp) for wp in u.get("waypoints", [])],
            "status": u.get("status", "active"),
        }
        for u in hostile
    ]
    return {"status": "success", "units": formatted, "count": len(formatted)}


@tool
def get_unit_details(unit_name: str) -> dict:
    """
    특정 부대의 상세 정보를 반환합니다.

    Args:
        unit_name: 부대명 (예: "1대대", "Red Force Alpha")

    Returns:
        {"status": "success" | "not_found", "unit": {...}}
    """
    state = _load_wargame_state()
    name_lower = unit_name.lower()
    for u in state.get("units", []):
        if u.get("name", "").lower() == name_lower:
            return {
                "status": "success",
                "unit": {
                    **u,
                    "location": _parse_location(u.get("location")),
                    "waypoints": [_parse_location(wp) for wp in u.get("waypoints", [])],
                },
            }
    return {"status": "not_found", "message": f"부대 '{unit_name}'을 찾을 수 없습니다."}


@tool
def get_units_by_type(unit_type: str) -> dict:
    """
    특정 유형의 부대 목록을 반환합니다.

    Args:
        unit_type: 부대 유형 ("INFANTRY", "ARMOR", "ARTILLERY",
                   "AVIATION", "ENGINEERING", "LOGISTICS")

    Returns:
        {
            "status": "success",
            "unit_type": str,
            "units": [...],
            "count": int,
            "affiliation_breakdown": {"friendly": int, "hostile": int, ...}
        }
    """
    state = _load_wargame_state()
    type_lower = unit_type.lower()
    matched = [u for u in state.get("units", []) if u.get("type", "").lower() == type_lower]

    breakdown: Dict[str, int] = {}
    for u in matched:
        aff = u.get("affiliation", "unknown").lower()
        breakdown[aff] = breakdown.get(aff, 0) + 1

    formatted = [
        {
            "name": u.get("name", ""),
            "affiliation": u.get("affiliation", ""),
            "echelon": u.get("echelon", ""),
            "location": _parse_location(u.get("location")),
        }
        for u in matched
    ]
    return {
        "status": "success" if matched else "no_results",
        "unit_type": unit_type,
        "units": formatted,
        "count": len(formatted),
        "affiliation_breakdown": breakdown,
    }


# ═════════════════════════════════════════════════════════════════
# 파이썬 워게임 시뮬레이터 실시간 쿼리 도구
# WargameEngine 인스턴스를 등록하면 SQLite DB를 통해 상태 조회
# ═════════════════════════════════════════════════════════════════

_wargame_engine = None


def register_wargame_engine(engine):
    """UI에서 WargameEngine 인스턴스를 등록."""
    global _wargame_engine
    _wargame_engine = engine


@tool
def get_wargame_situation() -> dict:
    """
    현재 워게임 시뮬레이터의 전체 전장 상황을 반환합니다.
    아군(BLUFOR)/적군(OPFOR) 전 부대의 종류·위치·전투력(%)·상태·행동을 포함합니다.

    Returns:
        {
            "status": "success" | "engine_not_ready",
            "game_time": str,
            "tick": int,
            "units": [
                {
                    "unit_id": str,          # "Alpha", "Red1" 등
                    "side": str,             # "BLUFOR" | "OPFOR"
                    "unit_type": str,        # "기계화보병" | "전차" | "정찰" | "대전차" | "자주포"
                    "x_km": float,           # 동쪽 좌표 (km)
                    "y_km": float,           # 북쪽 좌표 (km)
                    "combat_power_pct": float,  # 전투력 0~100%
                    "status": str,           # "active" | "suppressed" | "destroyed"
                    "current_action": str    # "hold" | "attack" | "defend" | "move" 등
                }, ...
            ],
            "summary": {
                "blufor_alive": int,
                "opfor_alive": int,
                "blufor_avg_cp": float,
                "opfor_avg_cp": float
            }
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        rows = _wargame_engine.db.get_latest_unit_states()
        if not rows:
            # DB 저장 전이면 엔진 메모리에서 직접 읽기
            state = _wargame_engine.get_state()
            rows = [
                {
                    "unit_id": u["id"], "side": u["side"],
                    "unit_type": u.get("unit_type", ""),
                    "x": u["x"], "y": u["y"],
                    "combat_power": u["combat_power"],
                    "status": u["status"], "current_action": u.get("current_action", "hold"),
                    "game_time": state.get("game_time", 0),
                    "tick": state.get("tick", 0),
                }
                for u in state.get("units", [])
            ]

        units = []
        for r in rows:
            units.append({
                "unit_id": r.get("unit_id", r.get("id", "")),
                "side": r["side"],
                "unit_type": r.get("unit_type", ""),
                "x_km": round(r["x"] / 1000, 2),
                "y_km": round(r["y"] / 1000, 2),
                "combat_power_pct": round(r["combat_power"], 1),
                "status": r["status"],
                "current_action": r.get("current_action", "hold"),
            })

        blufor = [u for u in units if u["side"] == "BLUFOR" and u["status"] != "destroyed"]
        opfor  = [u for u in units if u["side"] == "OPFOR"  and u["status"] != "destroyed"]

        state_info = _wargame_engine.get_state()
        return {
            "status": "success",
            "game_time": state_info.get("game_time_str", "00:00:00"),
            "tick": state_info.get("tick", 0),
            "units": units,
            "summary": {
                "blufor_alive": len(blufor),
                "opfor_alive": len(opfor),
                "blufor_avg_cp": round(sum(u["combat_power_pct"] for u in blufor) / len(blufor), 1) if blufor else 0.0,
                "opfor_avg_cp":  round(sum(u["combat_power_pct"] for u in opfor)  / len(opfor),  1) if opfor  else 0.0,
            },
        }
    except Exception as e:
        logger.error(f"get_wargame_situation error: {e}", exc_info=True)
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
            "unit": { unit_id, side, unit_type, x_km, y_km, combat_power_pct, status, current_action },
            "history": [ {tick, game_time, x_km, y_km, combat_power_pct, status}, ... ]  # 최근 20틱
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        rows = _wargame_engine.db.get_latest_unit_states()
        unit_row = next((r for r in rows if r.get("unit_id", r.get("id", "")) == unit_id), None)

        if unit_row is None:
            # 엔진 메모리에서 탐색
            state = _wargame_engine.get_state()
            u_data = next((u for u in state.get("units", []) if u["id"] == unit_id), None)
            if u_data is None:
                return {"status": "not_found", "message": f"부대 '{unit_id}'를 찾을 수 없습니다."}
            unit_row = u_data
            unit_row["unit_id"] = u_data["id"]

        unit_info = {
            "unit_id": unit_row.get("unit_id", unit_row.get("id", "")),
            "side": unit_row["side"],
            "unit_type": unit_row.get("unit_type", ""),
            "x_km": round(unit_row["x"] / 1000, 2),
            "y_km": round(unit_row["y"] / 1000, 2),
            "combat_power_pct": round(unit_row["combat_power"], 1),
            "status": unit_row["status"],
            "current_action": unit_row.get("current_action", "hold"),
        }

        history_rows = _wargame_engine.db.get_unit_history(unit_id, limit=20)
        history = [
            {
                "tick": r["tick"],
                "game_time": round(r["game_time"], 1),
                "x_km": round(r["x"] / 1000, 2),
                "y_km": round(r["y"] / 1000, 2),
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
