"""
EXAONE4 메인 에이전트 모델 로더 (EXAONE-4.0-32B-AWQ)

vllm.LLM을 직접 사용하여 quantization="awq"와 trust_remote_code=True를
vLLM 엔진에 명시적으로 전달합니다.

smolagents CodeAgent backbone으로 사용하기 위해
__call__(messages, stop_sequences, **kwargs) → ChatMessage 인터페이스를 구현합니다.
"""
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "models_config.yaml"


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


class EXAONE4DirectModel:
    """
    EXAONE4 전용 vLLM 직접 래퍼.

    vllm.LLM을 직접 사용하여 quantization="awq"와 trust_remote_code=True를
    vLLM 엔진에 명시적으로 전달합니다.

    smolagents CodeAgent backbone으로 동작하기 위해
    __call__(messages, stop_sequences, **kwargs) → ChatMessage 인터페이스를 구현합니다.
    """

    def __init__(
        self,
        model_id: str,
        quantization: str = "awq",
        tensor_parallel_size: int = 1,
        gpu_memory_utilization: float = 0.35,
        dtype: str = "float16",
        max_model_len: int = 32768,
        generation_kwargs: Optional[Dict] = None,
        **extra_llm_kwargs,
    ):
        from vllm import LLM
        from transformers import AutoTokenizer

        self.model_id = model_id
        self._exaone4_generation_kwargs = generation_kwargs or {}

        logger.info(f"Initializing vllm.LLM directly for EXAONE4: {model_id}")
        logger.info(
            f"LLM kwargs: quantization={quantization}, "
            f"tensor_parallel_size={tensor_parallel_size}, "
            f"gpu_memory_utilization={gpu_memory_utilization}, "
            f"dtype={dtype}, max_model_len={max_model_len}, trust_remote_code=True"
        )

        self._llm = LLM(
            model=model_id,
            trust_remote_code=True,
            quantization=quantization,
            tensor_parallel_size=tensor_parallel_size,
            gpu_memory_utilization=gpu_memory_utilization,
            dtype=dtype,
            max_model_len=max_model_len,
            enforce_eager=True,
            **extra_llm_kwargs,
        )

        logger.info(f"Loading tokenizer: {model_id}")
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_id, trust_remote_code=True
        )
        logger.info("EXAONE4DirectModel ready")

    @staticmethod
    def _normalize_messages(messages) -> List[Dict[str, str]]:
        """
        smolagents ChatMessage / MessageRole enum → EXAONE tokenizer용 plain dict 변환.

        EXAONE chat template 지원 role: system, user, assistant
        - TOOL_RESPONSE → user (prefix 추가)
        - TOOL_CALL     → assistant
        - 기타 unknown  → user
        """
        normalized = []
        for msg in messages:
            if hasattr(msg, "role"):
                role_raw = msg.role
                role_str = role_raw.value if hasattr(role_raw, "value") else str(role_raw)
            elif isinstance(msg, dict):
                role_str = str(msg.get("role", "user"))
            else:
                continue

            if hasattr(msg, "content"):
                content = msg.content
            elif isinstance(msg, dict):
                content = msg.get("content", "")
            else:
                content = str(msg)

            if isinstance(content, list):
                parts = [
                    c.get("text", str(c)) if isinstance(c, dict) else str(c)
                    for c in content
                ]
                content = "\n".join(parts)
            content = str(content) if content is not None else ""

            r = role_str.lower().replace("-", "_").replace(".", "_")
            if "system" in r:
                normalized.append({"role": "system", "content": content})
            elif "assistant" in r:
                normalized.append({"role": "assistant", "content": content})
            elif "tool_response" in r or "tool_result" in r:
                normalized.append({"role": "user", "content": f"[Tool Result]\n{content}"})
            elif "tool_call" in r:
                if normalized and normalized[-1]["role"] == "assistant":
                    normalized[-1]["content"] += f"\n{content}"
                else:
                    normalized.append({"role": "assistant", "content": content})
            else:
                normalized.append({"role": "user", "content": content})
        return normalized

    def __call__(
        self,
        messages,
        stop_sequences: Optional[List[str]] = None,
        **kwargs,
    ):
        from vllm import SamplingParams

        normalized = self._normalize_messages(messages)

        prompt = self._tokenizer.apply_chat_template(
            normalized,
            tokenize=False,
            add_generation_prompt=True,
        )

        gen_cfg = self._exaone4_generation_kwargs
        params = SamplingParams(
            temperature=kwargs.get("temperature", gen_cfg.get("temperature", 0.1)),
            max_tokens=kwargs.get("max_tokens", gen_cfg.get("max_tokens", 4096)),
            stop=stop_sequences or [],
        )

        outputs = self._llm.generate([prompt], params)
        text = outputs[0].outputs[0].text.strip()
        return _make_chat_message(text)

    def generate(self, messages, stop_sequences=None, **kwargs):
        return self(messages, stop_sequences=stop_sequences, **kwargs)


def load_exaone_model(config: Optional[Dict[str, Any]] = None) -> EXAONE4DirectModel:
    if config is None:
        config = load_exaone_model_config()

    model_id = config.get("model_id_awq") or config.get("model_id", "LGAI-EXAONE/EXAONE-4.0-32B-AWQ")
    generation_cfg = config.get("generation", {})

    logger.info(f"Loading EXAONE4 model: {model_id}")
    return EXAONE4DirectModel(
        model_id=model_id,
        quantization=config.get("quantization", "awq"),
        tensor_parallel_size=config.get("tensor_parallel_size", 1),
        gpu_memory_utilization=config.get("gpu_memory_utilization", 0.35),
        dtype=config.get("dtype", "float16"),
        max_model_len=config.get("max_model_len", 32768),
        generation_kwargs=generation_cfg,
    )


def load_model_from_config_file() -> EXAONE4DirectModel:
    config = load_exaone_model_config()
    return load_exaone_model(config)


def load_exaone_model_config() -> Dict[str, Any]:
    with open(CONFIG_PATH) as f:
        full_config = yaml.safe_load(f)
    return dict(full_config.get("agent_model", {}))
