"""
워게임 전술 지도 쿼리 도구 모음
전술 지도의 아군/적군 부대 위치, 상태, 이동 경로를 조회합니다.
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
