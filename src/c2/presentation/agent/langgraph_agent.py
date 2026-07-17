"""LangGraph 기반 C2 전장 에이전트.

smolagents CodeAgent(코드 생성형 툴 호출) 대신 LangGraph StateGraph(그래프 기반
ReAct: LLM ↔ ToolNode)로 동작한다. 기능은 기존 BattlefieldAgent 와 동일하도록:

- 동일 툴셋 재사용 (c2.presentation.agent.langgraph_tools.build_langchain_tools → build_battlefield_tools)
- 동일 시스템 지시사항(config/agent_custom_instructions.txt)
- 매 판단마다 온톨로지(Neo4j) 상황 자동 주입 (_raw_run)
- 동일 호출 인터페이스: run() / agent.run() / reset_memory() / get_situation_memory() / reload_instructions()

LLM 은 기존과 동일하게 별도 vLLM 서버(OpenAI 호환)에서 서빙되며 ChatOpenAI 로 연결한다.
도구 호출은 function-calling 방식이므로 vLLM 서버를 tool-calling 활성화로 기동해야 한다
(예: --enable-auto-tool-choice --tool-call-parser hermes).
"""

from __future__ import annotations

import logging
import os

from c2.presentation.agent.battlefield_agent import (
    _load_agent_config,
    _load_custom_instructions,
    classify_intent,
)
from c2.infrastructure.llm.langgraph_llm import build_chat_llm, describe_llm_target, resolve_provider
from c2.presentation.agent.langgraph_tools import build_langchain_tools

logger = logging.getLogger(__name__)


import re as _re

_ROLE_PREAMBLE = (
    "당신은 C2(지휘통제) 군사 AI 에이전트입니다. 전장 상황 판단과 BLUFOR 임무계획 수립을 담당합니다.\n"
    "\n"
    "[동작 방식 — 매우 중요]\n"
    "- 당신은 Python 코드를 작성하지 않습니다. 도구가 필요하면 네이티브 function calling(tool call)으로 호출하세요.\n"
    "- ```py 코드 블록, `import json`, print() 같은 코드 형식은 절대 사용하지 마세요.\n"
    "- 임무계획을 요청받으면, 각 과제 지시(제공된 데이터·부대 목록·규칙)를 그대로 따르고, "
    "최종 임무계획은 반드시 하나의 JSON 블록(```json ... ```)으로 출력하세요.\n"
    "- mission_plans 는 절대 빈 배열로 두지 마세요. 계획 대상 부대를 모두 포함해야 합니다.\n"
    "\n"
    "[전술 교리 요약]\n"
    "- 표적은 탐지(detected)된 OPFOR 우선. attack/flank 부대는 담당 표적을 target_unit_id 로 명시.\n"
    "- 전투력 CP<30% 부대는 defend/withdraw. 자주포(포병)는 후방 유지(hold/defend)하며 자동 화력지원.\n"
    "- 항공 CAS(cas/strike/helicopter)는 아군 전용·5회 제한. 포병 화력지원은 횟수 제한 없이 동시 투사.\n"
    "- 아군이 적과 근접(≈1.5km) 교전 표적은 정밀타격(strike). 좌표는 위경도(WGS84) 소수점 6자리.\n"
)


