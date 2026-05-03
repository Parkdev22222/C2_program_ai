"""
ARMA3 → Colab 로컬 릴레이 스크립트

[로컬 PC에서 실행]
  pip install requests
  python relay.py --url https://xxxx-xx-xx-xx.ngrok-free.app --token YOUR_SECRET_TOKEN

[동작 방식]
  ARMA3 .rpt 로그 파일을 실시간 감시 → [C2AI_DATA] 라인 추출 → Colab 서버로 HTTP POST
"""

import argparse
import glob
import json
import logging
import os
import sys
import time
from pathlib import Path
from datetime import datetime

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

C2AI_PREFIX = "[C2AI_DATA]"


# ── ARMA3 RPT 로그 경로 탐색 ─────────────────────────────────────

def find_latest_rpt() -> str:
    """최신 ARMA3 .rpt 로그 파일 경로를 반환합니다."""
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Arma 3"),
        os.path.expanduser(r"~\AppData\Local\Arma 3"),
        r"C:\Users\Public\Documents\Arma 3",
    ]
    rpt_files = []
    for base in candidates:
        rpt_files.extend(glob.glob(os.path.join(base, "*.rpt")))

    if not rpt_files:
        raise FileNotFoundError(
            "ARMA3 .rpt 파일을 찾을 수 없습니다.\n"
            "--rpt 옵션으로 경로를 직접 지정하세요.\n"
            "예: python relay.py --rpt C:\\Users\\YourName\\AppData\\Local\\Arma 3\\arma3_xxx.rpt"
        )
    return max(rpt_files, key=os.path.getmtime)


# ── 데이터 파싱 ───────────────────────────────────────────────────

def parse_c2ai_line(line: str) -> dict | None:
    """[C2AI_DATA]{...} 라인을 파싱해서 dict 반환. 실패 시 None."""
    line = line.strip()
    idx = line.find(C2AI_PREFIX)
    if idx == -1:
        return None
    json_str = line[idx + len(C2AI_PREFIX):]
    if not json_str.startswith("{"):
        return None
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        log.debug(f"JSON 파싱 실패: {e}  raw={json_str[:80]}")
        return None


# ── Colab 서버 전송 ───────────────────────────────────────────────

def send_to_colab(url: str, token: str, data: dict, timeout: int = 10) -> bool:
    """Colab FastAPI 서버로 데이터 POST 전송."""
    endpoint = url.rstrip("/") + "/arma3/report"
    try:
        resp = requests.post(
            endpoint,
            json=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            log.info(f"전송 성공: mission_time={data.get('t')}  units={len(data.get('units', []))}")
            return True
        log.warning(f"서버 응답 오류: {resp.status_code} {resp.text[:80]}")
        return False
    except requests.exceptions.ConnectionError:
        log.error(f"서버 연결 실패: {endpoint}")
        return False
    except Exception as e:
        log.error(f"전송 오류: {e}")
        return False


# ── 파일 테일 ─────────────────────────────────────────────────────

def tail_and_relay(rpt_path: str, colab_url: str, token: str, poll_interval: float = 0.5):
    """RPT 파일을 실시간 감시하며 C2AI 데이터를 Colab으로 전송합니다."""
    log.info(f"감시 파일: {rpt_path}")
    log.info(f"Colab URL: {colab_url}")

    # 파일 끝으로 이동 (기존 데이터 스킵)
    with open(rpt_path, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, 2)
        log.info("기존 로그 건너뜀 — 새 데이터부터 감시 시작")

        sent_count = 0
        while True:
            line = f.readline()
            if not line:
                time.sleep(poll_interval)
                continue

            data = parse_c2ai_line(line)
            if data is None:
                continue

            # 수신 시각 추가
            data["received_at"] = datetime.utcnow().isoformat()
            if send_to_colab(colab_url, token, data):
                sent_count += 1
                if sent_count % 10 == 0:
                    log.info(f"누적 전송 횟수: {sent_count}")


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ARMA3 → Colab 데이터 릴레이")
    parser.add_argument("--url",   required=True,
                        help="Colab ngrok URL (예: https://xxxx.ngrok-free.app)")
    parser.add_argument("--token", required=True,
                        help="인증 토큰 (Colab 서버와 동일한 값)")
    parser.add_argument("--rpt",   default="",
                        help="ARMA3 .rpt 파일 경로 (미지정 시 자동 탐색)")
    parser.add_argument("--poll",  type=float, default=0.5,
                        help="파일 폴링 간격(초), 기본 0.5")
    args = parser.parse_args()

    rpt_path = args.rpt or find_latest_rpt()
    log.info(f"RPT 경로: {rpt_path}")

    try:
        tail_and_relay(rpt_path, args.url, args.token, args.poll)
    except KeyboardInterrupt:
        log.info("릴레이 종료")


if __name__ == "__main__":
    main()
