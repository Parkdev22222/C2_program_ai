"""
EXAONE Deep 전략/전술 전문 모델 로더

vLLM 서빙 구조: 모델은 별도 프로세스의 vLLM 서버(`vllm serve`)가 로드하고,
이 모듈은 OpenAI 호환 API 클라이언트만 생성합니다.
서버 기동: python scripts/launch_vllm_servers.py

strategy_advisor_tool에서 model(messages, temperature, max_tokens) 형태로 호출됩니다.
"""
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from agent.vllm_client import VLLMServerClient, resolve_base_url

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "models_config.yaml"

STRATEGY_BASE_URL_ENV = "C2_STRATEGY_VLLM_BASE_URL"
STRATEGY_DEFAULT_PORT = 8001


class StrategyServedModel:
    """
    EXAONE Deep vLLM 서빙 클라이언트 래퍼.

    vLLM 서버(OpenAI 호환 API)에 Chat Completions 요청을 보냅니다.
    채팅 템플릿 적용은 서버가 수행하므로 tokenizer 로딩이 필요 없습니다.

    strategy_advisor_tool에서 model(messages, temperature, max_tokens) 형태로 호출됩니다.
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
        self._strategy_generation_kwargs = generation_kwargs or {}

        served_name = served_model_name or model_id
        logger.info(
            f"Connecting to vLLM server for EXAONE Deep: {base_url} "
            f"(served model: {served_name})"
        )
        self._client = VLLMServerClient(
            base_url=base_url,
            served_model_name=served_name,
            api_key=api_key,
            timeout=request_timeout,
        )
        self._client.check_health()
        logger.info("StrategyServedModel ready")

    def __call__(
        self,
        messages,
        temperature: float = 0.2,
        max_tokens: int = 8192,
        **kwargs,
    ) -> str:
        logger.debug(f"Generating strategy response (max_tokens={max_tokens}, temp={temperature})")
        result = self._client.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        logger.debug(f"Strategy response length: {len(result)} chars")
        return result


# 하위 호환 별칭 (기존 코드에서 StrategyVLLMModel 이름을 참조하는 경우)
StrategyVLLMModel = StrategyServedModel


def load_strategy_model(config: Optional[Dict[str, Any]] = None) -> StrategyServedModel:
    if config is None:
        config = load_strategy_model_config()

    model_id = config.get("model_id", "LGAI-EXAONE/EXAONE-Deep-7.8B")
    generation_cfg = config.get("generation", {})
    serving_cfg = config.get("serving", {})

    base_url = resolve_base_url(serving_cfg, STRATEGY_BASE_URL_ENV, STRATEGY_DEFAULT_PORT)

    logger.info(f"Loading EXAONE Deep served model client: {model_id} @ {base_url}")
    return StrategyServedModel(
        model_id=model_id,
        base_url=base_url,
        served_model_name=serving_cfg.get("served_model_name"),
        api_key=serving_cfg.get("api_key", "EMPTY"),
        request_timeout=serving_cfg.get("request_timeout", 600.0),
        generation_kwargs=generation_cfg,
    )


def load_strategy_model_from_config_file() -> StrategyServedModel:
    config = load_strategy_model_config()
    return load_strategy_model(config)


def load_strategy_model_config() -> Dict[str, Any]:
    with open(CONFIG_PATH) as f:
        full_config = yaml.safe_load(f)
    return dict(full_config.get("strategy_model", {}))
