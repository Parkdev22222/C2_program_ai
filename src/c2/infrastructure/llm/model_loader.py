"""
EXAONE4 메인 에이전트 모델 로더 (EXAONE-4.0-32B-AWQ)

vLLM 서빙 구조: 모델은 별도 프로세스의 vLLM 서버(`vllm serve`)가 로드하고,
이 모듈은 OpenAI 호환 API 클라이언트만 생성합니다.
서버 기동: python scripts/launch_vllm_servers.py

smolagents CodeAgent backbone으로 사용하기 위해
__call__(messages, stop_sequences, **kwargs) → ChatMessage 인터페이스를 구현합니다.
"""
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from c2.infrastructure.llm.vllm_client import VLLMServerClient, resolve_base_url

logger = logging.getLogger(__name__)

# src/c2/infrastructure/llm/model_loader.py 기준 5단계 상위가 리포지토리 루트
CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "models_config.yaml"

AGENT_BASE_URL_ENV = "C2_AGENT_VLLM_BASE_URL"
AGENT_DEFAULT_PORT = 8000


def _make_chat_message(text: str):
    """smolagents ChatMessage 호환 객체를 생성합니다."""
    try:
        from smolagents.models import ChatMessage
        return ChatMessage(role="assistant", content=text)
    except Exception:
        pass
    try:
        from smolagents.types import ChatMessage
        return ChatMessage(role="assistant", content=text)
    except Exception:
        pass

    class _Msg:
        def __init__(self, content):
            self.role = "assistant"
            self.content = content
            self.tool_calls = None
    return _Msg(text)


class EXAONE4ServedModel:
    """
    EXAONE4 vLLM 서빙 클라이언트 래퍼.

    vLLM 서버(OpenAI 호환 API)에 Chat Completions 요청을 보냅니다.
    채팅 템플릿 적용은 서버가 수행하므로 tokenizer 로딩이 필요 없습니다.

    smolagents CodeAgent backbone으로 동작하기 위해
    __call__(messages, stop_sequences, **kwargs) → ChatMessage 인터페이스를 구현합니다.
    """

    def __init__(
        self,
        model_id: str,
        base_url: str,
        served_model_name: Optional[str] = None,
        api_key: str = "EMPTY",
        request_timeout: float = 600.0,
        generation_kwargs: Optional[Dict] = None,
    ):
        self.model_id = model_id
        self._exaone4_generation_kwargs = generation_kwargs or {}

        served_name = served_model_name or model_id
        logger.info(
            f"Connecting to vLLM server for EXAONE4: {base_url} "
            f"(served model: {served_name})"
        )
        self._client = VLLMServerClient(
            base_url=base_url,
            served_model_name=served_name,
            api_key=api_key,
            timeout=request_timeout,
        )
        self._client.check_health()
        logger.info("EXAONE4ServedModel ready")

    def __call__(self, messages, stop_sequences=None, **kwargs):
        gen_cfg = self._exaone4_generation_kwargs
        text = self._client.chat(
            messages,
            temperature=kwargs.get("temperature", gen_cfg.get("temperature", 0.1)),
            max_tokens=kwargs.get("max_tokens", gen_cfg.get("max_tokens", 4096)),
            stop=stop_sequences,
        )
        return _make_chat_message(text)

    def generate(self, messages, stop_sequences=None, **kwargs):
        return self(messages, stop_sequences=stop_sequences, **kwargs)


def load_exaone_model(config: Optional[Dict[str, Any]] = None) -> EXAONE4ServedModel:
    if config is None:
        config = load_exaone_model_config()

    model_id = config.get("model_id_awq") or config.get("model_id", "LGAI-EXAONE/EXAONE-4.0-32B-AWQ")
    generation_cfg = config.get("generation", {})
    serving_cfg = config.get("serving", {})

    base_url = resolve_base_url(serving_cfg, AGENT_BASE_URL_ENV, AGENT_DEFAULT_PORT)

    logger.info(f"Loading EXAONE4 served model client: {model_id} @ {base_url}")
    return EXAONE4ServedModel(
        model_id=model_id,
        base_url=base_url,
        served_model_name=serving_cfg.get("served_model_name"),
        api_key=serving_cfg.get("api_key", "EMPTY"),
        request_timeout=serving_cfg.get("request_timeout", 600.0),
        generation_kwargs=generation_cfg,
    )


def load_model_from_config_file() -> EXAONE4ServedModel:
    config = load_exaone_model_config()
    return load_exaone_model(config)


def load_exaone_model_config() -> Dict[str, Any]:
    with open(CONFIG_PATH) as f:
        full_config = yaml.safe_load(f)
    return dict(full_config.get("agent_model", {}))
