"""
C2 군사 전략 AI - 메인 진입점

듀얼 모델 아키텍처:
- EXAONE4 (EXAONE-4.0-32B-AWQ): 영상 분석 및 상황 판단 메인 에이전트
- EXAONE Deep (EXAONE-Deep-32B): 전략/전술 전문 모델
  → EXAONE4가 군사 전략/전술 쿼리 감지 시 자동 호출
  → EXAONE4의 상황 분석 결과를 기반으로 전략/전술 권고 생성

사용 예시:
  python main.py ui                          # Gradio UI 실행
  python main.py analyze --video path.mp4   # 영상 분석
  python main.py query --query "적 기갑 전술 추천"
  python main.py check-env                  # 환경 확인
"""
import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 모델 사전 로딩 (공유 인스턴스)
# ─────────────────────────────────────────────

def preload_models(skip_strategy: bool = False):
    """EXAONE4와 EXAONE Deep을 순서대로 로드합니다."""
    from agent.model_loader import load_model_from_config_file
    from agent.strategy_model_loader import load_strategy_model_from_config_file

    logger.info("=== 모델 로딩 시작 ===")
    logger.info("EXAONE4 (메인 에이전트 모델) 로딩 중...")
    exaone4_model = load_model_from_config_file()
    logger.info("EXAONE4 로딩 완료")

    strategy_model = None
    if not skip_strategy:
        logger.info("EXAONE Deep (전략/전술 전문 모델) 로딩 중...")
        strategy_model = load_strategy_model_from_config_file()
        logger.info("EXAONE Deep 로딩 완료")

    logger.info("=== 모델 로딩 완료 ===")
    return exaone4_model, strategy_model


def preload_vision_models():
    """비전 ML 모델들(객체 탐지, 임베딩, 설명 생성)을 사전 로딩합니다."""
    from core_src.model_manager import ModelManager
    mm = ModelManager()
    mm.preload_all()
    return mm


# ─────────────────────────────────────────────
# 에이전트 초기화
# ─────────────────────────────────────────────

def init_agent(exaone4_model=None, strategy_model=None) -> "BattlefieldAgent":
    """BattlefieldAgent를 초기화합니다."""
    from agent.battlefield_agent import BattlefieldAgent
    logger.info("BattlefieldAgent 초기화 중...")
    agent = BattlefieldAgent(
        exaone4_model=exaone4_model,
        strategy_model=strategy_model,
    )
    logger.info("BattlefieldAgent 초기화 완료")
    return agent


# ─────────────────────────────────────────────
# 커맨드: UI 실행
# ─────────────────────────────────────────────

def cmd_ui(args):
    """Gradio 웹 인터페이스를 실행합니다."""
    logger.info("UI 모드 시작")

    exaone4_model, strategy_model = preload_models(skip_strategy=args.skip_strategy)
    agent = init_agent(exaone4_model, strategy_model)

    # 비전 모델 사전 로딩 (선택)
    if not args.skip_vision_preload:
        try:
            preload_vision_models()
        except Exception as e:
            logger.warning(f"비전 모델 사전 로딩 실패 (영상 업로드 시 로딩됨): {e}")

    from ui.gradio_app import launch_app
    launch_app(
        agent=agent,
        server_name=args.host,
        server_port=args.port,
        share=args.share,
    )


# ─────────────────────────────────────────────
# 커맨드: 영상 분석
# ─────────────────────────────────────────────

def cmd_analyze(args):
    """영상 파일을 분석하고 결과를 출력합니다."""
    from core_src.video_analysis_system import VideoAnalysisSystem

    video_path = Path(args.video)
    if not video_path.exists():
        logger.error(f"영상 파일 없음: {video_path}")
        sys.exit(1)

    logger.info(f"영상 분석 시작: {video_path}")
    system = VideoAnalysisSystem(collection_name=args.collection)
    summary = system.analyze_video(
        video_path=str(video_path),
        segment_duration=args.segment_duration,
    )

    print("\n=== 영상 분석 결과 ===")
    print(f"비디오 ID  : {summary['video_id']}")
    print(f"컬렉션     : {summary['collection']}")
    print(f"총 길이    : {summary['duration']:.1f}초")
    print(f"세그먼트 수: {summary['segment_count']}개")
    print(f"탐지 건수  : {summary['total_detections']}건")

    if args.output:
        import json
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"\n결과 저장: {out_path}")


# ─────────────────────────────────────────────
# 커맨드: 에이전트 쿼리 (CLI)
# ─────────────────────────────────────────────

def cmd_query(args):
    """에이전트에 단일 쿼리를 실행합니다."""
    from agent.battlefield_agent import BattlefieldAgent, is_strategy_query

    exaone4_model, strategy_model = preload_models(
        skip_strategy=not is_strategy_query(args.query)
    )
    agent = init_agent(exaone4_model, strategy_model)

    if args.video_id:
        agent.set_video_context([args.video_id])

    print(f"\n쿼리: {args.query}")
    print("=" * 60)

    response = agent.run(args.query)
    print(response)

    # 전략 쿼리인 경우 메모리 상태 출력
    if is_strategy_query(args.query):
        memory = agent.get_situation_memory()
        if memory.get("situation_analysis"):
            print("\n[참고] 상황 분석 메모리가 EXAONE Deep에 전달되었습니다.")


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

    # vllm
    try:
        import vllm
        print(f"[OK] vllm {vllm.__version__}")
    except ImportError:
        print("[FAIL] vllm 미설치 - EXAONE4/EXAONE Deep 모델 실행 불가")

    # 기타 패키지
    packages = ["gradio", "transformers", "cv2", "numpy", "faiss"]
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
        description="C2 군사 전략 AI - EXAONE4 + EXAONE Deep 듀얼 모델 시스템",
    )
    subparsers = parser.add_subparsers(dest="command")

    # ui
    ui_p = subparsers.add_parser("ui", help="Gradio UI 실행")
    ui_p.add_argument("--host", default="0.0.0.0")
    ui_p.add_argument("--port", type=int, default=7860)
    ui_p.add_argument("--share", action="store_true")
    ui_p.add_argument("--skip-strategy", action="store_true", help="EXAONE Deep 로딩 건너뜀")
    ui_p.add_argument("--skip-vision-preload", action="store_true", help="비전 모델 사전 로딩 건너뜀")

    # analyze
    ana_p = subparsers.add_parser("analyze", help="영상 분석")
    ana_p.add_argument("--video", required=True, help="분석할 영상 파일 경로")
    ana_p.add_argument("--collection", default="default", help="컬렉션명")
    ana_p.add_argument("--segment-duration", type=int, default=5, help="세그먼트 길이(초)")
    ana_p.add_argument("--output", help="결과 JSON 저장 경로")

    # query
    q_p = subparsers.add_parser("query", help="에이전트 쿼리 (CLI)")
    q_p.add_argument("--query", required=True, help="실행할 쿼리")
    q_p.add_argument("--video-id", help="컨텍스트로 사용할 비디오 ID")

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
        elif args.command == "analyze":
            cmd_analyze(args)
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
