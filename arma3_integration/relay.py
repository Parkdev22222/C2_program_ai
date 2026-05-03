"""
ARMA3 ↔ Colab 양방향 릴레이 스크립트

[로컬 PC에서 실행]
  pip install requests
  python relay.py --url https://xxxx-xx-xx-xx.ngrok-free.app --token YOUR_SECRET_TOKEN

[추가: 임무 명령 수신 활성화]
  python relay.py --url https://... --token TOKEN --mission-dir "C:\\...\\missions\\MyMission.Altis"

[macOS CrossOver — 로그 파일 위치 확인]
  python relay.py --find-log          ← 로그 파일(rpt/dat) 자동 탐색 결과 출력

[동작 방식]
  ① 업로드: ARMA3 로그 파일(.rpt 또는 .dat) 감시 → [C2AI_DATA] 라인 추출 → Colab 서버로 HTTP POST
  ② 다운로드: Colab에서 에이전트가 발행한 임무 명령 폴링 → SQF 파일 생성 → 미션 폴더 저장
              ARMA3의 c2_order_executor.sqf가 SQF 파일을 자동 감지하여 실행

[로그 파일 확장자 참고]
  Windows/CrossOver(Wine): 일반적으로 .rpt (arma3_YYYY-MM-DD_HH-MM-SS.rpt)
  macOS CrossOver 일부 버전: .rpt 또는 .dat 로 생성될 수 있음
  --log 옵션으로 직접 경로 지정 가능 (확장자 무관)
"""

import argparse
import glob
import json
import logging
import os
import sys
import threading
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

# macOS CrossOver에서 .rpt 외에 .dat 확장자로 생성되는 경우도 있음
_LOG_EXTENSIONS = ("*.rpt", "*.dat")


# ── ARMA3 로그 경로 탐색 ──────────────────────────────────────────

def _log_search_patterns() -> list:
    """플랫폼별 ARMA3 로그 파일 glob 패턴 목록을 반환합니다 (.rpt + .dat)."""
    home = str(Path.home())

    if sys.platform == "win32":
        patterns = []
        for ext in _LOG_EXTENSIONS:
            patterns += [
                os.path.join(os.path.expandvars(r"%LOCALAPPDATA%"), "Arma 3", ext),
                os.path.join(home, "AppData", "Local", "Arma 3", ext),
            ]
        return patterns

    if sys.platform == "darwin":
        crossover_base = os.path.join(home, "Library", "Application Support", "CrossOver", "Bottles")
        patterns = []
        for ext in _LOG_EXTENSIONS:
            patterns += [
                # CrossOver — AppData\Local\Arma 3 (Vista+ 경로)
                os.path.join(crossover_base, "*", "drive_c", "users", "*",
                             "AppData", "Local", "Arma 3", ext),
                os.path.join(crossover_base, "*", "drive_c", "Users", "*",
                             "AppData", "Local", "Arma 3", ext),
                # CrossOver — XP 스타일 경로 (Local Settings\Application Data)
                os.path.join(crossover_base, "*", "drive_c", "users", "*",
                             "Local Settings", "Application Data", "Arma 3", ext),
                os.path.join(crossover_base, "*", "drive_c", "Users", "*",
                             "Local Settings", "Application Data", "Arma 3", ext),
                # CrossOver — 게임 설치 폴더 내 로그 (일부 버전)
                os.path.join(crossover_base, "*", "drive_c", "Program Files (x86)",
                             "Steam", "steamapps", "common", "Arma 3", ext),
                os.path.join(crossover_base, "*", "drive_c", "Program Files",
                             "Steam", "steamapps", "common", "Arma 3", ext),
                # CrossOver — drive_c 전체 재귀 탐색 (위치 불명확할 때 대비)
                os.path.join(crossover_base, "*", "drive_c", "**", "Arma 3", ext),
                # macOS 네이티브 (구버전 Steam for Mac)
                os.path.join(home, "Library", "Logs", "Arma 3", ext),
                os.path.join(home, "Library", "Application Support", "Arma 3", ext),
                # Parallels shared folder
                os.path.join("/Volumes", "*", "Users", "*", "AppData", "Local", "Arma 3", ext),
            ]
        return patterns

    # Linux
    patterns = []
    for ext in _LOG_EXTENSIONS:
        patterns += [
            os.path.join(home, ".local", "share", "Arma 3", ext),
            os.path.join(home, ".steam", "steam", "steamapps", "common", "Arma 3", ext),
        ]
    return patterns


