"""
한 코드 블록(스텝)당 도구 호출 횟수를 1회로 제한하는 가드.

동작 원리:
  - smolagents Python executor가 코드 블록을 실행하기 직전에 activate() 호출
  - 각 Tool.forward() 진입 시 guard() 호출 → 2회째부터 RuntimeError 발생
  - 코드 블록 실행 완료 후 deactivate() 호출
  - threading.local 사용 → 에이전트 스레드와 UI 스레드가 독립적으로 카운트

주의: activate()/deactivate()로 감싸인 구간(에이전트 코드 블록)에서만 제한이 적용됨.
      gradio_app.py 등에서 직접 도구를 호출하는 경우에는 제한 없음.
"""
import threading
import logging

logger = logging.getLogger(__name__)

_tls = threading.local()


def activate() -> None:
    """에이전트 코드 블록 실행 직전 호출 — 가드를 활성화하고 카운터를 0으로 초기화."""
    _tls.active = True
    _tls.calls = 0


def deactivate() -> None:
    """에이전트 코드 블록 실행 완료 후 호출 — 가드를 비활성화."""
    _tls.active = False
    _tls.calls = 0


def reset() -> None:
    """하위 호환용 — activate()와 동일."""
    activate()


def guard(tool_name: str) -> None:
    """Tool.forward() 진입 시 호출 — 에이전트 실행 중에만 2회째 호출을 차단."""
    if not getattr(_tls, "active", False):
        return  # 직접 Python 호출 (gradio_app 등) → 제한 없음
    calls = getattr(_tls, "calls", 0)
    if calls >= 1:
        raise RuntimeError(
            f"[단일 도구 제한] '{tool_name}' 호출이 거부되었습니다.\n"
            "이번 코드 블록에서 이미 도구를 1회 호출했습니다.\n"
            "결과를 print()로 출력한 뒤 다음 코드 블록에서 호출하세요."
        )
    _tls.calls = calls + 1
    logger.debug(f"[SingleToolGuard] tool_call={calls + 1}: {tool_name}")
