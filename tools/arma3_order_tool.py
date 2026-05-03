"""
ARMA3 임무 명령 송신 도구

에이전트가 생성한 중대 단위 임무 경로 JSON을 ARMA3로 전송합니다.
relay.py가 Colab에서 폴링하여 로컬 PC의 ARMA3 미션 폴더에 SQF 파일로 저장합니다.

━━━━ 임무 명령 JSON 스키마 ━━━━
{
  "scenario": "기계화 보병 대대 vs 대대",
  "friendly_side": "BLUFOR",
  "tactical_intent": "전체 전술 의도 설명",
  "companies": [
    {
      "company_id": "Alpha",               // ARMA3 groupId와 일치해야 함
      "side": "BLUFOR",                    // "BLUFOR" | "OPFOR" | "INDEP"
      "mission_type": "attack",            // "attack"|"defend"|"flank"|"support"|"withdrawal"|"recon"
      "formation": "wedge",                // "wedge"|"line"|"column"|"echelon_left"|"echelon_right"|"vee"|"diamond"
      "speed": "combat",                   // "safe"|"aware"|"combat"|"stealth"
      "waypoints": [
        {
          "seq": 1,
          "x": 1234.5,                     // ARMA3 ASL 좌표 (동쪽, 미터)
          "y": 5678.9,                     // ARMA3 ASL 좌표 (북쪽, 미터)
          "action": "move",                // "move"|"attack"|"defend"|"hold"|"support_by_fire"|"assault"
          "radius": 50,                    // 웨이포인트 반경(미터)
          "hold_time_sec": 0,              // 대기 시간(초), 0이면 즉시 이동
          "notes": "1번 집결지로 이동"
        }
      ],
      "notes": "북방 우회 기동 후 고지 공격"
    }
  ]
}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import json
import logging
from smolagents import tool

logger = logging.getLogger(__name__)


@tool
def send_mission_orders_to_arma3(mission_orders_json: str) -> dict:
    """
    생성된 전략/전술 임무 경로를 ARMA3로 전송합니다.
    relay.py가 실행 중인 로컬 PC의 ARMA3 미션 폴더에 SQF 파일로 자동 저장됩니다.

    이 도구는 기계화 보병 대대 vs 대대 시나리오에서
    중대 단위 임무 경로를 ARMA3가 직접 실행할 수 있는 형태로 변환합니다.

    Args:
        mission_orders_json: 중대별 임무 경로를 담은 JSON 문자열.
            스키마:
            {
              "scenario": str,           # 시나리오 설명
              "friendly_side": str,      # "BLUFOR" | "OPFOR"
              "tactical_intent": str,    # 전체 작전 의도
              "companies": [
                {
                  "company_id": str,     # ARMA3 groupId (예: "Alpha", "Bravo")
                  "side": str,           # "BLUFOR" | "OPFOR" | "INDEP"
                  "mission_type": str,   # "attack"|"defend"|"flank"|"support"|"withdrawal"|"recon"
                  "formation": str,      # "wedge"|"line"|"column"|"echelon_left"|"echelon_right"
                  "speed": str,          # "safe"|"aware"|"combat"|"stealth"
                  "waypoints": [
                    {
                      "seq": int,
                      "x": float,        # ARMA3 ASL 동쪽 좌표(m)
                      "y": float,        # ARMA3 ASL 북쪽 좌표(m)
                      "action": str,     # "move"|"attack"|"defend"|"hold"|"assault"
                      "radius": int,     # 웨이포인트 반경(m), 기본 50
                      "hold_time_sec": int,
                      "notes": str
                    }
                  ],
                  "notes": str
                }
              ]
            }

    Returns:
        {
            "status": "ok" | "error",
            "order_id": str,
            "seq": int,
            "companies_count": int,
            "message": str
        }
    """
    try:
        orders = json.loads(mission_orders_json)
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"JSON 파싱 실패: {e}"}

    if not isinstance(orders, dict):
        return {"status": "error", "message": "dict 형태의 JSON이 필요합니다"}

    companies = orders.get("companies", [])
    if not companies:
        return {"status": "error", "message": "companies 항목이 비어있습니다"}

    try:
        from core_src.arma3_order_manager import save_order
        saved = save_order(orders)
    except Exception as e:
        logger.error(f"임무 명령 저장 실패: {e}")
        return {"status": "error", "message": str(e)}

    return {
        "status": "ok",
        "order_id": saved["order_id"],
        "seq": saved["seq"],
        "companies_count": len(companies),
        "company_ids": [c.get("company_id") for c in companies],
        "message": (
            f"임무 명령 #{saved['seq']} 저장 완료. "
            f"relay.py가 실행 중이면 ARMA3 미션 폴더에 "
            f"c2ai_order_{saved['seq']}.sqf 파일이 생성됩니다."
        ),
    }


@tool
def get_arma3_order_status() -> dict:
    """
    최근 ARMA3 임무 명령 목록과 전달 상태를 반환합니다.

    Returns:
        {
            "status": "ok",
            "orders": [
                {
                    "order_id": str,
                    "seq": int,
                    "issued_at": str,
                    "status": "pending" | "delivered",
                    "companies_count": int,
                    "tactical_intent": str
                }, ...
            ],
            "count": int
        }
    """
    try:
        from core_src.arma3_order_manager import list_all_orders
        orders = list_all_orders(limit=10)
        summary = [
            {
                "order_id": o.get("order_id"),
                "seq": o.get("seq"),
                "issued_at": o.get("issued_at"),
                "status": o.get("status"),
                "companies_count": len(o.get("companies", [])),
                "tactical_intent": o.get("tactical_intent", ""),
            }
            for o in reversed(orders)
        ]
        return {"status": "ok", "orders": summary, "count": len(summary)}
    except Exception as e:
        return {"status": "error", "message": str(e)}