# 하위 호환 별칭
_rpt_search_patterns = _log_search_patterns


def find_latest_rpt() -> str:
    """최신 ARMA3 로그 파일 경로를 반환합니다 (.rpt 또는 .dat)."""
    log_files = []
    for pattern in _log_search_patterns():
        recursive = "**" in pattern
        log_files.extend(glob.glob(pattern, recursive=recursive))

    if not log_files:
        if sys.platform == "darwin":
            hint = (
                "\n[참고] 로그 파일은 ARMA3가 실행된 이후 생성됩니다.\n"
                "ARMA3를 한 번 실행 후 메인 메뉴까지 진입한 뒤 다시 시도하세요.\n\n"
                "로그 파일 위치 확인 방법:\n"
                "  python launch.py --find-rpt\n\n"
                "직접 경로 지정 (.rpt 또는 .dat 모두 가능):\n"
                "  python relay.py --url ... --token ... "
                "--rpt ~/Library/Application\\ Support/CrossOver/Bottles/[병이름]/"
                "drive_c/Users/crossover/AppData/Local/Arma\\ 3/arma3_xxx.rpt"
            )
        else:
            hint = (
                "\n직접 경로 지정:\n"
                "  python relay.py --rpt /path/to/arma3_xxx.rpt"
            )
        raise FileNotFoundError(f"ARMA3 로그 파일(.rpt/.dat)을 찾을 수 없습니다.{hint}")

    return max(log_files, key=os.path.getmtime)


def find_all_rpt_files() -> list:
    """진단용: 탐색 가능한 모든 ARMA3 로그 파일 목록을 반환합니다 (.rpt + .dat)."""
    log_files = []
    for pattern in _log_search_patterns():
        recursive = "**" in pattern
        log_files.extend(glob.glob(pattern, recursive=recursive))
    return sorted(set(log_files), key=os.path.getmtime, reverse=True)


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

            data["received_at"] = datetime.utcnow().isoformat()
            if send_to_colab(colab_url, token, data):
                sent_count += 1
                if sent_count % 10 == 0:
                    log.info(f"누적 전송 횟수: {sent_count}")


# ── SQF 변환 ─────────────────────────────────────────────────────

def _sqf_formation(f: str) -> str:
    return {
        "wedge": "WEDGE", "line": "LINE", "column": "COLUMN",
        "echelon_left": "ECHELON LEFT", "echelon_right": "ECHELON RIGHT",
        "vee": "VEE", "diamond": "DIAMOND", "stag_column": "STAG COLUMN", "file": "FILE",
    }.get(f.lower(), "WEDGE")


def _sqf_behavior(s: str) -> str:
    return {
        "safe": "SAFE", "aware": "AWARE", "combat": "COMBAT", "stealth": "STEALTH",
        "attack": "COMBAT", "defend": "AWARE", "recon": "STEALTH",
    }.get(s.lower(), "COMBAT")


def _sqf_speed(s: str) -> str:
    return {
        "safe": "LIMITED", "aware": "NORMAL", "combat": "FULL", "stealth": "LIMITED",
        "slow": "LIMITED", "normal": "NORMAL", "fast": "FULL",
    }.get(s.lower(), "FULL")


def _sqf_wp_type(a: str) -> str:
    return {
        "move": "MOVE", "attack": "ATTACK", "defend": "HOLD",
        "hold": "HOLD", "support_by_fire": "HOLD", "assault": "ATTACK",
        "recon": "MOVE", "withdrawal": "MOVE", "support": "HOLD",
    }.get(a.lower(), "MOVE")


