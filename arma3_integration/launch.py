"""
C2AI 통합 런처 — 시나리오명 하나로 전체 시스템 자동 시작

[실행 예시]
  python launch.py --scenario bn_vs_bn --url https://xxxx.ngrok-free.app --token TOKEN

[동작 순서]
  1. scenarios.yaml에서 시나리오 설정 로드
  2. ARMA3 실행 파일 자동 탐색
  3. 미션 폴더 자동 탐색
  4. ARMA3 프로세스 실행
  5. 새 .rpt 로그 파일 감지 대기 (최대 120초)
  6. relay.py 시작 (전장 데이터 업로드 + 임무 명령 다운로드)
"""

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# 같은 패키지 내 모듈 임포트 (단독 실행 지원)
_THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(_THIS_DIR))
sys.path.insert(0, str(_THIS_DIR.parent))

from arma3_launcher import Arma3Launcher
import relay as relay_module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ── relay 스레드 래퍼 ─────────────────────────────────────────────

def _run_relay(rpt_path: str, colab_url: str, token: str,
               mission_dir: str, poll: float, order_poll: float):
    """relay.py의 핵심 루프를 스레드로 실행합니다."""
    # 임무 명령 폴링 스레드 (mission_dir 있을 때만)
    if mission_dir:
        t = threading.Thread(
            target=relay_module.poll_and_apply_orders,
            args=(colab_url, token, mission_dir, order_poll),
            daemon=True,
            name="OrderPoller",
        )
        t.start()
        log.info(f"임무 명령 폴링 시작 → {mission_dir}")

    # 전장 데이터 업로드 루프 (블로킹)
    relay_module.tail_and_relay(rpt_path, colab_url, token, poll)


