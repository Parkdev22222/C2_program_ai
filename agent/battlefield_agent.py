"""
C2 군사 AI 에이전트 - EXAONE4 기반 듀얼 모델 아키텍처

역할:
- EXAONE4: 메인 CodeAgent. 영상 분석, 상황 판단, 최종 응답 생성
- EXAONE Deep: 전략/전술 전문 모델. strategy_advisor_tool을 통해 EXAONE4가 호출

흐름:
1. 영상 분석 쿼리 → EXAONE4가 직접 비디오 도구를 사용하여 상황 분석 및 응답
   → 응답 후 situation_memory 자동 갱신
2. 전략/전술 쿼리 → EXAONE4가 strategy_advisor_tool 호출
   → EXAONE Deep이 [EXAONE4 상황 분석 + 사용자 쿼리]로 전략/전술 권고 생성
   → EXAONE4가 [자신의 상황 분석 + EXAONE Deep 권고]를 종합하여 최종 응답
"""
import re
import yaml
import logging
from pathlib import Path
from typing import List, Optional, Any

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_agent_config() -> dict:
    with open(CONFIG_DIR / "agent_config.yaml") as f:
        return yaml.safe_load(f)


def _load_custom_instructions() -> str:
    instr_file = CONFIG_DIR / "agent_custom_instructions.txt"
    if instr_file.exists():
        return instr_file.read_text(encoding="utf-8")
    return ""


def is_strategy_query(text: str, config: dict = None) -> bool:
    """
    사용자 쿼리가 군사 전략/전술 추천 쿼리인지 판별합니다.
    agent_config.yaml의 strategy_keywords를 기준으로 판별합니다.
    """
    if config is None:
        cfg = _load_agent_config()
    else:
        cfg = config
    keywords = cfg.get("strategy_keywords", {})
    text_lower = text.lower()
    for lang_keywords in keywords.values():
        for kw in lang_keywords:
            if kw.lower() in text_lower:
                return True
    return False


