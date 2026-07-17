"""[shim] moved to c2.infrastructure.llm.langgraph_llm

이 모듈은 하위 호환을 위한 재노출(shim)입니다. 실제 구현은
`c2.infrastructure.llm.langgraph_llm`으로 이전되었습니다. 기존 임포트
(`from agent.langgraph_llm import build_chat_llm`)는 계속 동작하며
동일 객체(identity)를 반환합니다.
"""
from c2.infrastructure.llm.langgraph_llm import (
    build_chat_llm,
    describe_llm_target,
    resolve_base_url,
    resolve_provider,
)

__all__ = [
    "build_chat_llm",
    "describe_llm_target",
    "resolve_base_url",
    "resolve_provider",
]
