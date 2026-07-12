"""LangGraph 기반 C2 전장 에이전트.

smolagents CodeAgent(코드 생성형 툴 호출) 대신 LangGraph StateGraph(그래프 기반
ReAct: LLM ↔ ToolNode)로 동작한다. 기능은 기존 BattlefieldAgent 와 동일하도록:

- 동일 툴셋 재사용 (agent.langgraph_tools.build_langchain_tools → build_battlefield_tools)
- 동일 시스템 지시사항(config/agent_custom_instructions.txt)
- 매 판단마다 온톨로지(Neo4j) 상황 자동 주입 (_raw_run)
- 동일 호출 인터페이스: run() / agent.run() / reset_memory() / get_situation_memory() / reload_instructions()

LLM 은 기존과 동일하게 별도 vLLM 서버(OpenAI 호환)에서 서빙되며 ChatOpenAI 로 연결한다.
도구 호출은 function-calling 방식이므로 vLLM 서버를 tool-calling 활성화로 기동해야 한다
(예: --enable-auto-tool-choice --tool-call-parser hermes).
"""

from __future__ import annotations

import logging

from agent.battlefield_agent import (
    _load_agent_config,
    _load_custom_instructions,
    classify_intent,
)
from agent.langgraph_llm import build_chat_llm
from agent.langgraph_tools import build_langchain_tools

logger = logging.getLogger(__name__)


_ROLE_PREAMBLE = (
    "당신은 EXAONE4 기반 C2(지휘통제) 군사 AI 에이전트입니다. 전장 상황 판단과 "
    "BLUFOR 임무계획 수립을 담당합니다.\n"
    "도구는 **function calling(tool call)** 으로 호출합니다. 아래 지시사항의 "
    "'코드 출력 형식/코드 블록' 관련 규칙(smolagents 전용)은 무시하고, 필요한 도구를 "
    "tool call 로 사용하세요. 최종 임무계획은 반드시 JSON 블록으로 응답에 출력하세요.\n"
)


class _RawRunner:
    """agent.agent.run(...) 호환용 얇은 래퍼 (smolagents CodeAgent 자리 대체)."""

    def __init__(self, parent: "LangGraphBattlefieldAgent") -> None:
        self._parent = parent

    def run(self, query: str, reset: bool = False):
        return self._parent._raw_run(query, reset=reset)


class LangGraphBattlefieldAgent:
    """LangGraph StateGraph 기반 전장 에이전트 (BattlefieldAgent 호환 인터페이스)."""

    def __init__(self, exaone4_model=None):
        # exaone4_model 인자는 인터페이스 호환용 — LangGraph 는 vLLM 엔드포인트에 직접 연결
        self._agent_config = _load_agent_config()
        self._custom_instructions = _load_custom_instructions()
        self._exaone4_model = exaone4_model

        self._llm = build_chat_llm()
        self._tools = build_langchain_tools()
        self._graph = self._build_graph()

        # agent.agent.run(...) 로 호출되는 경로(COA/공격/정찰) 호환
        self.agent = _RawRunner(self)
        self._situation_memory: dict = {}
        logger.info("LangGraphBattlefieldAgent 초기화 완료 (tools=%d)", len(self._tools))

    # ── 시스템 프롬프트 / 그래프 ──────────────────────────────────
    def _system_prompt(self) -> str:
        return _ROLE_PREAMBLE + "\n" + (self._custom_instructions or "")

    def _build_graph(self):
        from langgraph.prebuilt import create_react_agent

        return create_react_agent(self._llm, self._tools, prompt=self._system_prompt())

    def _recursion_limit(self) -> int:
        ca = self._agent_config.get("code_agent", {})
        return int(ca.get("max_steps", 20)) * 2 + 5

    # ── 온톨로지 상황 자동 주입 ───────────────────────────────────
    def _inject_ontology(self, task: str) -> str:
        try:
            from tools.ontology_query_tool import ontology_situation_block

            block = ontology_situation_block()
        except Exception as e:
            logger.debug("온톨로지 상황 주입 실패(무시): %s", e)
            block = ""
        if block and isinstance(task, str):
            return block + "\n" + task
        return task

    # ── 실행 ───────────────────────────────────────────────────────
    def _raw_run(self, query: str, reset: bool = False) -> str:
        """그래프 1회 실행 — 온톨로지 상황 주입 후 최종 응답 텍스트 반환.

        create_react_agent 는 invoke 마다 stateless 이므로 reset 은 자연히 보장됨.
        """
        task = self._inject_ontology(query)
        try:
            result = self._graph.invoke(
                {"messages": [("user", task)]},
                config={"recursion_limit": self._recursion_limit()},
            )
        except Exception as e:
            logger.error("LangGraph 실행 오류: %s", e, exc_info=True)
            return f'{{"status": "error", "message": "{e}"}}'
        msgs = result.get("messages", []) if isinstance(result, dict) else []
        if not msgs:
            return ""
        content = getattr(msgs[-1], "content", msgs[-1])
        return str(content)

    def run(self, query: str, reset: bool = False) -> str:
        try:
            intent = classify_intent(query)
            logger.info("Intent: %s", intent.get("intent", "general"))
        except Exception:
            pass
        return self._raw_run(query, reset=reset)

    # ── BattlefieldAgent 호환 메서드 ──────────────────────────────
    def reset_memory(self) -> None:
        # create_react_agent 는 invoke 마다 stateless → 별도 리셋 불필요
        pass

    def get_situation_memory(self) -> dict:
        return dict(self._situation_memory)

    def reload_instructions(self):
        self._custom_instructions = _load_custom_instructions()
        self._graph = self._build_graph()
        logger.info("Instructions reloaded (graph 재구성)")

    @property
    def exaone4_model(self):
        return self._exaone4_model
