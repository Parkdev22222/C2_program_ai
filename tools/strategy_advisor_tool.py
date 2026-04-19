"""
전략 어드바이저 툴 - EXAONE4와 EXAONE Deep 간의 듀얼 모델 오케스트레이션

동작 흐름:
1. EXAONE4가 군사 전략/전술 추천 쿼리 감지
2. EXAONE4가 strategy_advisor_tool 호출
3. 이 툴이 세션 메모리에서 EXAONE4의 이전 상황 분석 결과를 가져옴
4. EXAONE Deep에 [상황 분석 + 사용자 쿼리]를 전달하여 전략/전술 권고 생성
5. EXAONE Deep의 권고 결과를 EXAONE4에게 반환
6. EXAONE4가 자신의 상황 분석 + EXAONE Deep 권고를 종합하여 최종 응답
"""
import logging
from typing import Optional, Any
from smolagents import Tool

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 세션 메모리: EXAONE4의 상황 분석 결과 저장소
# gradio_app.py에서 EXAONE4 응답 후 업데이트됨
# ─────────────────────────────────────────────
_session_memory: dict = {
    "situation_analysis": None,   # 가장 최근 EXAONE4 상황 분석 텍스트
    "video_ids": [],              # 분석에 사용된 영상 ID 목록
    "analysis_timestamp": None,   # 분석 시각
}


def update_situation_memory(analysis_text: str, video_ids: list = None):
    """
    EXAONE4가 상황 분석을 완료한 후 호출하여 세션 메모리를 갱신합니다.
    gradio_app.py 또는 battlefield_agent.py에서 호출됩니다.
    """
    from datetime import datetime
    _session_memory["situation_analysis"] = analysis_text
    _session_memory["video_ids"] = video_ids or []
    _session_memory["analysis_timestamp"] = datetime.now().isoformat()
    logger.info("Situation memory updated with latest EXAONE4 analysis")


def get_situation_memory() -> dict:
    """현재 세션 메모리의 상황 분석 정보를 반환합니다."""
    return dict(_session_memory)


def clear_situation_memory():
    """세션 메모리를 초기화합니다."""
    _session_memory["situation_analysis"] = None
    _session_memory["video_ids"] = []
    _session_memory["analysis_timestamp"] = None


# ─────────────────────────────────────────────
# EXAONE Deep 모델 인스턴스 (전역 싱글톤)
# ─────────────────────────────────────────────
_strategy_model = None


def get_strategy_model():
    """EXAONE Deep 모델을 지연 로딩으로 가져옵니다."""
    global _strategy_model
    if _strategy_model is None:
        try:
            from agent.strategy_model_loader import load_strategy_model_from_config_file
            logger.info("Loading EXAONE Deep strategy model (lazy load)...")
            _strategy_model = load_strategy_model_from_config_file()
        except Exception as e:
            logger.error(f"Failed to load EXAONE Deep model: {e}")
            raise RuntimeError(f"EXAONE Deep 모델 로딩 실패: {e}")
    return _strategy_model


def set_strategy_model(model):
    """외부에서 주입한 EXAONE Deep 모델 인스턴스를 설정합니다 (테스트/초기화용)."""
    global _strategy_model
    _strategy_model = model


# ─────────────────────────────────────────────
# EXAONE Deep 프롬프트 빌더
# ─────────────────────────────────────────────

STRATEGY_SYSTEM_PROMPT = """당신은 EXAONE Deep 기반 군사 전략/전술 전문가 AI입니다.
제공된 전장 상황 분석 정보를 바탕으로 구체적이고 실행 가능한 군사 전략 및 전술을 추천합니다.

추천 시 다음 원칙을 준수하세요:
1. 제공된 상황 정보에 근거한 현실적인 권고만 제시
2. 아군 피해 최소화 원칙 우선
3. 단기/중기/장기 행동 계획으로 구분하여 제시
4. 각 전략/전술의 위험도와 효과를 평가하여 제시
5. 군사 전술 원칙(기습, 집중, 경제, 기동, 통일 등)을 명시적으로 적용"""

STRATEGY_USER_TEMPLATE = """## 전장 상황 분석 (EXAONE4 제공)

{situation_analysis}

---

## 전략/전술 추천 요청

{user_query}

위 상황 분석을 바탕으로 구체적인 군사 전략 및 전술을 추천해주세요."""

