"""
ARMA3 임무 명령 DB 매니저

에이전트가 생성한 중대 단위 임무 경로 JSON을 저장하고,
relay.py가 폴링하여 ARMA3로 전달합니다.
"""
import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

ARMA3_ORDERS_FILE = Path(__file__).parent.parent / "data" / "arma3_orders.json"
_lock = threading.Lock()


def _load_orders_db() -> dict:
    if ARMA3_ORDERS_FILE.exists():
        try:
            with open(ARMA3_ORDERS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"arma3_orders.json 읽기 실패: {e}")
    return {"current_seq": 0, "orders": []}


def _save_orders_db(db: dict):
    ARMA3_ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ARMA3_ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def save_order(order: dict) -> dict:
    """
    에이전트가 생성한 임무 명령을 DB에 저장합니다.

    order dict에 order_id, seq, issued_at, status 필드가 추가됩니다.
    Returns: 저장된 order (order_id, seq 포함)
    """
    with _lock:
        db = _load_orders_db()
        db["current_seq"] += 1
        seq = db["current_seq"]

        order = dict(order)
        order["order_id"] = str(uuid.uuid4())[:8]
        order["seq"] = seq
        order["issued_at"] = order.get("issued_at") or datetime.utcnow().isoformat()
        order["status"] = "pending"

        db["orders"].append(order)
        # 최근 100개만 보관
        db["orders"] = db["orders"][-100:]
        _save_orders_db(db)

    logger.info(f"임무 명령 저장: seq={seq}  order_id={order['order_id']}  "
                f"companies={len(order.get('companies', []))}")
    return order


def get_pending_orders() -> List[dict]:
    """relay.py 폴링 시 전달되지 않은 대기 중 명령을 반환합니다."""
    with _lock:
        db = _load_orders_db()
        return [o for o in db.get("orders", []) if o.get("status") == "pending"]


def acknowledge_orders(order_ids: List[str]):
    """relay.py가 SQF 파일 저장 후 수신 완료를 표시합니다."""
    with _lock:
        db = _load_orders_db()
        acked = 0
        for o in db.get("orders", []):
            if o.get("order_id") in order_ids:
                o["status"] = "delivered"
                o["delivered_at"] = datetime.utcnow().isoformat()
                acked += 1
        _save_orders_db(db)
    logger.info(f"명령 수신 확인: {acked}개")


def list_all_orders(limit: int = 20) -> List[dict]:
    """최근 명령 목록을 반환합니다 (에이전트 조회용)."""
    with _lock:
        db = _load_orders_db()
        orders = db.get("orders", [])
        return orders[-limit:]
