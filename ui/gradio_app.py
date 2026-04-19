"""
C2 군사 AI - Gradio 웹 인터페이스

핵심 기능:
1. 군사 영상 업로드 및 분석 (EXAONE4)
2. EXAONE4 상황 분석 응답 → situation_memory 자동 갱신
3. 전략/전술 쿼리 시 EXAONE4 → EXAONE Deep → EXAONE4 파이프라인 자동 실행
4. 채팅 히스토리 기반 컨텍스트 유지

EXAONE4는 영상 분석 결과를 situation_memory에 저장하고,
이후 전략/전술 쿼리 시 strategy_advisor_tool이 이를 EXAONE Deep에 자동 전달합니다.
"""
import re
import time
import logging
import gradio as gr
import yaml
from pathlib import Path
from typing import List, Optional, Tuple, Generator

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_ui_config() -> dict:
    with open(CONFIG_DIR / "agent_config.yaml") as f:
        return yaml.safe_load(f).get("gradio", {})


# ─────────────────────────────────────────────
# 전역 상태
# ─────────────────────────────────────────────
_agent = None
_video_analysis_system = None
_analyzed_videos: List[dict] = []       # {"video_id", "filename", "summary"}
_active_video_ids: List[str] = []       # 현재 선택된 비디오 IDs
_last_situation_analysis: str = ""      # EXAONE4의 마지막 상황 분석 텍스트


def _get_agent():
    global _agent
    return _agent


def _is_situation_analysis_response(response: str) -> bool:
    """
    EXAONE4의 응답이 상황 분석 결과인지 판별합니다.
    전장 상황 분석 보고서 형식이거나 탐지 정보를 포함하면 True를 반환합니다.
    """
    situation_markers = [
        "전장 상황 분석",
        "탐지된 전력",
        "상황 분석 보고서",
        "battlefield situation",
        "situation analysis",
        "detected",
        "탐지",
        "이동 패턴",
        "threat",
        "위협",
        "tank", "soldier", "truck", "전차", "병력",
    ]
    response_lower = response.lower()
    matched = sum(1 for m in situation_markers if m.lower() in response_lower)
    return matched >= 2


def _update_situation_memory_if_needed(response: str, video_ids: List[str] = None):
    """
    EXAONE4 응답이 상황 분석 결과이면 situation_memory를 갱신합니다.
    strategy_advisor_tool이 이를 읽어 EXAONE Deep에 전달합니다.
    """
    global _last_situation_analysis
    if _is_situation_analysis_response(response):
        _last_situation_analysis = response
        try:
            from tools.strategy_advisor_tool import update_situation_memory
            update_situation_memory(response, video_ids or _active_video_ids)
            logger.info("Situation memory updated from EXAONE4 analysis response")
        except Exception as e:
            logger.warning(f"Failed to update situation memory: {e}")


def _is_strategy_query(text: str) -> bool:
    """사용자 쿼리가 전략/전술 추천 요청인지 판별합니다."""
    try:
        from agent.battlefield_agent import is_strategy_query
        return is_strategy_query(text)
    except Exception:
        strategy_kw = ["전략", "전술", "작전", "기동", "대응방안", "추천", "제안",
                       "strategy", "tactics", "maneuver", "recommend"]
        text_lower = text.lower()
        return any(kw in text_lower for kw in strategy_kw)


# ─────────────────────────────────────────────
# 비디오 분석 함수
# ─────────────────────────────────────────────

