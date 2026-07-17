"""[shim] moved to c2.infrastructure.llm.model_loader

이 모듈은 하위 호환을 위한 재노출(shim)입니다. 실제 구현은
`c2.infrastructure.llm.model_loader`로 이전되었습니다. 기존 임포트
(`from agent.model_loader import load_model_from_config_file`)는 계속 동작하며
동일 객체(identity)를 반환합니다.
"""
from c2.infrastructure.llm.model_loader import (
    AGENT_BASE_URL_ENV,
    AGENT_DEFAULT_PORT,
    CONFIG_PATH,
    EXAONE4ServedModel,
    load_exaone_model,
    load_exaone_model_config,
    load_model_from_config_file,
)

__all__ = [
    "AGENT_BASE_URL_ENV",
    "AGENT_DEFAULT_PORT",
    "CONFIG_PATH",
    "EXAONE4ServedModel",
    "load_exaone_model",
    "load_exaone_model_config",
    "load_model_from_config_file",
]