# ── 메인 ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="C2AI 통합 런처 — 시나리오명만 지정하면 ARMA3 자동 실행 + relay 시작",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예:
  python launch.py --scenario bn_vs_bn --url https://xxxx.ngrok-free.app --token my_token
  python launch.py --scenario custom --mission-name MyMission --url ... --token ...
  python launch.py --list-scenarios
        """,
    )

    # 필수/핵심 인수
    parser.add_argument("--scenario", default="",
                        help="실행할 시나리오 이름 (scenarios.yaml에 등록된 키)")
    parser.add_argument("--url", default="",
                        help="Colab ngrok URL (예: https://xxxx.ngrok-free.app)")
    parser.add_argument("--token", default="",
                        help="인증 토큰 (Colab 서버와 동일한 값)")

    # 선택 인수
    parser.add_argument("--list-scenarios", action="store_true",
                        help="등록된 시나리오 목록 출력 후 종료")
    parser.add_argument("--find-rpt", action="store_true",
                        help="RPT 파일 탐색 결과 출력 후 종료 (진단용)")
    parser.add_argument("--exe", default="",
                        help="ARMA3 exe 경로 직접 지정 (자동 탐색 생략)")
    parser.add_argument("--mission-dir", default="",
                        help="미션 폴더 절대 경로 직접 지정 (자동 탐색 생략)")
    parser.add_argument("--mission-name", default="",
                        help="미션 이름 직접 지정 (custom 시나리오 사용 시)")
    parser.add_argument("--rpt", default="",
                        help="ARMA3 .rpt 파일 경로 직접 지정 (자동 탐색 생략)")
    parser.add_argument("--rpt-wait", type=int, default=120,
                        help=".rpt 파일 대기 최대 시간(초), 기본 120")
    parser.add_argument("--poll", type=float, default=0.5,
                        help="RPT 파일 폴링 간격(초), 기본 0.5")
    parser.add_argument("--order-poll", type=float, default=5.0,
                        help="임무 명령 폴링 간격(초), 기본 5.0")
    parser.add_argument("--no-launch", action="store_true",
                        help="ARMA3 실행 없이 relay만 시작 (이미 ARMA3가 실행 중인 경우)")

    args = parser.parse_args()

    launcher = Arma3Launcher()

    # ── RPT 파일 진단 ─────────────────────────────────────────────
    if args.find_rpt:
        from datetime import datetime
        print("\n[RPT 파일 탐색 결과]")
        files = relay_module.find_all_rpt_files()
        if files:
            for f in files:
                mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S")
                size  = os.path.getsize(f)
                print(f"  {mtime}  {size:>10,}B  {f}")
            print(f"\n→ 가장 최신 파일: {files[0]}")
            print("\n사용 예:")
            print(f"  python relay.py --url ... --token ... --rpt \"{files[0]}\"")
        else:
            print("  .rpt 파일을 찾을 수 없습니다.")
            print("\n[탐색한 경로 패턴]")
            for p in relay_module._rpt_search_patterns():
                print(f"  {p}")
            print("\n[주의] RPT 파일은 ARMA3가 실행되어야 생성됩니다.")
            print("  1. CrossOver에서 ARMA3를 실행하세요")
            print("  2. 메인 메뉴까지 진입하세요 (로딩 완료 후 .rpt 생성됨)")
            print("  3. python launch.py --find-rpt  다시 실행하세요")
        sys.exit(0)

    # ── 시나리오 목록 출력 ────────────────────────────────────────
    if args.list_scenarios:
        print("\n등록된 시나리오 목록:")
        print("-" * 60)
        for key, info in launcher.list_scenarios().items():
            print(f"  {key:<20}  {info['display_name']}")
            print(f"  {'':20}  {info['description']}")
            print(f"  {'':20}  맵: {info['world']}")
            print()
        sys.exit(0)

    # ── 필수 인수 확인 ────────────────────────────────────────────
    if not args.scenario:
        parser.error("--scenario 옵션이 필요합니다. --list-scenarios로 목록 확인 가능.")
    if not args.url or not args.token:
        parser.error("--url 과 --token 옵션이 필요합니다.")

    scenario     = launcher.get_scenario(args.scenario)
    display_name = scenario.get("display_name", args.scenario)

    # 미션 폴더 결정 (relay의 --mission-dir로 사용)
    mission_dir = args.mission_dir
    if not mission_dir and not args.no_launch:
        try:
            mission_dir = launcher.resolve_mission_dir(
                scenario,
                override_dir=args.mission_dir,
            )
        except (FileNotFoundError, ValueError) as e:
            log.warning(f"미션 폴더 탐색 실패: {e}")
            log.warning("임무 명령 수신 기능이 비활성화됩니다.")
            mission_dir = ""

    log.info("=" * 60)
    log.info(f"C2AI 통합 런처 시작")
    log.info(f"시나리오: {display_name}")
    log.info(f"Colab URL: {args.url}")
    log.info(f"미션 폴더: {mission_dir or '(미설정)'}")
    log.info("=" * 60)

    # ── ARMA3 실행 ───────────────────────────────────────────────
    arma3_proc = None
    if not args.no_launch:
        log.info("ARMA3 실행 중...")
        try:
            arma3_proc = launcher.launch(
                scenario_name=args.scenario,
                override_exe=args.exe,
                override_mission_dir=args.mission_dir,
                override_mission_name=args.mission_name,
            )
        except (FileNotFoundError, KeyError, ValueError) as e:
            log.error(f"ARMA3 실행 실패: {e}")
            sys.exit(1)

        log.info("ARMA3 프로세스 실행 완료. .rpt 파일 생성 대기 중...")
    else:
        log.info("--no-launch: ARMA3 실행 건너뜀 (relay만 시작)")

    # ── .rpt 파일 확보 ────────────────────────────────────────────
    rpt_path = args.rpt
    if not rpt_path and not args.no_launch:
        rpt_path = launcher.wait_for_rpt(timeout=args.rpt_wait)
        if rpt_path is None:
            log.error(
                f".rpt 파일을 {args.rpt_wait}초 안에 찾지 못했습니다.\n"
                "ARMA3가 정상적으로 시작되었는지 확인하거나 --rpt 옵션으로 직접 지정하세요."
            )
            sys.exit(1)
    elif not rpt_path and args.no_launch:
        # ARMA3가 이미 실행 중인 경우 최신 .rpt 파일 자동 탐색
        try:
            rpt_path = relay_module.find_latest_rpt()
            log.info(f"최신 .rpt 파일 사용: {rpt_path}")
        except FileNotFoundError as e:
            log.error(str(e))
            sys.exit(1)

    # ── relay 시작 ────────────────────────────────────────────────
    log.info(f"relay 시작: rpt={rpt_path}")
    log.info(f"  전장 데이터 업로드: 활성화")
    log.info(f"  임무 명령 수신: {'활성화' if mission_dir else '비활성화'}")

    relay_thread = threading.Thread(
        target=_run_relay,
        args=(rpt_path, args.url, args.token, mission_dir, args.poll, args.order_poll),
        daemon=False,
        name="RelayMain",
    )
    relay_thread.start()

    # ── 종료 대기 ─────────────────────────────────────────────────
    try:
        if arma3_proc is not None:
            log.info("ARMA3 종료 시 relay도 자동 종료됩니다. (Ctrl+C로 강제 종료)")
            if sys.platform == "darwin":
                # macOS: open -a는 즉시 리턴 → pgrep으로 실제 프로세스 감시
                log.info("macOS: ARMA3 프로세스 감시 중 (pgrep)...")
                time.sleep(15)  # ARMA3 기동 시간 대기
                while True:
                    r = subprocess.run(
                        ["pgrep", "-i", "arma3"],
                        capture_output=True,
                    )
                    if r.returncode != 0:
                        break
                    time.sleep(10)
            else:
                arma3_proc.wait()
            log.info("ARMA3 종료됨. 5초 후 relay 종료...")
            time.sleep(5)
        else:
            relay_thread.join()
    except KeyboardInterrupt:
        log.info("사용자 중단 (Ctrl+C)")
    finally:
        if arma3_proc and arma3_proc.poll() is None:
            log.info("ARMA3 프로세스 종료 중...")
            arma3_proc.terminate()
        log.info("C2AI 런처 종료")


if __name__ == "__main__":
    main()
