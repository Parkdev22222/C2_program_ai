"""
EXAONE4 메인 에이전트 모델 로더 (EXAONE-4.0-32B-AWQ)

vLLM AWQ 커스텀 커널이 Colab 환경의 torch ABI와 맞지 않는 문제로
autoawq + transformers 기반 로더로 교체.

smolagents CodeAgent backbone으로 사용하기 위해
__call__(messages, stop_sequences, **kwargs) → ChatMessage 인터페이스를 구현합니다.
"""
import logging
import yaml
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
    EXAONE4 전용 autoawq + transformers 래퍼.

    smolagents CodeAgent backbone으로 동작하기 위해
    __call__(messages, stop_sequences, **kwargs) → ChatMessage 인터페이스를 구현합니다.
    """

    def __init__(
        self,
        model_id: str,
        dtype: str = "float16",
        max_model_len: int = 32768,
        generation_kwargs: Optional[Dict] = None,
        **_ignored,  # vLLM 전용 파라미터(tensor_parallel_size 등) 무시
    ):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_id = model_id
        self._max_model_len = max_model_len
        self._generation_kwargs = generation_kwargs or {}

        torch_dtype = torch.bfloat16 if dtype == "bfloat16" else torch.float16
        logger.info(f"[EXAONE4] autoawq+transformers 로딩: {model_id} (dtype={dtype})")

        self._model = AutoModelForCausalLM.from_pretrained(
            model_id,
            trust_remote_code=True,
            device_map="auto",
            torch_dtype=torch_dtype,
        )
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_id, trust_remote_code=True
        )
        logger.info("[EXAONE4] 모델 로드 완료")

    @staticmethod
    def _normalize_messages(messages) -> List[Dict[str, str]]:
        """smolagents ChatMessage / MessageRole enum → plain dict 변환."""
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
        import torch

        normalized = self._normalize_messages(messages)
        gen_cfg = self._generation_kwargs

        inputs = self._tokenizer.apply_chat_template(
            normalized,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
        ).to(self._model.device)

        temperature = kwargs.get("temperature", gen_cfg.get("temperature", 0.1))
        max_new_tokens = kwargs.get("max_tokens", gen_cfg.get("max_tokens", 4096))
        max_new_tokens = min(max_new_tokens, self._max_model_len - inputs.shape[1])

        stop_ids = []
        if stop_sequences:
            for s in stop_sequences:
                ids = self._tokenizer.encode(s, add_special_tokens=False)
                if ids:
                    stop_ids.append(ids[0])

        with torch.no_grad():
            output_ids = self._model.generate(
                inputs,
                max_new_tokens=max_new_tokens,
                temperature=temperature if temperature > 0 else None,
                do_sample=temperature > 0,
                pad_token_id=self._tokenizer.eos_token_id,
                eos_token_id=(
                    stop_ids + [self._tokenizer.eos_token_id]
                    if stop_ids else self._tokenizer.eos_token_id
                ),
            )

        text = self._tokenizer.decode(
            output_ids[0][inputs.shape[1]:], skip_special_tokens=True
        ).strip()
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