def analyze_video(video_file, collection_name: str, progress=gr.Progress()):
    """
    업로드된 군사 영상을 분석합니다.
    EXAONE4가 이후 상황 분석에 사용할 수 있도록 DB에 저장합니다.
    """
    global _video_analysis_system, _analyzed_videos, _active_video_ids

    choices = _get_video_list_choices()
    if video_file is None:
        return "영상 파일을 먼저 업로드하세요.", gr.update(choices=choices, value=choices)

    try:
        progress(0.1, desc="영상 분석 시스템 초기화 중...")
        if _video_analysis_system is None:
            from core_src.video_analysis_system import VideoAnalysisSystem
            _video_analysis_system = VideoAnalysisSystem(collection_name=collection_name or "default")

        progress(0.3, desc="비디오 분석 중 (객체 탐지, 임베딩 생성)...")
        summary = _video_analysis_system.analyze_video(
            video_path=video_file.name if hasattr(video_file, "name") else str(video_file),
        )

        video_id = summary["video_id"]
        _analyzed_videos.append({
            "video_id": video_id,
            "filename": Path(video_file.name if hasattr(video_file, "name") else str(video_file)).name,
            "summary": summary,
        })
        _active_video_ids = [v["video_id"] for v in _analyzed_videos]

        # 도구 컨텍스트 업데이트
        try:
            from tools.videodb_query_tool import (
                set_selected_video_ids,
                register_videodb_manager,
                register_video_collection,
            )
            set_selected_video_ids(_active_video_ids)
            register_videodb_manager(collection_name or "default", _video_analysis_system.videodb)
            register_video_collection(video_id, collection_name or "default")
        except Exception as e:
            logger.warning(f"Failed to update tool context: {e}")

        if _agent:
            _agent.set_video_context(_active_video_ids)

        progress(1.0, desc="분석 완료!")

        # 분석 결과 요약 메시지
        obj_counts = summary.get("segments", [])
        total_dets = summary.get("total_detections", 0)
        result_msg = (
            f"✓ 영상 분석 완료\n"
            f"  - 비디오 ID: {video_id}\n"
            f"  - 총 길이: {summary.get('duration', 0):.1f}초\n"
            f"  - 세그먼트 수: {summary.get('segment_count', 0)}개\n"
            f"  - 탐지된 객체 수: {total_dets}건\n\n"
            f"이제 채팅창에서 영상에 대해 질문하거나 전략/전술 추천을 요청할 수 있습니다."
        )
        new_choices = _get_video_list_choices()
        # gr.update로 choices와 value를 함께 설정해야 CheckboxGroup이 정상 동작
        return result_msg, gr.update(choices=new_choices, value=new_choices)

    except Exception as e:
        logger.error(f"Video analysis error: {e}", exc_info=True)
        choices = _get_video_list_choices()
        return f"분석 오류: {e}", gr.update(choices=choices, value=choices)


def _get_video_list_choices() -> list:
    return [f"{v['video_id']} - {v['filename']}" for v in _analyzed_videos]


def update_active_videos(selected_items: List[str]) -> str:
    """UI에서 선택된 비디오를 활성 컨텍스트로 설정합니다."""
    global _active_video_ids
    _active_video_ids = []
    for item in (selected_items or []):
        vid = item.split(" - ")[0].strip()
        _active_video_ids.append(vid)

    try:
        from tools.videodb_query_tool import set_selected_video_ids
        set_selected_video_ids(_active_video_ids)
    except Exception as e:
        logger.warning(f"Failed to update video context: {e}")

    if _agent:
        _agent.set_video_context(_active_video_ids)

    return f"활성 비디오: {len(_active_video_ids)}개 선택됨"


# ─────────────────────────────────────────────
# 채팅 처리 함수 (핵심 로직)
# ─────────────────────────────────────────────

