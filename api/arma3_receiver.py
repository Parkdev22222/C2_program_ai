"""
ARMA3 데이터 수신 FastAPI 엔드포인트

[Colab 실행 예시]
  import uvicorn, threading
  from api.arma3_receiver import app, set_auth_token

  set_auth_token("YOUR_SECRET_TOKEN")
  threading.Thread(target=uvicorn.run, kwargs={"app": app, "host": "0.0.0.0", "port": 8765}, daemon=True).start()

  # ngrok 터널 열기
  from pyngrok import ngrok
  tunnel = ngrok.connect(8765)
  print("ngrok URL:", tunnel.public_url)

[로컬 relay.py 실행]
  python relay.py --url https://xxxx.ngrok-free.app --token YOUR_SECRET_TOKEN
"""
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

app = FastAPI(title="C2 AI - ARMA3 Receiver")

_auth_token: Optional[str] = None
_security = HTTPBearer(auto_error=False)


def set_auth_token(token: str):
    """서버 기동 전에 인증 토큰을 설정합니다."""
    global _auth_token
    _auth_token = token
    logger.info("ARMA3 receiver auth token set")


def _verify_token(credentials: HTTPAuthorizationCredentials = Depends(_security)):
    if _auth_token is None:
        raise HTTPException(status_code=503, detail="서버 토큰이 설정되지 않았습니다")
    if credentials is None or credentials.credentials != _auth_token:
        raise HTTPException(status_code=401, detail="인증 실패")
    return credentials.credentials


@app.post("/arma3/report")
async def receive_arma3_report(request: Request, token: str = Depends(_verify_token)):
    """
    relay.py에서 POST된 ARMA3 전장 보고 데이터를 수신하여 DB에 저장합니다.

    Body (JSON):
      {
        "t": int,           # 미션 경과 시간(초)
        "units": [...],
        "groups": [...],
        "summary": {...},
        "received_at": str
      }
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON 파싱 실패")

    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="dict 형태의 JSON이 필요합니다")

    try:
        from core_src.arma3_db_manager import save_report
        save_report(data)
    except Exception as e:
        logger.error(f"ARMA3 보고 저장 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "status": "ok",
        "mission_time": data.get("t"),
        "units_received": len(data.get("units", [])),
    }


@app.get("/arma3/status")
async def get_arma3_status(token: str = Depends(_verify_token)):
    """현재 저장된 ARMA3 상태 요약을 반환합니다."""
    from core_src.arma3_db_manager import load_state
    state = load_state()
    return {
        "status": "ok",
        "last_updated": state.get("last_updated", ""),
        "mission_time": state.get("mission_time", 0),
        "unit_count": len(state.get("units", [])),
        "group_count": len(state.get("groups", [])),
        "summary": state.get("summary", {}),
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}
