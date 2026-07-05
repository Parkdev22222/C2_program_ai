#!/usr/bin/env python3
"""
vLLM 서버 기동 스크립트

config/models_config.yaml의 agent_model / strategy_model 설정을 읽어
각 모델을 OpenAI 호환 vLLM 서버(`vllm serve`)로 기동합니다.

사용 예시:
  python scripts/launch_vllm_servers.py                # 두 모델 모두 기동
  python scripts/launch_vllm_servers.py --only agent    # EXAONE4만 기동
  python scripts/launch_vllm_servers.py --only strategy # EXAONE Deep만 기동
  python scripts/launch_vllm_servers.py --dry-run       # 실행할 명령만 출력

기동 후 애플리케이션(python main.py ui 등)을 별도 프로세스로 실행하면
agent/model_loader.py, agent/strategy_model_loader.py가 이 서버로 요청을 보냅니다.
"""
import argparse
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config" / "models_config.yaml"

READY_TIMEOUT_S = 1800   # 모델 다운로드 포함 최대 대기 (30분)
POLL_INTERVAL_S = 5


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def build_agent_command(cfg: dict) -> tuple:
    """EXAONE4 (메인 에이전트) vllm serve 명령을 생성합니다."""
    serving = cfg.get("serving", {})
    model_id = cfg.get("model_id_awq") or cfg.get("model_id", "LGAI-EXAONE/EXAONE-4.0-32B-AWQ")
    host = serving.get("host", "127.0.0.1")
    port = int(serving.get("port", 8000))

    cmd = [
        "vllm", "serve", model_id,
        "--host", host,
        "--port", str(port),
        "--served-model-name", serving.get("served_model_name", model_id),
        "--trust-remote-code",
        "--tensor-parallel-size", str(cfg.get("tensor_parallel_size", 1)),
        "--gpu-memory-utilization", str(cfg.get("gpu_memory_utilization", 0.30)),
        "--dtype", cfg.get("dtype", "float16"),
        "--max-model-len", str(cfg.get("max_model_len", 32768)),
        "--enforce-eager",
    ]
    quantization = cfg.get("quantization")
    if quantization:
        cmd += ["--quantization", quantization]
    return cmd, host, port


def build_strategy_command(cfg: dict) -> tuple:
    """EXAONE Deep (전략/전술) vllm serve 명령을 생성합니다."""
    serving = cfg.get("serving", {})
    model_id = cfg.get("model_id", "LGAI-EXAONE/EXAONE-Deep-7.8B")
    host = serving.get("host", "127.0.0.1")
    port = int(serving.get("port", 8001))

    cmd = [
        "vllm", "serve", model_id,
        "--host", host,
        "--port", str(port),
        "--served-model-name", serving.get("served_model_name", model_id),
        "--trust-remote-code",
        "--tensor-parallel-size", str(cfg.get("tensor_parallel_size", 1)),
        "--gpu-memory-utilization", str(cfg.get("gpu_memory_utilization", 0.45)),
        "--dtype", cfg.get("dtype", "bfloat16"),
        "--max-model-len", str(cfg.get("max_model_len", 32768)),
        "--enforce-eager",
    ]
    return cmd, host, port


def wait_until_ready(
    name: str, host: str, port: int,
    proc: "subprocess.Popen" = None,
    timeout_s: int = READY_TIMEOUT_S,
) -> bool:
    """서버 /health 엔드포인트가 응답할 때까지 대기합니다.

    대기 중 서버 프로세스가 죽으면 즉시 실패를 반환합니다.
    """
    url = f"http://{host}:{port}/health"
    deadline = time.time() + timeout_s
    print(f"[{name}] 서버 준비 대기 중... ({url})")
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            print(
                f"[{name}] 서버 프로세스가 기동 중 종료됨 (exit code {proc.returncode}). "
                f"위 로그의 에러를 확인하세요 (CUDA OOM이면 gpu_memory_utilization/max_model_len 조정)."
            )
            return False
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    print(f"[{name}] 서버 준비 완료: http://{host}:{port}/v1")
                    return True
        except Exception:
            pass
        time.sleep(POLL_INTERVAL_S)
    print(f"[{name}] 서버 준비 시간 초과 ({timeout_s}s)")
    return False


def main():
    parser = argparse.ArgumentParser(description="vLLM 서버 기동 (EXAONE4 + EXAONE Deep)")
    parser.add_argument(
        "--only", choices=["agent", "strategy"],
        help="지정한 모델 서버만 기동 (기본: 둘 다)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="서버를 기동하지 않고 실행할 명령만 출력",
    )
    parser.add_argument(
        "--no-wait", action="store_true",
        help="서버 기동 후 준비(health) 대기를 건너뜀",
    )
    args = parser.parse_args()

    config = load_config()
    targets = []
    if args.only in (None, "agent"):
        cmd, host, port = build_agent_command(config.get("agent_model", {}))
        targets.append(("EXAONE4-agent", cmd, host, port))
    if args.only in (None, "strategy"):
        cmd, host, port = build_strategy_command(config.get("strategy_model", {}))
        targets.append(("EXAONE-Deep-strategy", cmd, host, port))

    if args.dry_run:
        for name, cmd, _, _ in targets:
            print(f"[{name}]")
            print("  " + " ".join(cmd))
        return

    procs = []

    def shutdown(signum=None, frame=None):
        print("\n서버 종료 중...")
        for name, p in procs:
            if p.poll() is None:
                p.terminate()
        for name, p in procs:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # 순차 기동: 두 서버가 동시에 GPU 메모리를 프로파일링하면 가용량 계산이
    # 어긋나 OOM이 날 수 있으므로, 앞 서버가 준비된 후 다음 서버를 기동한다.
    for name, cmd, host, port in targets:
        print(f"[{name}] 기동: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd)
        procs.append((name, proc))
        if not args.no_wait:
            if not wait_until_ready(name, host, port, proc):
                shutdown()

    print("\n모든 vLLM 서버가 기동되었습니다. 애플리케이션을 실행하세요: python main.py ui")
    print("종료: Ctrl+C")

    # 자식 프로세스가 하나라도 죽으면 전체 종료
    while True:
        for name, p in procs:
            code = p.poll()
            if code is not None:
                print(f"[{name}] 서버가 종료되었습니다 (exit code {code}). 전체 종료합니다.")
                shutdown()
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
