"""
vLLM 서빙(OpenAI 호환 API) 공용 클라이언트

기존에는 vllm.LLM을 프로세스 내부에 직접 로드했지만,
이제 별도 프로세스로 기동된 vLLM 서버(`vllm serve` / scripts/launch_vllm_servers.py)에
OpenAI 호환 Chat Completions API로 요청을 보낸다.

- 채팅 템플릿 적용은 vLLM 서버가 수행하므로 tokenizer가 더 이상 필요 없다.
- smolagents ChatMessage / MessageRole enum → OpenAI messages 형식 변환만 담당한다.
"""
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_API_KEY = "EMPTY"
LAUNCH_HINT = (
    "vLLM 서버가 실행 중인지 확인하세요: "
    "python scripts/launch_vllm_servers.py"
)


def resolve_base_url(serving_cfg: Dict, env_var: str, default_port: int) -> str:
    """serving 설정과 환경변수에서 base_url을 결정합니다.

    우선순위: 환경변수 > serving.base_url > http://{host}:{port}/v1
    """
    env_url = os.environ.get(env_var, "").strip()
    if env_url:
        return env_url.rstrip("/")

    base_url = str(serving_cfg.get("base_url") or "").strip()
    if base_url:
        return base_url.rstrip("/")

    host = serving_cfg.get("host", "127.0.0.1")
    port = serving_cfg.get("port", default_port)
    return f"http://{host}:{port}/v1"


def normalize_messages(messages) -> List[Dict[str, str]]:
    """
    smolagents ChatMessage / MessageRole enum → OpenAI chat messages 변환.

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


class VLLMServerClient:
    """
    vLLM OpenAI 호환 서버에 대한 얇은 클라이언트.

    모델 로더(EXAONE4ServedModel / StrategyServedModel)가 내부에서 사용한다.
    """

    def __init__(
        self,
        base_url: str,
        served_model_name: str,
        api_key: str = DEFAULT_API_KEY,
        timeout: float = 600.0,
    ):
        from openai import OpenAI

        self.base_url = base_url
        self.served_model_name = served_model_name
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    def check_health(self) -> bool:
        """서버 연결 확인. 실패해도 예외를 던지지 않고 False를 반환합니다."""
        try:
            models = self._client.models.list()
            available = [m.id for m in models.data]
            if self.served_model_name not in available:
                logger.warning(
                    f"vLLM server at {self.base_url} is up, but model "
                    f"'{self.served_model_name}' not in served models: {available}"
                )
            return True
        except Exception as e:
            logger.warning(
                f"vLLM server not reachable at {self.base_url}: {e}. {LAUNCH_HINT}"
            )
            return False

    def chat(
        self,
        messages,
        temperature: float,
        max_tokens: int,
        stop: Optional[List[str]] = None,
        **kwargs,
    ) -> str:
        """Chat Completions 호출 후 응답 텍스트를 반환합니다."""
        normalized = normalize_messages(messages)
        try:
            response = self._client.chat.completions.create(
                model=self.served_model_name,
                messages=normalized,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop or None,
                **kwargs,
            )
        except Exception as e:
            raise RuntimeError(
                f"vLLM 서버({self.base_url}) 호출 실패: {e}. {LAUNCH_HINT}"
            ) from e

        text = response.choices[0].message.content or ""
        return text.strip()