def chat(
    message: str,
    history: List[Tuple[str, str]],
) -> Tuple[str, List[Tuple[str, str]]]:
    """
    사용자 메시지를 처리합니다.

    처리 흐름:
    1. 영상 분석 쿼리 → EXAONE4가 비디오 도구로 직접 처리
       → 응답이 상황 분석이면 situation_memory 자동 갱신
    2. 전략/전술 쿼리 → EXAONE4가 strategy_advisor_tool 호출
       → EXAONE Deep이 [상황 분석 + 쿼리]로 전략/전술 권고 생성
       → EXAONE4가 최종 종합 응답 반환
    """
    if not message.strip():
        return "", history

    agent = _get_agent()
    if agent is None:
        history.append((message, "에이전트가 초기화되지 않았습니다. main.py를 통해 실행해주세요."))
        return "", history

    # 전략/전술 쿼리 감지 → 사전 상황 분석 확인
    is_strategy = _is_strategy_query(message)
    if is_strategy and not _last_situation_analysis:
        warning = (
            "[안내] 전략/전술 추천을 위해서는 먼저 군사 영상을 분석하는 것을 권장합니다.\n"
            "상황 분석 없이도 일반적인 군사 원칙 기반으로 추천이 가능하지만, "
            "영상 분석 후 쿼리하면 더 정확한 상황 기반 추천을 받을 수 있습니다.\n\n"
        )
        history.append((message, warning + "처리 중..."))
    else:
        history.append((message, "처리 중..."))

    try:
        response = agent.run(message)
        response_text = str(response)

        # EXAONE4 응답이 상황 분석이면 situation_memory 갱신
        _update_situation_memory_if_needed(response_text, _active_video_ids)

        # 전략 쿼리 응답에 듀얼 모델 사용 표시 추가
        if is_strategy:
            response_text = _annotate_dual_model_response(response_text)

        history[-1] = (message, response_text)

    except Exception as e:
        logger.error(f"Agent run error: {e}", exc_info=True)
        error_msg = f"처리 중 오류가 발생했습니다: {e}"
        history[-1] = (message, error_msg)

    return "", history


def _annotate_dual_model_response(response: str) -> str:
    """전략/전술 응답에 듀얼 모델 처리 표시를 추가합니다."""
    annotation = "\n\n---\n*이 응답은 EXAONE4(상황 분석) + EXAONE Deep(전략/전술 추천)의 협업으로 생성되었습니다.*"
    return response + annotation


def clear_chat_history() -> Tuple[List, str]:
    """채팅 히스토리와 상황 메모리를 초기화합니다."""
    global _last_situation_analysis
    _last_situation_analysis = ""
    try:
        from tools.strategy_advisor_tool import clear_situation_memory
        clear_situation_memory()
    except Exception:
        pass
    return [], "대화 기록과 상황 분석 메모리가 초기화되었습니다."


def get_situation_memory_status() -> str:
    """현재 상황 분석 메모리 상태를 반환합니다."""
    try:
        from tools.strategy_advisor_tool import get_situation_memory
        memory = get_situation_memory()
        if memory.get("situation_analysis"):
            ts = memory.get("analysis_timestamp", "")
            preview = memory["situation_analysis"][:200] + "..."
            return f"상황 분석 메모리 활성 (분석 시각: {ts})\n\n미리보기:\n{preview}"
        return "상황 분석 메모리가 비어 있습니다. 먼저 영상 분석을 수행하세요."
    except Exception as e:
        return f"메모리 상태 조회 오류: {e}"


# ─────────────────────────────────────────────
# Gradio 앱 빌더
# ─────────────────────────────────────────────