class BattlefieldAgent:
    """
    EXAONE4 기반 C2 군사 AI 에이전트

    smolagents CodeAgent를 래핑하여 다음 기능을 제공합니다:
    - 비디오 분석 도구 통합
    - 전략 쿼리 감지 및 EXAONE Deep 호출 (strategy_advisor_tool)
    - 상황 분석 메모리 자동 갱신
    - 컨텍스트(비디오/PDF) 관리
    """

    def __init__(
        self,
        exaone4_model=None,
        strategy_model=None,
        videodb_manager=None,
        embedding_generator=None,
        pdf_rag_system=None,
    ):
        self._agent_config = _load_agent_config()
        self._custom_instructions = _load_custom_instructions()
        self._videodb_manager = videodb_manager
        self._embedding_generator = embedding_generator
        self._pdf_rag_system = pdf_rag_system

        # 선택된 비디오/PDF 컨텍스트
        self._selected_video_ids: List[str] = []
        self._selected_pdf_ids: List[str] = []

        # 모델 로딩
        self._exaone4_model = exaone4_model or self._load_exaone4()
        self._strategy_model = strategy_model or self._load_strategy_model()

        # 툴 초기화
        self._tools = self._build_tools()

        # smolagents CodeAgent 초기화
        self._agent = self._init_code_agent()

    def _load_exaone4(self):
        try:
            from agent.model_loader import load_model_from_config_file
            return load_model_from_config_file()
        except Exception as e:
            logger.error(f"Failed to load EXAONE4 model: {e}")
            raise

    def _load_strategy_model(self):
        try:
            from agent.strategy_model_loader import load_strategy_model_from_config_file
            return load_strategy_model_from_config_file()
        except Exception as e:
            logger.warning(f"Failed to load EXAONE Deep model: {e}. Strategy tool will fail if used.")
            return None

    def _build_tools(self) -> list:
        tools = []

        # 비디오 쿼리 도구
        try:
            from tools.videodb_query_tool import (
                get_selected_contexts,
                query_video_semantic,
                query_video_by_object,
                query_video_by_event,
                get_video_summary,
                get_segment_details,
                set_active_videos,
            )
            tools.extend([
                get_selected_contexts,
                query_video_semantic,
                query_video_by_object,
                query_video_by_event,
                get_video_summary,
                get_segment_details,
                set_active_videos,
            ])
            logger.info("VideoDB query tools loaded")
        except Exception as e:
            logger.warning(f"Failed to load videodb tools: {e}")

        # PDF RAG 도구
        try:
            from tools.pdf_rag_tool import pdf_rag_search, add_pdf_to_rag
            tools.extend([pdf_rag_search, add_pdf_to_rag])
            logger.info("PDF RAG tools loaded")
        except Exception as e:
            logger.warning(f"Failed to load PDF RAG tools: {e}")

        # 워게임 쿼리 도구
        try:
            from tools.wargame_query_tool import (
                get_tactical_situation,
                get_friendly_units,
                get_hostile_units,
                get_unit_details,
                get_units_by_type,
            )
            tools.extend([
                get_tactical_situation,
                get_friendly_units,
                get_hostile_units,
                get_unit_details,
                get_units_by_type,
            ])
            logger.info("Wargame query tools loaded")
        except Exception as e:
            logger.warning(f"Failed to load wargame tools: {e}")

        # 전략 어드바이저 도구 (핵심 신규 도구)
        try:
            from tools.strategy_advisor_tool import create_strategy_advisor_tool
            strategy_tool = create_strategy_advisor_tool(self._strategy_model)
            tools.append(strategy_tool)
            logger.info("Strategy advisor tool (EXAONE Deep) loaded")
        except Exception as e:
            logger.warning(f"Failed to load strategy advisor tool: {e}")

        return tools

    def _init_code_agent(self):
        from smolagents import CodeAgent

        ca_cfg = self._agent_config.get("code_agent", {})
        return CodeAgent(
            tools=self._tools,
            model=self._exaone4_model,
            max_steps=ca_cfg.get("max_steps", 20),
            planning_interval=ca_cfg.get("planning_interval", 3),
            stream_outputs=ca_cfg.get("stream_outputs", False),
            additional_authorized_imports=ca_cfg.get("authorized_imports", []),
            system_prompt=self._custom_instructions or None,
        )

    # ─────────────────────────────────────────────────────────────
    # 공개 API
    # ─────────────────────────────────────────────────────────────

    def run(self, query: str, reset: bool = False) -> str:
        """
        쿼리를 실행합니다.

        전략/전술 쿼리인 경우 에이전트의 커스텀 지시사항에 따라
        자동으로 strategy_advisor_tool을 사용합니다.

        Args:
            query: 사용자 입력 쿼리
            reset: True이면 에이전트 메모리를 초기화

        Returns:
            에이전트의 최종 응답 텍스트
        """
        if is_strategy_query(query, self._agent_config):
            logger.info("Strategy/tactics query detected → agent will use strategy_advisor_tool")
            augmented_query = self._augment_strategy_query(query)
        else:
            augmented_query = query

        logger.info(f"Running agent with query: {query[:80]}...")
        result = self._agent.run(augmented_query, reset=reset)
        return str(result)

    def _augment_strategy_query(self, query: str) -> str:
        """
        전략/전술 쿼리에 strategy_advisor_tool 사용 지시를 명시적으로 추가합니다.
        에이전트가 커스텀 지시사항을 놓치지 않도록 보장합니다.
        """
        return (
            f"{query}\n\n"
            f"[중요] 이 쿼리는 군사 전략/전술 추천 요청입니다. "
            f"반드시 strategy_advisor_tool을 호출하여 EXAONE Deep의 전략/전술 권고를 받은 후, "
            f"나의 이전 상황 분석과 EXAONE Deep의 권고를 종합하여 최종 응답을 작성하세요."
        )

    def set_video_context(self, video_ids: List[str]):
        """에이전트가 쿼리할 비디오 컨텍스트를 설정합니다."""
        self._selected_video_ids = video_ids
        try:
            from tools.videodb_query_tool import set_selected_video_ids
            set_selected_video_ids(video_ids)
        except Exception as e:
            logger.warning(f"Failed to update videodb tool context: {e}")
        logger.info(f"Video context set: {video_ids}")

    def set_pdf_context(self, pdf_ids: List[str]):
        """에이전트가 쿼리할 PDF 컨텍스트를 설정합니다."""
        self._selected_pdf_ids = pdf_ids
        try:
            from tools.pdf_rag_tool import set_selected_pdfs
            set_selected_pdfs(pdf_ids)
        except Exception as e:
            logger.warning(f"Failed to update PDF tool context: {e}")
        logger.info(f"PDF context set: {pdf_ids}")

    def add_pdf(self, pdf_path: str) -> str:
        """PDF를 RAG 시스템에 추가합니다."""
        try:
            from tools.pdf_rag_tool import add_pdf_to_rag
            result = add_pdf_to_rag(pdf_path)
            return str(result)
        except Exception as e:
            logger.error(f"Failed to add PDF: {e}")
            return f"PDF 추가 실패: {e}"

    def get_situation_memory(self) -> dict:
        """현재 세션의 상황 분석 메모리를 반환합니다."""
        from tools.strategy_advisor_tool import get_situation_memory
        return get_situation_memory()

    @property
    def agent(self):
        return self._agent

    @property
    def exaone4_model(self):
        return self._exaone4_model

    @property
    def strategy_model(self):
        return self._strategy_model
