"""[shim] moved to c2.infrastructure.llm.vllm_client

이 모듈은 하위 호환을 위한 재노출(shim)입니다. 실제 구현은
`c2.infrastructure.llm.vllm_client`로 이전되었습니다. 기존 임포트
(`from agent.vllm_client import VLLMServerClient`)는 계속 동작하며
동일 객체(identity)를 반환합니다.
"""
from c2.infrastructure.llm.vllm_client import (
    DEFAULT_API_KEY,
    LAUNCH_HINT,
    VLLMServerClient,
    normalize_messages,
    resolve_base_url,
)

__all__ = [
    "DEFAULT_API_KEY",
    "LAUNCH_HINT",
    "VLLMServerClient",
    "normalize_messages",
    "resolve_base_url",
]
