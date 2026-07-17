"""smolagents 툴 → LangChain 툴 어댑터.

기존 tools/*.py 의 smolagents @tool 객체를 그대로 재사용해 LangChain StructuredTool 로
감싼다. 실제 실행 로직·워게임 엔진 연동(register_wargame_engine)·반환 구조가 동일하므로
LangGraph 에이전트도 smolagents 경로와 완전히 같은 기능을 수행한다.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Optional

from pydantic import Field, create_model

logger = logging.getLogger(__name__)

# smolagents input type → 파이썬 타입
_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
    "any": str,
    "null": str,
}


def _build_args_schema(smol_tool):
    """smolagents 툴의 forward 시그니처 + inputs 로 pydantic args_schema 생성 (기본값 보존)."""
    sig = inspect.signature(smol_tool.forward)
    inputs = getattr(smol_tool, "inputs", {}) or {}
    fields: dict = {}
    for name, param in sig.parameters.items():
        if name in ("self", "args", "kwargs"):
            continue
        spec = inputs.get(name, {})
        pytype = _TYPE_MAP.get(spec.get("type", "string"), str)
        desc = spec.get("description", "") or ""
        if param.default is not inspect.Parameter.empty:
            fields[name] = (Optional[pytype], Field(default=param.default, description=desc))
        else:
            fields[name] = (pytype, Field(description=desc))
    return create_model(f"{smol_tool.name}_Args", **fields)


# smolagents 조기 종료(성공) 예외 클래스명 — LangGraph 에서는 성공 결과로 환원해야 함
_FINAL_ANSWER_EXC_NAMES = {"FinalAnswerException", "ReturnValueException", "AgentFinish"}


def _final_answer_value(exc):
    """smolagents 조기 종료 예외에서 실제 반환값(성공 result)을 추출. 아니면 None."""
    if type(exc).__name__ not in _FINAL_ANSWER_EXC_NAMES:
        return None
    for attr in ("value", "final_answer", "output"):
        if hasattr(exc, attr):
            return getattr(exc, attr)
    args = getattr(exc, "args", None)
    return args[0] if args else None


def to_langchain_tool(smol_tool):
    """단일 smolagents 툴을 LangChain StructuredTool 로 변환."""
    from langchain_core.tools import StructuredTool

    args_schema = _build_args_schema(smol_tool)

    def _run(**kwargs):
        # smolagents 툴 객체 호출 → forward 실행 (엔진 연동·반환 dict 동일)
        try:
            result = smol_tool(**kwargs)
        except Exception as e:
            # smolagents 는 apply 성공 시 FinalAnswerException 으로 CodeAgent 를 조기 종료한다.
            # LangGraph 경로에서는 이 예외를 성공 결과로 환원해야 '성공'이 '툴 에러'로
            # 오인되지 않는다 (오인 시 계획은 적용됐는데 LLM 이 실패로 판단해 재시도).
            fa = _final_answer_value(e)
            if fa is None:
                raise
            result = fa
        # LangChain ToolMessage 는 문자열을 기대 → dict/list 는 JSON 직렬화
        if isinstance(result, (dict, list)):
            try:
                return json.dumps(result, ensure_ascii=False)
            except Exception:
                return str(result)
        return result

    return StructuredTool.from_function(
        func=_run,
        name=smol_tool.name,
        description=(smol_tool.description or smol_tool.name).strip()[:1024],
        args_schema=args_schema,
    )


def build_langchain_tools() -> list:
    """smolagents 와 동일한 툴셋을 LangChain 툴 리스트로 반환."""
    from c2.presentation.agent.battlefield_agent import build_battlefield_tools

    lc_tools = []
    for smol_tool in build_battlefield_tools():
        try:
            lc_tools.append(to_langchain_tool(smol_tool))
        except Exception as e:
            logger.warning("LangChain 툴 변환 실패(%s): %s", getattr(smol_tool, "name", "?"), e)
    logger.info("LangGraph 툴 %d개 준비", len(lc_tools))
    return lc_tools
