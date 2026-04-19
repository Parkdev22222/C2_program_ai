"""
EXAONE4 메인 에이전트 모델 로더 (EXAONE-4.0-32B-AWQ)

smolagents VLLMModel이 trust_remote_code를 vLLM 엔진에 전달하지 못하는 버그가 있어,
smolagents VLLMModel을 사용하되 trust_remote_code 전달 여부를 먼저 시도하고,
실패하면 vllm.LLM 직접 래퍼로 폴백합니다.

EXAONE4는 smolagents CodeAgent의 backbone 모델이므로 smolagents VLLMModel 인터페이스가
필요합니다. AWQ 모델은 trust_remote_code 없이도 로드되는 경우가 많아 먼저 시도합니다.

주의: temperature, max_tokens는 __init__()에 전달하지 않고 generate() 시 전달합니다.
"""
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "models_config.yaml"


def load_exaone_model(config: Optional[Dict[str, Any]] = None):
    """
    EXAONE4 모델을 로드합니다.
    smolagents VLLMModel로 먼저 시도하고, trust_remote_code 문제 발생 시
    vllm.LLM 직접 래퍼로 폴백합니다.

    Returns:
        smolagents VLLMModel 또는 EXAONE4VLLMModel 인스턴스
    """
    if config is None:
        config = load_exaone_model_config()

    model_id = config.get("model_id_awq") or config.get("model_id", "LGAI-EXAONE/EXAONE-4.0-32B-AWQ")
    generation_cfg = config.pop("generation", {})

    model_kwargs = {
        k: v
        for k, v in config.items()
        if k not in ("model_id", "model_id_awq", "quantization", "generation")
    }

    logger.info(f"Loading EXAONE4 main agent model: {model_id}")

    # smolagents VLLMModel 먼저 시도 (CodeAgent backbone으로 필요)
    try:
        from smolagents import VLLMModel
        model = VLLMModel(model_id=model_id, **model_kwargs)
        model._exaone4_generation_kwargs = generation_cfg
        logger.info("EXAONE4 loaded via smolagents VLLMModel")
        return model
    except Exception as e:
        logger.warning(f"smolagents VLLMModel failed ({e}), falling back to direct vLLM wrapper")

    # 폴백: vllm.LLM 직접 사용 래퍼
    model = _load_exaone4_direct(model_id, model_kwargs, generation_cfg)
    logger.info("EXAONE4 loaded via direct vLLM wrapper")
    return model


def _load_exaone4_direct(model_id: str, model_kwargs: dict, generation_cfg: dict):
    """
    smolagents VLLMModel 실패 시 vllm.LLM을 직접 사용하는 래퍼를 반환합니다.
    smolagents Model 인터페이스와 호환되도록 구현합니다.
    """
    from vllm import LLM
    from transformers import AutoTokenizer

    logger.info(f"Initializing vllm.LLM directly for EXAONE4: {model_id}")

    llm = LLM(
        model=model_id,
        trust_remote_code=True,
        **{k: v for k, v in model_kwargs.items() if k != "trust_remote_code"},
    )
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)

    class EXAONE4VLLMModel:
        """smolagents Model 인터페이스 호환 래퍼"""

        def __init__(self):
            self.model_id = model_id
            self._llm = llm
            self._tokenizer = tokenizer
            self._exaone4_generation_kwargs = generation_cfg

        def __call__(self, messages, temperature=0.1, max_tokens=4096, **kwargs):
            from vllm import SamplingParams

            prompt = self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            params = SamplingParams(temperature=temperature, max_tokens=max_tokens)
            outputs = self._llm.generate([prompt], params)
            return outputs[0].outputs[0].text.strip()

        # smolagents CodeAgent가 내부적으로 사용하는 generate() 인터페이스
        def generate(self, messages, stop_sequences=None, **kwargs):
            temperature = self._exaone4_generation_kwargs.get("temperature", 0.1)
            max_tokens = self._exaone4_generation_kwargs.get("max_tokens", 4096)
            return self(messages, temperature=temperature, max_tokens=max_tokens)

    return EXAONE4VLLMModel()


def load_model_from_config_file():
    """
    models_config.yaml에서 agent_model 설정을 읽어 EXAONE4 모델을 로드합니다.
    """
    config = load_exaone_model_config()
    return load_exaone_model(config)


def load_exaone_model_config() -> Dict[str, Any]:
    """models_config.yaml의 agent_model 섹션을 반환합니다."""
    with open(CONFIG_PATH) as f:
        full_config = yaml.safe_load(f)
    return dict(full_config.get("agent_model", {}))
