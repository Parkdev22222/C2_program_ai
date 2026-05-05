"""
워게임 시뮬레이터 임무계획 실행 도구 (smolagents Tool)

LLM 에이전트가 JSON 형태의 임무계획을 생성한 후 워게임 엔진에 직접 적용합니다.
ARMA3와 동일한 JSON 포맷을 사용합니다.
"""
import json
import logging
from smolagents import tool

logger = logging.getLogger(__name__)

_wargame_engine = None


def register_wargame_engine(engine):
    """UI에서 WargameEngine 인스턴스를 등록."""
    global _wargame_engine
    _wargame_engine = engine


@tool
def apply_wargame_mission_plan(plan_json: str) -> dict:
    """
    JSON 형태의 BLUFOR 임무계획을 워게임 시뮬레이터에 적용합니다.
    각 중대에 이동 웨이포인트와 임무 유형을 부여합니다.

    Args:
        plan_json: JSON 문자열. 아래 형식을 따릅니다.
            {
              "mission_plans": [
                {
                  "company_id": "Alpha",
                  "mission_type": "attack",   // attack | defend | flank | withdraw | hold
                  "waypoints": [[x, y], ...], // 좌표 단위: m (0~30000)
                  "objective": "Red1 격멸"
                },
                ...
              ]
            }

    Returns:
        {
            "status": "success" | "engine_not_ready" | "error",
            "applied": int,   // 적용된 부대 수
            "skipped": list,  // 찾지 못하거나 전투불능인 부대 ID 목록
            "message": str
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        if isinstance(plan_json, dict):
            plan = plan_json
        else:
            plan = json.loads(plan_json)
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"JSON 파싱 실패: {e}"}

    try:
        mission_plans = plan.get("mission_plans", [])
        if not mission_plans:
            return {"status": "error", "message": "mission_plans 필드가 없거나 비어 있습니다."}

        # 적용 전 ID 유효성 사전 확인
        state = _wargame_engine.get_state()
        valid_ids = {u["id"] for u in state.get("units", []) if u["side"] == "BLUFOR"}
        skipped = [mp["company_id"] for mp in mission_plans
                   if mp.get("company_id", "") not in valid_ids]

        _wargame_engine.apply_mission_plan(plan)

        applied = len(mission_plans) - len(skipped)
        logger.info(f"임무계획 적용: {applied}개 부대, 건너뜀: {skipped}")
        return {
            "status": "success",
            "applied": applied,
            "skipped": skipped,
            "message": f"{applied}개 부대에 임무계획 적용 완료." + (
                f" (건너뜀: {skipped})" if skipped else ""
            ),
        }
    except Exception as e:
        logger.error(f"apply_wargame_mission_plan error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@tool
def apply_wargame_air_support(support_json: str) -> dict:
    """
    JSON 형태의 공중지원 계획을 워게임 시뮬레이터에 적용합니다.
    근접항공지원(CAS), 정밀타격(strike), 포병(artillery), 헬기(helicopter)를 지원합니다.

    Args:
        support_json: JSON 문자열. 아래 형식을 따릅니다.
            {
              "air_support_plans": [
                {
                  "call_sign": "VIPER-1",
                  "support_type": "cas",        // cas | strike | artillery | helicopter
                  "target": [x, y],             // 폭격 중심 좌표 (m)
                  "radius": 1500,               // 피해 반경 (m)
                  "delay": 60                   // 투입 지연 (게임 초)
                },
                ...
              ]
            }

    Returns:
        {
            "status": "success" | "engine_not_ready" | "error",
            "registered": int,   // 등록된 공중지원 수
            "message": str
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        if isinstance(support_json, dict):
            plan = support_json
        else:
            plan = json.loads(support_json)
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"JSON 파싱 실패: {e}"}

    try:
        support_plans = plan.get("air_support_plans", [])
        if not support_plans:
            return {"status": "error", "message": "air_support_plans 필드가 없거나 비어 있습니다."}

        _wargame_engine.apply_air_support_plan(plan)

        logger.info(f"공중지원 등록: {len(support_plans)}건")
        return {
            "status": "success",
            "registered": len(support_plans),
            "message": f"{len(support_plans)}건의 공중지원 요청이 등록되었습니다.",
        }
    except Exception as e:
        logger.error(f"apply_wargame_air_support error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@tool
def get_wargame_engine_status() -> dict:
    """
    워게임 시뮬레이터 엔진 상태(실행 중 여부, 시간 배율 등)를 반환합니다.

    Returns:
        {
            "status": "success" | "engine_not_ready",
            "running": bool,
            "game_time": str,
            "tick": int,
            "time_scale": int,   // 실제 1초 = X 게임 초
            "winner": str | null,
            "air_supports_active": int
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    try:
        state = _wargame_engine.get_state()
        active_air = sum(1 for a in state.get("air_supports", []) if a["status"] == "active")
        return {
            "status": "success",
            "running": _wargame_engine.running,
            "game_time": state.get("game_time_str", "00:00:00"),
            "tick": state.get("tick", 0),
            "time_scale": int(_wargame_engine.time_scale),
            "winner": state.get("winner"),
            "air_supports_active": active_air,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
