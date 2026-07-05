"""
C2 군사 전략 AI - 메인 진입점

모델 아키텍처:
- EXAONE4 (EXAONE-4.0-32B-AWQ): 상황 판단·전략/전술 추천·임무계획 수립 메인 에이전트
  (vLLM 서버로 서빙 — scripts/launch_vllm_servers.py)

사용 예시:
  python main.py ui                          # HTML 대시보드 UI 실행 (기본 포트 7861)
  python main.py query --query "적 기갑 전술 추천"
  python main.py check-env                  # 환경 확인
"""
import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 모델 사전 로딩 (공유 인스턴스)
# ─────────────────────────────────────────────

def preload_models():
    """EXAONE4 vLLM 서버 클라이언트를 생성합니다.

    모델은 별도 프로세스의 vLLM 서버에서 동작합니다.
    서버 기동: python scripts/launch_vllm_servers.py
    """
    from agent.model_loader import load_model_from_config_file

    logger.info("=== vLLM 서버 클라이언트 초기화 시작 ===")
    logger.info("EXAONE4 (메인 에이전트 모델) 서버 연결 중...")
    exaone4_model = load_model_from_config_file()
    logger.info("EXAONE4 클라이언트 준비 완료")
    logger.info("=== vLLM 서버 클라이언트 초기화 완료 ===")
    return exaone4_model


# ─────────────────────────────────────────────
# 에이전트 초기화
# ─────────────────────────────────────────────

def init_agent(exaone4_model=None) -> "BattlefieldAgent":
    """BattlefieldAgent를 초기화합니다."""
    from agent.battlefield_agent import BattlefieldAgent
    logger.info("BattlefieldAgent 초기화 중...")
    agent = BattlefieldAgent(exaone4_model=exaone4_model)
    logger.info("BattlefieldAgent 초기화 완료")
    return agent


# ─────────────────────────────────────────────
# 커맨드: UI 실행
# ─────────────────────────────────────────────

def cmd_ui(args):
    """HTML 대시보드 UI (FastAPI + Leaflet)를 실행합니다."""
    logger.info("UI 모드 시작 — http://%s:%d", args.host, args.port)

    exaone4_model = preload_models()
    agent = init_agent(exaone4_model)

    from ui.web_api import start_server
    start_server(agent=agent, host=args.host, port=args.port)


# ─────────────────────────────────────────────
# 커맨드: 에이전트 쿼리 (CLI)
# ─────────────────────────────────────────────

def cmd_query(args):
    """에이전트에 단일 쿼리를 실행합니다."""
    exaone4_model = preload_models()
    agent = init_agent(exaone4_model)

    print(f"\n쿼리: {args.query}")
    print("=" * 60)

    response = agent.run(args.query)
    print(response)


# ─────────────────────────────────────────────
# 커맨드: 환경 확인
# ─────────────────────────────────────────────

def cmd_check_env(args):
    """시스템 환경을 확인합니다."""
    print("=== C2 군사 AI 환경 확인 ===\n")

    # CUDA
    try:
        import torch
        cuda_ok = torch.cuda.is_available()
        if cuda_ok:
            gpu_count = torch.cuda.device_count()
            gpu_names = [torch.cuda.get_device_name(i) for i in range(gpu_count)]
            print(f"[OK] CUDA 사용 가능 - GPU {gpu_count}개: {gpu_names}")
            for i in range(gpu_count):
                mem = torch.cuda.get_device_properties(i).total_memory / 1024**3
                print(f"     GPU {i}: {mem:.1f} GB VRAM")
        else:
            print("[WARN] CUDA 사용 불가 - CPU 모드로 동작 (성능 저하)")
    except ImportError:
        print("[FAIL] PyTorch 미설치")

    # smolagents
    try:
        import smolagents
        print(f"[OK] smolagents {smolagents.__version__}")
    except ImportError:
        print("[FAIL] smolagents 미설치")

    # openai SDK (vLLM 서버 클라이언트)
    try:
        import openai
        print(f"[OK] openai SDK {openai.__version__}")
    except ImportError:
        print("[FAIL] openai 미설치 - vLLM 서버 호출 불가 (pip install openai)")

    # vllm (서버 기동용 — 클라이언트 전용 머신에서는 없어도 됨)
    try:
        import vllm
        print(f"[OK] vllm {vllm.__version__} (서버 기동 가능)")
    except ImportError:
        print("[WARN] vllm 미설치 - 이 머신에서 vLLM 서버 기동 불가 (원격 서버 사용 시 무관)")

    # vLLM 서버 연결 확인
    def _check_vllm_server(name: str, base_url: str):
        import urllib.request
        health_url = base_url.rsplit("/v1", 1)[0] + "/health"
        try:
            with urllib.request.urlopen(health_url, timeout=3) as resp:
                ok = resp.status == 200
        except Exception:
            ok = False
        if ok:
            print(f"[OK] {name} vLLM 서버 연결됨: {base_url}")
        else:
            print(f"[WARN] {name} vLLM 서버 미응답: {base_url} "
                  f"(기동: python scripts/launch_vllm_servers.py)")

    try:
        from agent.vllm_client import resolve_base_url
        from agent.model_loader import (
            load_exaone_model_config, AGENT_BASE_URL_ENV, AGENT_DEFAULT_PORT,
        )
        agent_cfg = load_exaone_model_config()
        _check_vllm_server(
            "EXAONE4",
            resolve_base_url(agent_cfg.get("serving", {}), AGENT_BASE_URL_ENV, AGENT_DEFAULT_PORT),
        )
    except Exception as e:
        print(f"[WARN] vLLM 서버 설정 확인 실패: {e}")

    # 기타 패키지
    packages = ["gradio", "cv2", "numpy"]
    for pkg in packages:
        try:
            mod = __import__(pkg)
            ver = getattr(mod, "__version__", "?")
            print(f"[OK] {pkg} {ver}")
        except ImportError:
            print(f"[FAIL] {pkg} 미설치")

    # 디렉토리
    dirs = ["config", "core_src", "agent", "tools", "ui", "data"]
    print("\n--- 디렉토리 확인 ---")
    for d in dirs:
        p = Path(d)
        status = "OK" if p.exists() else "MISSING"
        print(f"[{status}] {d}/")

    print("\n환경 확인 완료.")


# ─────────────────────────────────────────────
# argparse 설정
# ─────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="c2_ai",
        description="C2 군사 전략 AI - EXAONE4 단일 모델 시스템",
    )
    subparsers = parser.add_subparsers(dest="command")

    # ui
    ui_p = subparsers.add_parser("ui", help="HTML 대시보드 UI 실행 (FastAPI + Leaflet)")
    ui_p.add_argument("--host", default="0.0.0.0")
    ui_p.add_argument("--port", type=int, default=7860)

    # query
    q_p = subparsers.add_parser("query", help="에이전트 쿼리 (CLI)")
    q_p.add_argument("--query", required=True, help="실행할 쿼리")

    # check-env
    subparsers.add_parser("check-env", help="시스템 환경 확인")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        if args.command == "ui":
            cmd_ui(args)
        elif args.command == "query":
            cmd_query(args)
        elif args.command == "check-env":
            cmd_check_env(args)
    except KeyboardInterrupt:
        logger.info("사용자에 의해 중단되었습니다.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"실행 오류: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
