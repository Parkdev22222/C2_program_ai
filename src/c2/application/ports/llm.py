"""LLM 호출 포트.

vLLM 서버(OpenAI 호환 Chat Completions API)에 대한 얇은 클라이언트 인터페이스.
`agent/vllm_client.py`의 `VLLMServerClient`가 이 포트를 구조적으로 만족한다.

주의: smolagents `CodeAgent`의 backbone 모델 인터페이스(`agent/model_loader.py`의
`EXAONE4ServedModel.__call__(messages, stop_sequences=None, **kwargs) -> ChatMessage`,
`.generate(...)`)는 이 포트와 별개의 "모델 어댑터" 계약이다. `EXAONE4ServedModel`은
내부적으로 `VLLMServerClient.chat()`을 호출해 이 포트를 사용하는 쪽이며, 그 자체가
이 포트를 만족하지는 않는다(반환 타입이 `str`이 아니라 `ChatMessage`이고 인자 형태도
다름). 애플리케이션 코드가 실제로 호출하는 메서드는 `chat()`과 `check_health()` 뿐이므로
YAGNI 원칙에 따라 두 메서드만 정의한다.
"""

from __future__ import annotations

from typing import Any, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """vLLM OpenAI 호환 서버 호출 포트 (`VLLMServerClient`가 구현)."""

    def chat(
        self,
        messages: Any,
        temperature: float,
        max_tokens: int,
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> str:
        """Chat Completions 호출 후 응답 텍스트를 반환한다."""
        ...

    def check_health(self) -> bool:
        """서버 연결 확인. 실패해도 예외 없이 False를 반환한다."""
        ...
