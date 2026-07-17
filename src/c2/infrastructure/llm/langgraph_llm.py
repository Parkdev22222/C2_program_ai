"""LangGraph 에이전트용 LLM 팩토리 (멀티 프로바이더).

두 가지 LLM 백엔드를 지원하며 환경변수/설정으로 전환한다.

- ``vllm`` (기본): 직접 서빙한 EXAONE4 vLLM 서버(OpenAI 호환 API)에 ChatOpenAI 로 연결.
- ``gemini``: Google Gemini API(ChatGoogleGenerativeAI)에 연결. 서버 기동 불필요.

프로바이더 선택 우선순위: 환경변수 C2_LLM_PROVIDER > models_config.yaml 최상위 llm_provider
                        > "vllm".

vLLM 서버 주소 우선순위: 환경변수 C2_AGENT_VLLM_BASE_URL > agent_model.serving.base_url
                        > host:port 조합.

Gemini API 키: 환경변수 GOOGLE_API_KEY (없으면 GEMINI_API_KEY). 설정 파일/코드에 키를 직접
              넣지 말고 환경변수로 주입하는 것을 권장한다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# src/c2/infrastructure/llm/langgraph_llm.py 기준 5단계 상위가 리포지토리 루트
_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "models_config.yaml"


def _full_cfg() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _agent_model_cfg() -> dict:
    return _full_cfg().get("agent_model", {})


def resolve_provider() -> str:
    """사용할 LLM 프로바이더('vllm' | 'gemini')를 결정한다."""
    env = os.environ.get("C2_LLM_PROVIDER")
    if env:
        return env.strip().lower()
    cfg_val = _full_cfg().get("llm_provider")
    if cfg_val:
        return str(cfg_val).strip().lower()
    return "vllm"


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


def _resolve_gemini_api_key(gcfg: dict) -> str:
    """Gemini API 키를 환경변수에서 해석한다 (설정에는 키 이름만 저장)."""
    key_env = gcfg.get("api_key_env", "GOOGLE_API_KEY")
    return (
        os.environ.get(key_env)
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or ""
    )


def describe_llm_target() -> str:
    """현재 프로바이더가 연결할 대상(엔드포인트/모델)을 사람이 읽을 문자열로 반환."""
    provider = resolve_provider()
    if provider in ("gemini", "google"):
        gcfg = _full_cfg().get("gemini_model", {}) or {}
        return f"gemini model={gcfg.get('model', 'gemini-2.5-flash')}"
    return f"vllm base_url={resolve_base_url()}"


def _build_vllm_llm(temperature: float | None, max_tokens: int | None):
    from langchain_openai import ChatOpenAI

    cfg = _agent_model_cfg()
    serving = cfg.get("serving", {})
    gen = cfg.get("generation", {})
    llm = ChatOpenAI(
        base_url=resolve_base_url(cfg),
        api_key=serving.get("api_key", "EMPTY") or "EMPTY",
        model=serving.get("served_model_name", "exaone4-agent"),
        temperature=temperature if temperature is not None else gen.get("temperature", 0.1),
        max_tokens=max_tokens or gen.get("max_tokens", 4096),
        timeout=serving.get("request_timeout", 600),
    )
    logger.info("LLM 프로바이더=vllm (base_url=%s)", resolve_base_url(cfg))
    return llm


def _build_gemini_llm(temperature: float | None, max_tokens: int | None):
    from langchain_google_genai import ChatGoogleGenerativeAI

    gcfg = _full_cfg().get("gemini_model", {}) or {}
    gen = gcfg.get("generation", {})
    api_key = _resolve_gemini_api_key(gcfg)
    if not api_key:
        raise RuntimeError(
            "Gemini API 키가 없습니다. 환경변수로 키를 주입하세요: "
            'export GOOGLE_API_KEY="<your-key>" (또는 GEMINI_API_KEY).'
        )
    model = gcfg.get("model", "gemini-2.5-flash")
    llm = ChatGoogleGenerativeAI(
        model=model,
        google_api_key=api_key,
        temperature=temperature if temperature is not None else gen.get("temperature", 0.1),
        max_output_tokens=max_tokens or gen.get("max_tokens", 4096),
    )
    logger.info("LLM 프로바이더=gemini (model=%s)", model)
    return llm


def build_chat_llm(temperature: float | None = None, max_tokens: int | None = None):
    """선택된 프로바이더에 맞는 LangChain Chat 모델을 생성한다.

    - vllm  : 직접 서빙한 EXAONE4 (ChatOpenAI, OpenAI 호환 API)
    - gemini: Google Gemini API (ChatGoogleGenerativeAI)
    두 모델 모두 function calling(tool call)을 지원하므로 LangGraph 그래프에서 동일하게 동작한다.
    """
    provider = resolve_provider()
    if provider in ("gemini", "google"):
        return _build_gemini_llm(temperature, max_tokens)
    return _build_vllm_llm(temperature, max_tokens)
