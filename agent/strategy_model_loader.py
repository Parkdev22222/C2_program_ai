"""
EXAONE Deep 전략/전술 전문 모델 로더

smolagents VLLMModel은 trust_remote_code를 vLLM 엔진에 전달하지 못하는 버그가 있어,
vllm.LLM을 직접 사용하는 커스텀 래퍼(StrategyVLLMModel)로 구현합니다.

주의: temperature, max_tokens는 __init__()에 전달하지 않고 __call__() 시 전달합니다.
"""
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "models_config.yaml"


class StrategyVLLMModel:
    """
    EXAONE Deep 전용 vLLM 직접 래퍼.

    smolagents VLLMModel이 trust_remote_code를 vLLM 엔진에 전달하지 못하는
    문제를 해결하기 위해 vllm.LLM을 직접 사용합니다.

    strategy_advisor_tool에서 model(messages, temperature, max_tokens) 형태로 호출됩니다.
    """

    def __init__(
        self,
        model_id: str,
        tensor_parallel_size: int = 1,
        gpu_memory_utilization: float = 0.55,
        dtype: str = "bfloat16",
        max_model_len: int = 32768,
        generation_kwargs: Optional[Dict] = None,
        **extra_llm_kwargs,
    ):
        from vllm import LLM
        from transformers import AutoTokenizer

        self.model_id = model_id
        self._strategy_generation_kwargs = generation_kwargs or {}

        logger.info(f"Initializing vllm.LLM directly for: {model_id}")
        logger.info(
            f"LLM kwargs: tensor_parallel_size={tensor_parallel_size}, "
            f"gpu_memory_utilization={gpu_memory_utilization}, "
            f"dtype={dtype}, max_model_len={max_model_len}, trust_remote_code=True"
        )

        # trust_remote_code를 vLLM 엔진에 직접 전달
        self._llm = LLM(
            model=model_id,
            trust_remote_code=True,
            tensor_parallel_size=tensor_parallel_size,
            gpu_memory_utilization=gpu_memory_utilization,
            dtype=dtype,
            max_model_len=max_model_len,
            **extra_llm_kwargs,
        )

        logger.info(f"Loading tokenizer for chat template: {model_id}")
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_id,
            trust_remote_code=True,
        )
        logger.info("StrategyVLLMModel ready")

    def __call__(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 8192,
        **kwargs,
    ) -> str:
        """
        Args:
            messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]
            temperature: 샘플링 온도
            max_tokens: 최대 생성 토큰 수

        Returns:
            생성된 텍스트 문자열
        """
        from vllm import SamplingParams

        # chat template 적용해서 단일 프롬프트 문자열로 변환
        prompt = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        sampling_params = SamplingParams(
            temperature=temperature,
            max_tokens=max_tokens,
        )

        logger.debug(f"Generating strategy response (max_tokens={max_tokens}, temp={temperature})")
        outputs = self._llm.generate([prompt], sampling_params)
        result = outputs[0].outputs[0].text.strip()
        logger.debug(f"Strategy response length: {len(result)} chars")
        return result


def load_strategy_model(config: Optional[Dict[str, Any]] = None) -> StrategyVLLMModel:
    """
    EXAONE Deep 모델을 StrategyVLLMModel로 로드합니다.

    Args:
        config: 모델 설정 딕셔너리. None이면 models_config.yaml에서 읽음.

    Returns:
        StrategyVLLMModel 인스턴스
    """
    if config is None:
        config = load_strategy_model_config()

    model_id = config.get("model_id", "LGAI-EXAONE/EXAONE-Deep-7.8B")
    generation_cfg = config.get("generation", {})

    logger.info(f"Loading EXAONE Deep strategy model: {model_id}")

    return StrategyVLLMModel(
        model_id=model_id,
        tensor_parallel_size=config.get("tensor_parallel_size", 1),
        gpu_memory_utilization=config.get("gpu_memory_utilization", 0.55),
        dtype=config.get("dtype", "bfloat16"),
        max_model_len=config.get("max_model_len", 32768),
        generation_kwargs=generation_cfg,
    )


def load_strategy_model_from_config_file() -> StrategyVLLMModel:
    """
    models_config.yaml에서 strategy_model 설정을 읽어 EXAONE Deep 모델을 로드합니다.
    """
    config = load_strategy_model_config()
    return load_strategy_model(config)


def load_strategy_model_config() -> Dict[str, Any]:
    """models_config.yaml의 strategy_model 섹션을 반환합니다."""
    with open(CONFIG_PATH) as f:
        full_config = yaml.safe_load(f)
    return dict(full_config.get("strategy_model", {}))
