"""
EXAONE Deep 전략/전술 전문 모델 로더

vLLM AWQ 커스텀 커널이 Colab 환경의 torch ABI와 맞지 않는 문제로
transformers 기반 로더로 교체.

strategy_advisor_tool에서 model(messages, temperature, max_tokens) 형태로 호출됩니다.
"""
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "models_config.yaml"


class StrategyVLLMModel:
    """
    EXAONE Deep 전용 transformers 래퍼.
    strategy_advisor_tool에서 model(messages, temperature, max_tokens) 형태로 호출됩니다.
    """

    def __init__(
        self,
        model_id: str,
        dtype: str = "bfloat16",
        max_model_len: int = 32768,
        generation_kwargs: Optional[Dict] = None,
        **_ignored,  # vLLM 전용 파라미터 무시
    ):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_id = model_id
        self._max_model_len = max_model_len
        self._generation_kwargs = generation_kwargs or {}

        torch_dtype = torch.bfloat16 if dtype == "bfloat16" else torch.float16
        logger.info(f"[StrategyModel] transformers 로딩: {model_id} (dtype={dtype})")

        self._model = AutoModelForCausalLM.from_pretrained(
            model_id,
            trust_remote_code=True,
            device_map="auto",
            torch_dtype=torch_dtype,
        )
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_id, trust_remote_code=True
        )
        logger.info("[StrategyModel] 모델 로드 완료")

    @staticmethod
    def _normalize_messages(messages) -> List[Dict[str, str]]:
        """smolagents MessageRole enum → plain dict 변환."""
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
        temperature: float = 0.2,
        max_tokens: int = 8192,
        **kwargs,
    ) -> str:
        import torch

        normalized = self._normalize_messages(messages)
        inputs = self._tokenizer.apply_chat_template(
            normalized,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
        ).to(self._model.device)

        max_new_tokens = min(max_tokens, self._max_model_len - inputs.shape[1])

        logger.debug(f"[StrategyModel] 생성 시작 (max_tokens={max_new_tokens}, temp={temperature})")
        with torch.no_grad():
            output_ids = self._model.generate(
                inputs,
                max_new_tokens=max_new_tokens,
                temperature=temperature if temperature > 0 else None,
                do_sample=temperature > 0,
                pad_token_id=self._tokenizer.eos_token_id,
            )

        result = self._tokenizer.decode(
            output_ids[0][inputs.shape[1]:], skip_special_tokens=True
        ).strip()
        logger.debug(f"[StrategyModel] 생성 완료 ({len(result)}자)")
        return result


def load_strategy_model(config: Optional[Dict[str, Any]] = None) -> StrategyVLLMModel:
    if config is None:
        config = load_strategy_model_config()

    model_id = config.get("model_id", "LGAI-EXAONE/EXAONE-Deep-7.8B")
    generation_cfg = config.get("generation", {})

    logger.info(f"Loading EXAONE Deep strategy model: {model_id}")
    return StrategyVLLMModel(
        model_id=model_id,
        dtype=config.get("dtype", "bfloat16"),
        max_model_len=config.get("max_model_len", 32768),
        generation_kwargs=generation_cfg,
    )


def load_strategy_model_from_config_file() -> StrategyVLLMModel:
    config = load_strategy_model_config()
    return load_strategy_model(config)


def load_strategy_model_config() -> Dict[str, Any]:
    with open(CONFIG_PATH) as f:
        full_config = yaml.safe_load(f)
    return dict(full_config.get("strategy_model", {}))
