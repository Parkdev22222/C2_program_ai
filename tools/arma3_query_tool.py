"""
ARMA3 실시간 전장 데이터 쿼리 도구

ARMA3 게임에서 relay.py를 통해 수신된 실시간 전장 상황을
에이전트가 조회할 수 있는 smolagents 도구 모음입니다.

데이터 좌표계: ARMA3 ASL (x=East, y=North, meters)
"""
import logging
from typing import Optional
from smolagents import tool

logger = logging.getLogger(__name__)


def _state():
    from core_src.arma3_db_manager import load_state
    return load_state()


# ─────────────────────────────────────────────────────────────────

@tool
def get_arma3_situation() -> dict:
    """
    ARMA3 게임에서 수신된 현재 전장 상황 요약을 반환합니다.
    미션 경과 시간, 마지막 업데이트 시각, 진영별 병력 현황을 포함합니다.

    Returns:
        {
            "status": "ok" | "no_data",
            "last_updated": str,       # ISO 시각
            "mission_time_sec": int,   # 미션 경과 시간(초)
            "total_units": int,
            "total_groups": int,
            "summary": {
                "opfor":  {"infantry": int, "armor": int, "helicopter": int},
                "blufor": {"infantry": int, "armor": int, "helicopter": int}
            }
        }
    """
    state = _state()
    if not state.get("last_updated"):
        return {"status": "no_data", "message": "ARMA3 데이터가 아직 수신되지 않았습니다."}

    return {
        "status": "ok",
        "last_updated": state["last_updated"],
        "mission_time_sec": state.get("mission_time", 0),
        "total_units": len(state.get("units", [])),
        "total_groups": len(state.get("groups", [])),
        "summary": state.get("summary", {}),
    }


@tool
def get_arma3_enemy_units(category: str = "") -> dict:
    """
    ARMA3에서 수신된 적군(OPFOR) 유닛 목록을 반환합니다.

    Args:
        category: 유닛 카테고리 필터 (비어있으면 전체 반환).
                  가능한 값: "infantry", "armor", "apc", "helicopter",
                             "aircraft", "naval", "vehicle", "truck", "unknown"

    Returns:
        {
            "status": "ok",
            "category_filter": str,
            "units": [
                {
                    "id": str,    # ARMA3 netId
                    "type": str,  # 유닛 클래스명
                    "cat": str,   # 카테고리
                    "hp": int,    # 내구도 0-100
                    "x": float,   # 동쪽(m)
                    "y": float,   # 북쪽(m)
                    "grp": str    # 소속 그룹ID
                }, ...
            ],
            "count": int
        }
    """
    state = _state()
    units = [u for u in state.get("units", []) if u.get("side") == "OPFOR"]
    if category:
        units = [u for u in units if u.get("cat", "").lower() == category.lower()]
    return {
        "status": "ok",
        "category_filter": category or "all",
        "units": units,
        "count": len(units),
    }


@tool
def get_arma3_friendly_units(category: str = "") -> dict:
    """
    ARMA3에서 수신된 아군(BLUFOR) 유닛 목록을 반환합니다.

    Args:
        category: 유닛 카테고리 필터 (비어있으면 전체 반환).
                  가능한 값: "infantry", "armor", "apc", "helicopter",
                             "aircraft", "naval", "vehicle", "truck", "unknown"

    Returns:
        {
            "status": "ok",
            "category_filter": str,
            "units": [...],
            "count": int
        }
    """
    state = _state()
    units = [u for u in state.get("units", []) if u.get("side") == "BLUFOR"]
    if category:
        units = [u for u in units if u.get("cat", "").lower() == category.lower()]
    return {
        "status": "ok",
        "category_filter": category or "all",
        "units": units,
        "count": len(units),
    }


@tool
def get_arma3_units_by_category(category: str) -> dict:
    """
    ARMA3 전장에서 특정 카테고리의 모든 유닛(아군+적군)을 반환합니다.
    적 전차 위치 파악, 헬기 분포 등에 유용합니다.

    Args:
        category: 유닛 카테고리.
                  가능한 값: "infantry", "armor", "apc", "helicopter",
                             "aircraft", "naval", "vehicle", "truck", "unknown"

    Returns:
        {
            "status": "ok",
            "category": str,
            "units": [...],
            "count": int,
            "by_side": {"OPFOR": int, "BLUFOR": int, "INDEP": int, "CIV": int}
        }
    """
    state = _state()
    matched = [u for u in state.get("units", []) if u.get("cat", "").lower() == category.lower()]

    by_side: dict = {}
    for u in matched:
        side = u.get("side", "UNKNOWN")
        by_side[side] = by_side.get(side, 0) + 1

    return {
        "status": "ok" if matched else "no_results",
        "category": category,
        "units": matched,
        "count": len(matched),
        "by_side": by_side,
    }


@tool
def get_arma3_groups(side: str = "") -> dict:
    """
    ARMA3 전장의 그룹(분대/소대) 목록을 반환합니다.
    각 그룹의 진영, 생존 병력 수, 지휘관 위치를 포함합니다.

    Args:
        side: 진영 필터 (비어있으면 전체). "OPFOR", "BLUFOR", "INDEP", "CIV"

    Returns:
        {
            "status": "ok",
            "side_filter": str,
            "groups": [
                {
                    "id": str,       # 그룹ID
                    "side": str,
                    "strength": int, # 생존 병력 수
                    "x": float,      # 지휘관 위치 동쪽(m)
                    "y": float       # 지휘관 위치 북쪽(m)
                }, ...
            ],
            "count": int
        }
    """
    state = _state()
    groups = state.get("groups", [])
    if side:
        groups = [g for g in groups if g.get("side", "").upper() == side.upper()]
    return {
        "status": "ok",
        "side_filter": side or "all",
        "groups": groups,
        "count": len(groups),
    }
