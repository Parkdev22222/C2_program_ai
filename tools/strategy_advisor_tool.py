"""
상황 분석 세션 메모리

EXAONE4가 생성한 최신 전장 상황 분석 텍스트를 세션 단위로 보관합니다.
gradio_app.py(메모리 패널, 채팅 후 갱신)와 battlefield_agent.py에서 사용합니다.

참고: 과거 이 모듈에 있던 EXAONE Deep 어드바이저 툴
(StrategyAdvisorTool / ReconAdvisorTool)은 제거되었습니다.
"""
import logging

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 세션 메모리: EXAONE4의 상황 분석 결과 저장소
# ─────────────────────────────────────────────
_session_memory: dict = {
    "situation_analysis": None,   # 가장 최근 EXAONE4 상황 분석 텍스트
    "analysis_timestamp": None,   # 분석 시각
}


def update_situation_memory(analysis_text: str, video_ids: list = None):
    """
    EXAONE4가 상황 분석을 완료한 후 호출하여 세션 메모리를 갱신합니다.
    (video_ids 인자는 하위 호환용으로만 남아 있으며 무시됩니다.)
    """
    from datetime import datetime
    _session_memory["situation_analysis"] = analysis_text
    _session_memory["analysis_timestamp"] = datetime.now().isoformat()
    logger.info("Situation memory updated with latest EXAONE4 analysis")


def get_situation_memory() -> dict:
    """현재 세션 메모리의 상황 분석 정보를 반환합니다."""
    return dict(_session_memory)


def clear_situation_memory():
    """세션 메모리를 초기화합니다."""
    _session_memory["situation_analysis"] = None
    _session_memory["analysis_timestamp"] = None
