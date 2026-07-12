"""LangGraph 에이전트용 LLM 팩토리.

기존 smolagents 경로와 동일하게 EXAONE4 는 별도 vLLM 서버(OpenAI 호환 API)에서 서빙되며,
여기서는 그 엔드포인트에 연결하는 LangChain ChatOpenAI 를 만든다.

서버 주소 우선순위: 환경변수 C2_AGENT_VLLM_BASE_URL > models_config.yaml agent_model.serving.base_url
                    > host:port 조합.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "models_config.yaml"


def _agent_model_cfg() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("agent_model", {})
    except Exception:
        return {}


def resolve_base_url(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else _agent_model_cfg()
    serving = cfg.get("serving", {})
    env = os.environ.get("C2_AGENT_VLLM_BASE_URL")
    if env:
        return env
    if serving.get("base_url"):
        return serving["base_url"]
    host = serving.get("host", "127.0.0.1")
    port = serving.get("port", 8000)
    return f"http://{host}:{port}/v1"


def build_chat_llm(temperature: float | None = None, max_tokens: int | None = None):
    """vLLM 서버에 연결하는 ChatOpenAI 인스턴스 생성."""
    from langchain_openai import ChatOpenAI

    cfg = _agent_model_cfg()
    serving = cfg.get("serving", {})
    gen = cfg.get("generation", {})
    return ChatOpenAI(
        base_url=resolve_base_url(cfg),
        api_key=serving.get("api_key", "EMPTY") or "EMPTY",
        model=serving.get("served_model_name", "exaone4-agent"),
        temperature=temperature if temperature is not None else gen.get("temperature", 0.1),
        max_tokens=max_tokens or gen.get("max_tokens", 4096),
        timeout=serving.get("request_timeout", 600),
    )