NO_SITUATION_TEMPLATE = """## 전략/전술 추천 요청

{user_query}

주의: 사전 영상 분석 결과가 없습니다. 일반적인 군사 원칙에 기반하여 답변합니다.
실제 운용 시에는 먼저 전장 영상을 분석한 후 전략/전술 쿼리를 입력하시기 바랍니다."""


class StrategyAdvisorTool(Tool):
    """
    EXAONE Deep 전략/전술 어드바이저 툴

    EXAONE4가 이 툴을 호출하면:
    - 세션 메모리에서 이전 상황 분석 결과를 가져옴
    - EXAONE Deep에 상황 분석 + 쿼리를 전달
    - EXAONE Deep의 전략/전술 권고 결과를 EXAONE4에게 반환
    """

    name = "strategy_advisor_tool"
    description = (
        "군사 전략 및 전술 추천을 위해 EXAONE Deep 전문 모델을 호출합니다. "
        "전략, 전술, 작전계획, 기동방안, 화력지원, 방어계획 등 군사적 행동 권고가 필요할 때 사용하세요. "
        "이 툴은 이전에 EXAONE4가 수행한 전장 상황 분석 결과를 자동으로 EXAONE Deep에 전달하므로, "
        "반드시 먼저 영상 분석을 수행한 후에 이 툴을 사용해야 합니다."
    )
    inputs = {
        "query": {
            "type": "string",
            "description": (
                "전략/전술 추천 요청 내용. 예: '적 기갑부대 탐지 시 아군 보병 전술 추천', "
                "'현재 전장 상황에서 방어 거점 구축 방안', '포위 기동을 위한 작전 계획'"
            ),
        },
        "additional_context": {
            "type": "string",
            "description": "추가 맥락 정보 (선택 사항). 아군 전력, 지형 정보 등 보완 정보.",
            "nullable": True,
        },
    }
    output_type = "string"

    def forward(self, query: str, additional_context: Optional[str] = None) -> str:
        logger.info(f"StrategyAdvisorTool called with query: {query[:100]}...")

        # 세션 메모리에서 상황 분석 가져오기
        memory = get_situation_memory()
        situation_analysis = memory.get("situation_analysis")
        analysis_timestamp = memory.get("analysis_timestamp")

        # EXAONE Deep 프롬프트 구성
        if situation_analysis:
            context_note = ""
            if analysis_timestamp:
                context_note = f"\n(분석 시각: {analysis_timestamp})"
            if additional_context:
                situation_with_extra = (
                    f"{situation_analysis}{context_note}\n\n"
                    f"### 추가 맥락 정보\n{additional_context}"
                )
            else:
                situation_with_extra = f"{situation_analysis}{context_note}"

            user_content = STRATEGY_USER_TEMPLATE.format(
                situation_analysis=situation_with_extra,
                user_query=query,
            )
        else:
            logger.warning("No situation analysis in memory. Using query-only mode.")
            user_content = NO_SITUATION_TEMPLATE.format(user_query=query)

        messages = [
            {"role": "system", "content": STRATEGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # EXAONE Deep 모델 호출
        try:
            strategy_model = get_strategy_model()
            gen_kwargs = getattr(strategy_model, "_strategy_generation_kwargs", {})
            temperature = gen_kwargs.get("temperature", 0.2)
            max_tokens = gen_kwargs.get("max_tokens", 8192)

            logger.info("Calling EXAONE Deep for strategy/tactics recommendation...")
            response = strategy_model(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            result = self._extract_text(response)
            logger.info("EXAONE Deep strategy response received successfully")
            return result

        except Exception as e:
            logger.error(f"EXAONE Deep call failed: {e}")
            return (
                f"[EXAONE Deep 전략 모델 호출 실패]\n"
                f"오류: {e}\n\n"
                f"전략/전술 추천을 위해 EXAONE Deep 모델이 필요합니다. "
                f"모델 설정을 확인해주세요."
            )

    def _extract_text(self, response: Any) -> str:
        if isinstance(response, str):
            return response
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, list):
                parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
                return "".join(parts)
            return str(content)
        return str(response)


def create_strategy_advisor_tool(strategy_model=None) -> StrategyAdvisorTool:
    """
    StrategyAdvisorTool 인스턴스를 생성합니다.

    Args:
        strategy_model: 미리 로드된 EXAONE Deep 모델. None이면 지연 로딩.
    """
    if strategy_model is not None:
        set_strategy_model(strategy_model)
    return StrategyAdvisorTool()