def orders_to_sqf(order: dict) -> str:
    """JSON 임무 명령을 ARMA3 SQF 스크립트로 변환합니다."""
    seq = order.get("seq", 0)
    lines = [
        f"// C2AI 중대 임무 명령 #{seq}",
        f"// 발령 시각: {order.get('issued_at', '')}",
        f"// 시나리오: {order.get('scenario', '')}",
        f"// 전술 의도: {order.get('tactical_intent', '')}",
        f"// 자동 생성 — 수동 편집 금지 (다음 명령 수신 시 덮어씌워짐)",
        "",
        f'diag_log "[C2AI] 임무 명령 #{seq} 적용 시작";',
        "",
    ]

    for company in order.get("companies", []):
        cid = company.get("company_id", "Unknown")
        # SQF 변수명에 사용할 수 있는 안전한 이름 (숫자로 시작 금지, 공백 제거)
        safe_cid = "".join(c if c.isalnum() or c == "_" else "_" for c in cid)
        if safe_cid and safe_cid[0].isdigit():
            safe_cid = "C_" + safe_cid

        formation = _sqf_formation(company.get("formation", "wedge"))
        behavior  = _sqf_behavior(company.get("speed", "combat"))
        speed     = _sqf_speed(company.get("speed", "combat"))
        waypoints = company.get("waypoints", [])
        notes     = company.get("notes", "").replace('"', "'")
        side      = company.get("side", "")
        mission   = company.get("mission_type", "")

        lines += [
            f"// ──── {cid} 중대  |  진영: {side}  |  임무: {mission} ────",
            f"private _grp_{safe_cid} = grpNull;",
            f'{{if (groupId _x == "{cid}") exitWith {{_grp_{safe_cid} = _x}}}} forEach allGroups;',
            f"if (!isNull _grp_{safe_cid}) then {{",
            f"    // 기존 웨이포인트 초기화",
            f"    while {{count (waypoints _grp_{safe_cid}) > 0}} do {{",
            f"        deleteWaypoint [_grp_{safe_cid}, 0];",
            f"    }};",
            f'    _grp_{safe_cid} setFormation "{formation}";',
            f'    _grp_{safe_cid} setSpeedMode "{speed}";',
            f'    {{_x setBehaviour "{behavior}"}} forEach units _grp_{safe_cid};',
            "",
        ]

        for wp in waypoints:
            x       = wp.get("x", 0)
            y       = wp.get("y", 0)
            radius  = wp.get("radius", 50)
            wp_type = _sqf_wp_type(wp.get("action", "move"))
            desc    = str(wp.get("notes", f"WP{wp.get('seq', '')}")).replace('"', "'")
            hold    = int(wp.get("hold_time_sec", 0))

            lines += [
                f"    private _wp = _grp_{safe_cid} addWaypoint [[{x}, {y}, 0], {radius}];",
                f'    _wp setWaypointType "{wp_type}";',
                f'    _wp setWaypointDescription "{desc}";',
            ]
            if hold > 0:
                lines.append(f"    _wp setWaypointTimeout [{hold}, {hold}, {hold}];")
            lines.append("")

        lines += [
            f'    diag_log "[C2AI] {cid} 임무 적용 완료: {notes}";',
            "} else {",
            f'    diag_log "[C2AI] 경고: {cid} 그룹을 찾을 수 없습니다 (groupId 확인 필요)";',
            "};",
            "",
        ]

    lines.append(f'diag_log "[C2AI] 임무 명령 #{seq} 적용 완료";')
    return "\n".join(lines)


# ── 임무 명령 폴링 (Colab → ARMA3) ───────────────────────────────

