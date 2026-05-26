"""
한 코드 블록(스텝)당 도구 호출 횟수를 1회로 제한하는 가드.

동작 원리:
  - smolagents Python executor가 코드 블록을 실행하기 직전에 reset() 호출
  - 각 Tool.forward() 진입 시 guard() 호출 → 2회째부터 RuntimeError 발생
  - threading.local 사용 → 에이전트 스레드와 UI 스레드가 독립적으로 카운트
"""
import threading
import logging

logger = logging.getLogger(__name__)

_tls = threading.local()


def reset() -> None:
    """코드 블록 실행 전 호출 — 호출 카운터를 0으로 초기화."""
    _tls.calls = 0


def guard(tool_name: str) -> None:
    """Tool.forward() 진입 시 호출 — 2회째 호출이면 RuntimeError 발생."""
    calls = getattr(_tls, "calls", 0)
    if calls >= 1:
        raise RuntimeError(
            f"[단일 도구 제한] '{tool_name}' 호출이 거부되었습니다.\n"
            "이번 코드 블록에서 이미 도구를 1회 호출했습니다.\n"
            "결과를 print()로 출력한 뒤 다음 코드 블록에서 호출하세요."
        )
    _tls.calls = calls + 1
    logger.debug(f"[SingleToolGuard] tool_call={calls + 1}: {tool_name}")
