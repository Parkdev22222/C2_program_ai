"""
파이썬 워게임 시뮬레이터 실시간 쿼리 도구 (smolagents Tool)

WargameEngine SQLite DB를 통해 전장 상황을 조회합니다.
"""
import logging
import re as _re
from smolagents import tool
from tools.coord_utils import xy_to_latlon

logger = logging.getLogger(__name__)

_wargame_engine = None

# 최근 몇 틱 이내 이벤트를 "현재 교전 중"으로 판정할지
_ATTACK_WINDOW_TICKS = 5


def register_wargame_engine(engine):
    """UI에서 WargameEngine 인스턴스를 등록."""
    global _wargame_engine
    _wargame_engine = engine


def _build_attack_status(current_tick: int, blufor_ids: set) -> dict:
    """
    최근 _ATTACK_WINDOW_TICKS 틱 이내 이벤트를 파싱해
    각 BLUFOR 유닛의 피격 여부 및 공격 수단을 반환.

    Returns:
        {unit_id: {"be_attacked": bool, "enemy_attack_method": list[str]}}
        attack_method 값: "직사격" | "간접사격" | "공중폭격"
    """
    result = {uid: {"be_attacked": False, "enemy_attack_method": []} for uid in blufor_ids}
    if _wargame_engine is None:
        return result

    try:
        events = _wargame_engine.db.get_recent_events(n=60)
    except Exception:
        return result

    tick_threshold = current_tick - _ATTACK_WINDOW_TICKS

    for ev in events:
        if ev.get("tick", 0) < tick_threshold:
            continue
        etype = ev.get("event_type", "")
        msg   = ev.get("message", "")

        if etype in ("COMBAT", "SURPRISE"):
            # 형식: "{attacker}({type})→{defender}({type}): ..."
            # OPFOR→BLUFOR인 경우만 추출
            m = _re.search(r'→(\w+)\(', msg)
            if m:
                defender = m.group(1)
                if defender in blufor_ids:
                    entry = result[defender]
                    entry["be_attacked"] = True
                    if "직사격" not in entry["enemy_attack_method"]:
                        entry["enemy_attack_method"].append("직사격")

        elif etype == "INDIRECT":
            # 형식: "{spg}(자주포) 간접사격 → {defender}: ..."
            m = _re.search(r'간접사격 → (\w+):', msg)
            if m:
                defender = m.group(1)
                if defender in blufor_ids:
                    entry = result[defender]
                    entry["be_attacked"] = True
                    if "간접사격" not in entry["enemy_attack_method"]:
                        entry["enemy_attack_method"].append("간접사격")

        elif etype == "AIR_STRIKE":
            # 형식: "[OPFOR] {call_sign}→{unit_id}: ..."
            if "[OPFOR]" in msg:
                m = _re.search(r'→(\w+):', msg)
                if m:
                    defender = m.group(1)
                    if defender in blufor_ids:
                        entry = result[defender]
                        entry["be_attacked"] = True
                        if "공중폭격" not in entry["enemy_attack_method"]:
                            entry["enemy_attack_method"].append("공중폭격")

    # 공격 수단이 없으면 빈 문자열로 통일
    for uid, entry in result.items():
        if not entry["be_attacked"]:
            entry["enemy_attack_method"] = None
        elif len(entry["enemy_attack_method"]) == 1:
            entry["enemy_attack_method"] = entry["enemy_attack_method"][0]
        else:
            entry["enemy_attack_method"] = ", ".join(entry["enemy_attack_method"])

    return result


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
                {
                  "unit_id", "unit_type",
                  "lat": float, "lon": float,   # WGS84 위경도 (주 좌표)
                  "x_m": int, "y_m": int,        # 내부 미터 (하위호환)
                  "combat_power_pct", "status", "current_action",
                  "be_attacked": bool,            # 최근 5틱 내 피격 여부
                  "enemy_attack_method": str|null  # "직사격"|"간접사격"|"공중폭격"|복합
                }, ...
            ],
            "opfor_intel": [
                { "unit_id", "detection_status",
                  "known_lat": float, "known_lon": float,  # WGS84 위경도 (주 좌표)
                  "known_x_m": int, "known_y_m": int,       # 내부 미터 (하위호환)
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
        current_tick = state.get("tick", 0)

        # BLUFOR 유닛 ID 집합
        blufor_raw = [u for u in state.get("units", []) if u["side"] == "BLUFOR"]
        blufor_ids = {u["id"] for u in blufor_raw}

        # 피격 상태 판정
        attack_status = _build_attack_status(current_tick, blufor_ids)

        # BLUFOR 아군 실제 정보
        blufor_units = []
        for u in blufor_raw:
            lat, lon = xy_to_latlon(u["x"], u["y"])
            blufor_units.append({
                "unit_id":            u["id"],
                "unit_type":          u.get("unit_type", ""),
                "lat":                lat,
                "lon":                lon,
                "x_m":                int(u["x"]),
                "y_m":                int(u["y"]),
                "combat_power_pct":   round(u["combat_power"], 1),
                "status":             u["status"],
                "current_action":     u.get("current_action", "hold"),
                "be_attacked":        attack_status[u["id"]]["be_attacked"],
                "enemy_attack_method": attack_status[u["id"]]["enemy_attack_method"],
            })

        # OPFOR 인텔 필터 적용
        blufor_intel_entries = state.get("intelligence", {}).get("BLUFOR", [])
        opfor_intel = []
        for e in blufor_intel_entries:
            known_lat, known_lon = xy_to_latlon(e["known_x"], e["known_y"])
            opfor_intel.append({
                "unit_id":          e["unit_id"],
                "detection_status": e["status"],
                "known_lat":        known_lat,
                "known_lon":        known_lon,
                "known_x_m":        int(e["known_x"]),
                "known_y_m":        int(e["known_y"]),
                "unit_type":        e["unit_type"] or "미확인",
                "combat_power":     e["combat_power"],
                "detected_by":      e["detected_by"],
            })

        blufor_alive = [u for u in blufor_units if u["status"] != "destroyed"]
        opfor_detected = [e for e in opfor_intel if e["detection_status"] == "detected"]

        return {
            "status":      "success",
            "game_time":   state.get("game_time_str", "00:00:00"),
            "tick":        current_tick,
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
                    "known_lat": float, "known_lon": float,  # WGS84 위경도 (주 좌표)
                    "known_x_m": int, "known_y_m": int,       # 내부 미터 (하위호환)
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
        # Convert enemy_intel coordinates to lat/lon
        for entry in report.get("enemy_intel", []):
            kx = entry.get("known_x_m", entry.get("known_x", 0))
            ky = entry.get("known_y_m", entry.get("known_y", 0))
            known_lat, known_lon = xy_to_latlon(kx, ky)
            entry["known_lat"] = known_lat
            entry["known_lon"] = known_lon
            # Ensure known_x_m / known_y_m aliases exist
            if "known_x_m" not in entry and "known_x" in entry:
                entry["known_x_m"] = int(entry["known_x"])
            if "known_y_m" not in entry and "known_y" in entry:
                entry["known_y_m"] = int(entry["known_y"])
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
            "unit": { unit_id, side, unit_type, lat, lon, x_m, y_m, combat_power_pct, status, current_action },
            "history": [ {tick, game_time, lat, lon, x_m, y_m, combat_power_pct, status}, ... ]
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

        u_lat, u_lon = xy_to_latlon(unit_row["x"], unit_row["y"])
        unit_info = {
            "unit_id": unit_row.get("unit_id", unit_row.get("id", "")),
            "side": unit_row["side"],
            "unit_type": unit_row.get("unit_type", ""),
            "lat": u_lat,
            "lon": u_lon,
            "x_m": int(unit_row["x"]),
            "y_m": int(unit_row["y"]),
            "combat_power_pct": round(unit_row["combat_power"], 1),
            "status": unit_row["status"],
            "current_action": unit_row.get("current_action", "hold"),
        }

        history_rows = _wargame_engine.db.get_unit_history(unit_id, limit=20)
        history = []
        for r in history_rows:
            h_lat, h_lon = xy_to_latlon(r["x"], r["y"])
            history.append({
                "tick": r["tick"],
                "game_time": round(r["game_time"], 1),
                "lat": h_lat,
                "lon": h_lon,
                "x_m": int(r["x"]),
                "y_m": int(r["y"]),
                "combat_power_pct": round(r["combat_power"], 1),
                "status": r["status"],
            })

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