def poll_and_apply_orders(colab_url: str, token: str, mission_dir: str, poll_interval: float = 5.0):
    """
    Colab에서 대기 중인 임무 명령을 주기적으로 가져와
    mission_dir에 c2ai_order_N.sqf 파일로 저장합니다.
    ARMA3의 c2_order_executor.sqf가 해당 파일을 자동으로 감지하여 실행합니다.
    """
    mission_path = Path(mission_dir)
    if not mission_path.exists():
        log.warning(f"미션 폴더가 없습니다. 생성: {mission_dir}")
        mission_path.mkdir(parents=True, exist_ok=True)

    log.info(f"임무 명령 폴링 시작 (간격: {poll_interval}s) → {mission_dir}")
    endpoint_pending = colab_url.rstrip("/") + "/arma3/orders/pending"
    endpoint_ack     = colab_url.rstrip("/") + "/arma3/orders/ack"
    headers = {"Authorization": f"Bearer {token}"}

    while True:
        try:
            resp = requests.get(endpoint_pending, headers=headers, timeout=10)
            if resp.status_code == 200:
                orders = resp.json().get("orders", [])
                if orders:
                    acked_ids = []
                    for order in orders:
                        seq = order.get("seq", 0)
                        sqf_content = orders_to_sqf(order)
                        sqf_path = mission_path / f"c2ai_order_{seq}.sqf"
                        sqf_path.write_text(sqf_content, encoding="utf-8")
                        log.info(f"명령 SQF 저장: {sqf_path.name}  "
                                 f"companies={len(order.get('companies', []))}")
                        acked_ids.append(order["order_id"])

                    requests.post(
                        endpoint_ack,
                        json={"order_ids": acked_ids},
                        headers=headers,
                        timeout=10,
                    )
            elif resp.status_code not in (401, 503):
                log.debug(f"명령 폴링 응답: {resp.status_code}")
        except requests.exceptions.ConnectionError:
            log.debug("명령 폴링: 서버 연결 없음 (재시도 예정)")
        except Exception as e:
            log.error(f"명령 폴링 오류: {e}")

        time.sleep(poll_interval)


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ARMA3 ↔ Colab 양방향 데이터 릴레이")
    parser.add_argument("--url",         required=True,
                        help="Colab ngrok URL (예: https://xxxx.ngrok-free.app)")
    parser.add_argument("--token",       required=True,
                        help="인증 토큰 (Colab 서버와 동일한 값)")
    parser.add_argument("--rpt",         default="",
                        help="ARMA3 로그 파일 경로 (.rpt 또는 .dat, 미지정 시 자동 탐색)")
    parser.add_argument("--poll",        type=float, default=0.5,
                        help="로그 파일 폴링 간격(초), 기본 0.5")
    parser.add_argument("--mission-dir", default="",
                        help=(
                            "ARMA3 미션 폴더 경로 (임무 명령 수신 활성화).\n"
                            "예: C:\\Users\\NAME\\Documents\\Arma 3\\missions\\MyMission.Altis\n"
                            "지정 시 Colab에서 에이전트가 발행한 임무 명령을\n"
                            "c2ai_order_N.sqf 파일로 이 폴더에 저장합니다."
                        ))
    parser.add_argument("--order-poll",  type=float, default=5.0,
                        help="임무 명령 폴링 간격(초), 기본 5.0")
    parser.add_argument("--find-rpt",   action="store_true",
                        help="로그 파일(.rpt/.dat) 탐색 결과만 출력하고 종료 (진단용)")
    args = parser.parse_args()

    # ── 진단 모드 ──────────────────────────────────────────────────
    if args.find_rpt:
        print("\n[ARMA3 로그 파일 탐색 결과 (.rpt / .dat)]")
        files = find_all_rpt_files()
        if files:
            for f in files:
                mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S")
                size  = os.path.getsize(f)
                ext   = os.path.splitext(f)[1]
                print(f"  {mtime}  {size:>10,}B  [{ext}]  {f}")
            print(f"\n→ 가장 최신 파일: {files[0]}")
            print(f"\n사용 예:")
            print(f"  python relay.py --url ... --token ... --rpt \"{files[0]}\"")
        else:
            print("  로그 파일(.rpt/.dat)을 찾을 수 없습니다.")
            print("\n[탐색한 경로 패턴]")
            for p in _log_search_patterns():
                print(f"  {p}")
            print("\n[주의] 로그 파일은 ARMA3가 실행되어야 생성됩니다.")
            print("  CrossOver에서 ARMA3를 실행 후 메인 메뉴까지 진입한 뒤 다시 시도하세요.")
        return

    rpt_path = args.rpt or find_latest_rpt()
    log.info(f"로그 파일 경로: {rpt_path}")

    # 임무 명령 수신 스레드 (--mission-dir 지정 시)
    if args.mission_dir:
        order_thread = threading.Thread(
            target=poll_and_apply_orders,
            args=(args.url, args.token, args.mission_dir, args.order_poll),
            daemon=True,
            name="OrderPoller",
        )
        order_thread.start()
        log.info(f"임무 명령 수신 활성화: {args.mission_dir}")
    else:
        log.info("--mission-dir 미지정 → 임무 명령 수신 비활성화 (전장 데이터 업로드만 동작)")

    try:
        tail_and_relay(rpt_path, args.url, args.token, args.poll)
    except KeyboardInterrupt:
        log.info("릴레이 종료")


if __name__ == "__main__":
    main()