def create_app(agent=None) -> gr.Blocks:
    """
    Gradio 앱을 생성하여 반환합니다.

    Args:
        agent: 초기화된 BattlefieldAgent 인스턴스 (None이면 앱 내 알림)
    """
    global _agent
    _agent = agent

    ui_cfg = _load_ui_config()

    with gr.Blocks(
        title=ui_cfg.get("title", "C2 군사 전략 AI"),
        theme=gr.themes.Base(primary_hue="slate", secondary_hue="gray"),
    ) as app:

        gr.Markdown(f"""
# {ui_cfg.get('title', 'C2 군사 전략 AI - EXAONE4 + EXAONE Deep')}
{ui_cfg.get('description', '')}

**듀얼 모델 아키텍처:**
- **EXAONE4**: 영상 분석, 상황 판단, 최종 응답 생성
- **EXAONE Deep**: 전략/전술 전문 추천 (EXAONE4의 상황 분석을 바탕으로 호출됨)
        """)

        with gr.Row():
            # ─── 왼쪽 패널: 영상 분석 ───────────────────────
            with gr.Column(scale=1):
                gr.Markdown("## 영상 분석")

                video_upload = gr.File(
                    label="군사 영상 업로드 (mp4, avi, mov)",
                    file_types=[".mp4", ".avi", ".mov", ".mkv"],
                )
                collection_input = gr.Textbox(
                    label="컬렉션명",
                    value="default",
                    placeholder="컬렉션 이름 입력",
                )
                analyze_btn = gr.Button("영상 분석 시작", variant="primary")
                analysis_status = gr.Textbox(
                    label="분석 상태",
                    lines=6,
                    interactive=False,
                )

                gr.Markdown("### 분석된 영상 목록")
                video_list = gr.CheckboxGroup(
                    label="쿼리할 영상 선택",
                    choices=[],
                    value=[],
                )
                video_select_status = gr.Textbox(
                    label="선택 상태",
                    value="선택된 비디오 없음",
                    interactive=False,
                )

                gr.Markdown("### 상황 분석 메모리")
                memory_status_btn = gr.Button("메모리 상태 확인")
                memory_status_box = gr.Textbox(
                    label="EXAONE4 상황 분석 메모리",
                    lines=5,
                    interactive=False,
                )

            # ─── 오른쪽 패널: 채팅 인터페이스 ───────────────
            with gr.Column(scale=2):
                gr.Markdown("## AI 에이전트 채팅")
                gr.Markdown(
                    "영상 분석 및 전략/전술 관련 질문을 입력하세요. "
                    "전략/전술 쿼리는 자동으로 **EXAONE Deep** 모델이 추가 분석합니다."
                )

                chatbot = gr.Chatbot(
                    label="대화",
                    height=500,
                    show_copy_button=True,
                )

                with gr.Row():
                    query_input = gr.Textbox(
                        label="쿼리 입력",
                        placeholder=(
                            "예: '영상에서 탐지된 적 기갑부대를 분석해줘' 또는 "
                            "'현재 상황에서 방어 전술을 추천해줘'"
                        ),
                        lines=2,
                        scale=5,
                    )
                    send_btn = gr.Button("전송", variant="primary", scale=1)

                with gr.Row():
                    clear_btn = gr.Button("대화 초기화", variant="secondary")
                    clear_status = gr.Textbox(
                        label="",
                        value="",
                        interactive=False,
                        scale=3,
                    )

                gr.Markdown("### 예시 쿼리")
                example_queries = ui_cfg.get("examples", [
                    "영상에서 탐지된 적 전력을 분석해줘",
                    "현재 전장 상황에 대한 전략적 대응 방안을 추천해줘",
                    "적 기갑부대에 대한 전술적 대응 방안을 제안해줘",
                    "아군 방어 진지 구축을 위한 전략을 수립해줘",
                ])
                gr.Examples(
                    examples=[[q] for q in example_queries],
                    inputs=[query_input],
                    label="클릭하여 예시 쿼리 입력",
                )

        # ─── 이벤트 핸들러 ──────────────────────────────────

        analyze_btn.click(
            fn=analyze_video,
            inputs=[video_upload, collection_input],
            outputs=[analysis_status, video_list],
        )

        video_list.change(
            fn=update_active_videos,
            inputs=[video_list],
            outputs=[video_select_status],
        )

        send_btn.click(
            fn=chat,
            inputs=[query_input, chatbot],
            outputs=[query_input, chatbot],
        )

        query_input.submit(
            fn=chat,
            inputs=[query_input, chatbot],
            outputs=[query_input, chatbot],
        )

        clear_btn.click(
            fn=clear_chat_history,
            outputs=[chatbot, clear_status],
        )

        memory_status_btn.click(
            fn=get_situation_memory_status,
            outputs=[memory_status_box],
        )

    return app


def launch_app(agent=None, **kwargs):
    """앱을 생성하고 실행합니다."""
    ui_cfg = _load_ui_config()
    app = create_app(agent=agent)
    app.launch(
        server_name=kwargs.get("server_name", ui_cfg.get("server_name", "0.0.0.0")),
        server_port=kwargs.get("server_port", ui_cfg.get("server_port", 7860)),
        share=kwargs.get("share", ui_cfg.get("share", False)),
    )
