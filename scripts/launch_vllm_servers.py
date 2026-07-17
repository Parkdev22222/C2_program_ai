#!/usr/bin/env python3
"""
vLLM 서버 기동 스크립트

config/models_config.yaml의 agent_model 설정을 읽어
EXAONE4를 OpenAI 호환 vLLM 서버(`vllm serve`)로 기동합니다.

사용 예시:
  python scripts/launch_vllm_servers.py             # EXAONE4 서버 기동
  python scripts/launch_vllm_servers.py --dry-run   # 실행할 명령만 출력

서버 로그는 logs/vllm_<서버명>.log 파일에 저장되며,
서버가 기동 중 죽으면 해당 로그의 마지막 부분을 자동으로 출력합니다.

기동 후 애플리케이션(python main.py ui 등)을 별도 프로세스로 실행하면
c2.infrastructure.llm.model_loader가 이 서버로 요청을 보냅니다.
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
LOG_DIR = Path(__file__).parent.parent / "logs"

READY_TIMEOUT_S = 1800   # 모델 다운로드 포함 최대 대기 (30분)
POLL_INTERVAL_S = 5
LOG_TAIL_LINES = 40      # 서버 비정상 종료 시 출력할 로그 끝부분 줄 수


def print_log_tail(name: str, log_path: Path, lines: int = LOG_TAIL_LINES):
    """서버 로그 파일의 마지막 N줄을 출력합니다."""
    try:
        content = log_path.read_text(errors="replace").splitlines()
    except OSError as e:
        print(f"[{name}] 로그 파일을 읽을 수 없음 ({log_path}): {e}")
        return
    tail = content[-lines:]
    print(f"\n──── [{name}] 로그 마지막 {len(tail)}줄 ({log_path}) ────")
    for line in tail:
        print(f"  {line}")
    print("─" * 60)


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
        "--gpu-memory-utilization", str(cfg.get("gpu_memory_utilization", 0.90)),
        "--dtype", cfg.get("dtype", "float16"),
        "--max-model-len", str(cfg.get("max_model_len", 32768)),
    ]
    # 추론 속도 최적화 플래그 (config에서 제어)
    # enforce_eager=false(기본) → CUDA 그래프 사용으로 디코드 가속.
    if cfg.get("enforce_eager", False):
        cmd += ["--enforce-eager"]
    if cfg.get("enable_prefix_caching", False):
        cmd += ["--enable-prefix-caching"]
    max_num_seqs = cfg.get("max_num_seqs")
    if max_num_seqs:
        cmd += ["--max-num-seqs", str(max_num_seqs)]
    quantization = cfg.get("quantization")
    if quantization:
        cmd += ["--quantization", quantization]
    return cmd, host, port


def wait_until_ready(
    name: str, host: str, port: int,
    proc: "subprocess.Popen" = None,
    log_path: Path = None,
    timeout_s: int = READY_TIMEOUT_S,
) -> bool:
    """서버 /health 엔드포인트가 응답할 때까지 대기합니다.

    대기 중 서버 프로세스가 죽으면 즉시 실패를 반환하고 로그 끝부분을 출력합니다.
    """
    url = f"http://{host}:{port}/health"
    deadline = time.time() + timeout_s
    started = time.time()
    print(f"[{name}] 서버 준비 대기 중... ({url})")
    last_status = started
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            print(
                f"[{name}] 서버 프로세스가 기동 중 종료됨 (exit code {proc.returncode}). "
                f"아래 로그에서 에러를 확인하세요 (CUDA OOM이면 gpu_memory_utilization/max_model_len 조정)."
            )
            if log_path is not None:
                print_log_tail(name, log_path)
            return False
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    print(f"[{name}] 서버 준비 완료: http://{host}:{port}/v1")
                    return True
        except Exception:
            pass
        # 1분마다 경과 시간 표시 (다운로드/로딩이 길어질 때 멈춘 게 아님을 알 수 있게)
        if time.time() - last_status >= 60:
            elapsed = int(time.time() - started)
            print(f"[{name}] 로딩 중... {elapsed}초 경과 (진행 상황: tail -f {log_path})")
            last_status = time.time()
        time.sleep(POLL_INTERVAL_S)
    print(f"[{name}] 서버 준비 시간 초과 ({timeout_s}s)")
    if log_path is not None:
        print_log_tail(name, log_path)
    return False


def main():
    parser = argparse.ArgumentParser(description="vLLM 서버 기동 (EXAONE4)")
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
    cmd, host, port = build_agent_command(config.get("agent_model", {}))
    targets = [("EXAONE4-agent", cmd, host, port)]

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

    # 서버 stdout/stderr는 logs/vllm_<name>.log 파일로 분리 저장한다.
    LOG_DIR.mkdir(exist_ok=True)
    log_paths = {}
    for name, cmd, host, port in targets:
        log_path = LOG_DIR / f"vllm_{name}.log"
        log_paths[name] = log_path
        print(f"[{name}] 기동: {' '.join(cmd)}")
        print(f"[{name}] 로그: {log_path}  (실시간 확인: tail -f {log_path})")
        log_file = open(log_path, "w")
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
        procs.append((name, proc))
        if not args.no_wait:
            if not wait_until_ready(name, host, port, proc, log_path):
                shutdown()

    print("\n모든 vLLM 서버가 기동되었습니다. 애플리케이션을 실행하세요: python main.py ui")
    print("종료: Ctrl+C")

    # 자식 프로세스가 하나라도 죽으면 전체 종료
    while True:
        for name, p in procs:
            code = p.poll()
            if code is not None:
                print(f"[{name}] 서버가 종료되었습니다 (exit code {code}). 전체 종료합니다.")
                if name in log_paths:
                    print_log_tail(name, log_paths[name])
                shutdown()
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