def _sanitize_instructions_for_funccall(text: str) -> str:
    """smolagents 코드형 지시(코드 출력 형식/```py 블록)를 제거하고 LEARNED_RULES 등 교리만 남긴다."""
    if not text:
        return ""
    # 최상단 '코드 출력 형식/코드 블록당 도구 1개' 섹션(첫 '---' 이전)을 제거
    parts = text.split("\n---\n", 1)
    body = parts[1] if len(parts) == 2 else text
    # ```py ... ``` 코드 블록 제거 (```json 예시는 유지)
    body = _re.sub(r"```py[\s\S]*?```", "", body)
    return body.strip()


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

        self._provider = resolve_provider()
        self._llm = build_chat_llm()
        self._tools = build_langchain_tools()
        self._graph = self._build_graph()

        # agent.agent.run(...) 로 호출되는 경로(COA/공격/정찰) 호환
        self.agent = _RawRunner(self)
        self._situation_memory: dict = {}
        # 전술채팅 멀티턴 메모리 — 이전 N턴(쿼리+툴 호출/결과+최종 응답)을 저장소에서 적재·조회.
        # PostgreSQL(접속정보 있으면) 또는 in-memory 폴백. 채팅 경로(run) 전용이며,
        # 공격/정찰/COA 계획 경로(_raw_run)는 무상태로 두어 이전 대화가 섞이지 않게 한다.
        from c2.infrastructure.persistence.conversation_store import build_conversation_store

        self._conv_store = build_conversation_store()
        self._chat_session_id = os.environ.get("C2_CHAT_SESSION_ID", "wargame_chat")
        self._MEMORY_TURNS = 2
        # 활성 LLM 프로바이더를 항상 보이도록 출력 (vllm/gemini 혼동·연결오류 진단용)
        logger.warning("[C2 LLM] 활성 프로바이더=%s (%s)", self._provider, describe_llm_target())
        logger.info("LangGraphBattlefieldAgent 초기화 완료 (tools=%d)", len(self._tools))

    # ── 시스템 프롬프트 / 그래프 ──────────────────────────────────
    def _system_prompt(self) -> str:
        doctrine = _sanitize_instructions_for_funccall(self._custom_instructions or "")
        return _ROLE_PREAMBLE + "\n" + doctrine

    def _build_graph(self):
        from langgraph.prebuilt import create_react_agent

        return create_react_agent(self._llm, self._tools, prompt=self._system_prompt())

    def _recursion_limit(self) -> int:
        ca = self._agent_config.get("code_agent", {})
        return int(ca.get("max_steps", 20)) * 2 + 5

    # ── 온톨로지 상황 자동 주입 ───────────────────────────────────
    def _inject_ontology(self, task: str) -> str:
        try:
            from c2.presentation.tools.ontology_query_tool import ontology_situation_block

            block = ontology_situation_block()
        except Exception as e:
            logger.debug("온톨로지 상황 주입 실패(무시): %s", e)
            block = ""
        if block and isinstance(task, str):
            return block + "\n" + task
        return task

    # ── 오류 포맷 ──────────────────────────────────────────────────
    def _format_error(self, e: Exception) -> str:
        import json as _json

        hint = ""
        if "connection" in str(e).lower():
            if self._provider in ("gemini", "google"):
                hint = " — Gemini API 연결 실패: GOOGLE_API_KEY/네트워크를 확인하세요."
            else:
                hint = (
                    f" — vLLM 서버({describe_llm_target()})에 연결할 수 없습니다. 서버 기동 여부를 "
                    "확인하거나, Gemini API를 쓰려면 C2_LLM_PROVIDER=gemini + GOOGLE_API_KEY 를 "
                    "설정하세요."
                )
        logger.error("LangGraph 실행 오류(provider=%s): %s%s", self._provider, e, hint, exc_info=True)
        return _json.dumps(
            {"status": "error", "message": f"{e}{hint}", "provider": self._provider},
            ensure_ascii=False,
        )

    # ── 실행 (무상태 — 공격/정찰/COA 계획 경로) ────────────────────
    def _raw_run(self, query: str, reset: bool = False) -> str:
        """그래프 1회 실행 — 온톨로지 상황 주입 후 최종 응답 텍스트 반환 (히스토리 미사용).

        create_react_agent 는 invoke 마다 stateless 이므로 reset 은 자연히 보장됨.
        """
        task = self._inject_ontology(query)
        try:
            result = self._graph.invoke(
                {"messages": [("user", task)]},
                config={"recursion_limit": self._recursion_limit()},
            )
        except Exception as e:
            return self._format_error(e)
        msgs = result.get("messages", []) if isinstance(result, dict) else []
        return self._extract_result_text(msgs)

    # ── 결과 텍스트 추출 ──────────────────────────────────────────
    @staticmethod
    def _message_text(msg) -> str:
        """LangChain 메시지의 content 를 평문 문자열로 정규화 (list/dict parts 포함)."""
        content = getattr(msg, "content", msg)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, str):
                    parts.append(p)
                elif isinstance(p, dict):
                    parts.append(p.get("text") or p.get("content") or "")
            return "\n".join(x for x in parts if x).strip()
        return str(content).strip() if content is not None else ""

    def _extract_result_text(self, msgs: list) -> str:
        """그래프 실행 메시지에서 gradio 가 파싱할 최종 텍스트를 뽑는다.

        Gemini 등 function-calling 모델은 apply 툴 호출 후 **마지막 AIMessage content 가
        비어 있는** 경우가 흔하다. 이때 계획은 이미 툴이 적용했으므로, 뒤에서부터 스캔해
        (1) 비어있지 않은 응답 텍스트, 또는 (2) 툴 성공/JSON payload 를 surface 한다.
        이렇게 해야 gradio 가 mission_plans / '"status": "success"' 를 감지해 폴백을 피한다.
        """
        if not msgs:
            logger.warning("[LangGraph] 결과 메시지가 비어 있음")
            return ""
        # 1) 뒤에서부터 첫 번째 비어있지 않은 메시지 텍스트
        for msg in reversed(msgs):
            text = self._message_text(msg)
            if text:
                return text
        # 2) 모든 메시지가 비어 있으면 마지막 메시지 원본을 문자열화
        logger.warning(
            "[LangGraph] 모든 메시지 content 가 비어 있음 (msgs=%d, 마지막=%s)",
            len(msgs), type(msgs[-1]).__name__,
        )
        return str(getattr(msgs[-1], "content", "") or "")

    # ── 실행 (멀티턴 — 전술채팅 경로) ─────────────────────────────
    def run(self, query: str, reset: bool = False) -> str:
        """전술채팅 실행 — 이전 N턴(쿼리+툴 호출/결과+최종 응답)을 저장소에서 적재해
        현재 쿼리 앞에 붙여 멀티턴 대화를 지원한다. 실행 후 이번 턴을 저장소에 적재한다.
        """
        try:
            intent = classify_intent(query)
            logger.info("Intent: %s", intent.get("intent", "general"))
        except Exception:
            pass

        from langchain_core.messages import (
            HumanMessage, messages_from_dict, messages_to_dict,
        )

        if reset:
            try:
                self._conv_store.clear(self._chat_session_id)
            except Exception:
                pass

        # 이전 N턴 메시지 적재 (오래된 것 먼저) → LangChain 메시지로 복원
        prior_msgs = []
        try:
            turns = self._conv_store.recent_turns(self._chat_session_id, self._MEMORY_TURNS)
            prior_dicts = [m for turn in turns for m in turn]
            if prior_dicts:
                prior_msgs = list(messages_from_dict(prior_dicts))
        except Exception as e:
            logger.warning("[대화메모리] 이전 턴 적재 실패(무시): %s", e)
            prior_msgs = []

        task = self._inject_ontology(query)
        input_messages = prior_msgs + [HumanMessage(content=task)]
        try:
            result = self._graph.invoke(
                {"messages": input_messages},
                config={"recursion_limit": self._recursion_limit()},
            )
        except Exception as e:
            return self._format_error(e)

        all_msgs = result.get("messages", []) if isinstance(result, dict) else []
        # 이번 턴에 해당하는 메시지 (이전 히스토리 이후) — 저장·응답 추출용
        turn_msgs = list(all_msgs[len(prior_msgs):]) if all_msgs else []
        # 저장 시 온톨로지 블록이 붙은 human 을 원본 쿼리로 치환 (컨텍스트 절약)
        if turn_msgs:
            turn_msgs[0] = HumanMessage(content=query)
            try:
                self._conv_store.append_turn(
                    self._chat_session_id, messages_to_dict(turn_msgs)
                )
            except Exception as e:
                logger.warning("[대화메모리] 이번 턴 저장 실패(무시): %s", e)

        # 이번 턴 응답만 추출 (이전 히스토리 텍스트가 섞이지 않도록)
        return self._extract_result_text(turn_msgs if turn_msgs else all_msgs)

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
