"""
비디오 데이터베이스 쿼리 도구 모음 (smolagents Tool 형태)

EXAONE4 에이전트가 사용하는 비디오 분석 도구들:
- get_selected_contexts: 현재 선택된 비디오/PDF 컨텍스트 확인
- query_video_semantic: 의미론적 유사도 검색
- query_video_by_object: 객체 유형으로 검색
- query_video_by_event: 키워드 기반 이벤트 검색
- get_video_summary: 영상 요약 통계
- get_segment_details: 특정 세그먼트 상세 정보
- set_active_videos: 활성 비디오 설정
"""
import logging
from typing import List, Optional, Dict, Any
from smolagents import tool

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 전역 상태 (에이전트가 쿼리할 컨텍스트)
# ─────────────────────────────────────────────
_selected_video_ids: List[str] = []
_selected_pdf_ids: List[str] = []
_video_collection_map: Dict[str, str] = {}  # video_id → collection_name
_videodb_managers: Dict[str, Any] = {}       # collection_name → VideoDBManager
_embedding_generator = None


def set_selected_video_ids(video_ids: List[str]):
    global _selected_video_ids
    _selected_video_ids = list(video_ids)


def set_selected_pdf_ids(pdf_ids: List[str]):
    global _selected_pdf_ids
    _selected_pdf_ids = list(pdf_ids)


def register_videodb_manager(collection_name: str, manager):
    _videodb_managers[collection_name] = manager


def register_video_collection(video_id: str, collection_name: str):
    _video_collection_map[video_id] = collection_name


def set_embedding_generator(generator):
    global _embedding_generator
    _embedding_generator = generator


def _get_manager_for_video(video_id: str):
    collection = _video_collection_map.get(video_id, "default")
    return _videodb_managers.get(collection)


# ─────────────────────────────────────────────
# smolagents 도구 함수들
# ─────────────────────────────────────────────

@tool
def get_selected_contexts() -> dict:
    """
    현재 선택된 비디오 및 PDF 컨텍스트 목록을 반환합니다.
    비디오 쿼리 전에 반드시 먼저 이 도구를 호출하여 활성 컨텍스트를 확인하세요.

    Returns:
        {
            "selected_videos": ["v-xxxxxxxx", ...],
            "selected_pdfs": ["pdf_id", ...],
            "video_count": int,
            "pdf_count": int
        }
    """
    return {
        "selected_videos": list(_selected_video_ids),
        "selected_pdfs": list(_selected_pdf_ids),
        "video_count": len(_selected_video_ids),
        "pdf_count": len(_selected_pdf_ids),
        "status": "ok" if _selected_video_ids else "no_context_selected",
        "message": (
            "컨텍스트가 선택되어 있습니다." if _selected_video_ids
            else "선택된 비디오가 없습니다. UI에서 분석할 비디오를 선택해주세요."
        ),
    }


@tool
def query_video_semantic(query: str, top_k: int = 5) -> dict:
    """
    자연어 쿼리로 비디오 세그먼트를 의미론적으로 검색합니다.
    "전차가 숲을 통과하는 장면", "병력 이동" 같은 자연어로 검색 가능합니다.

    Args:
        query: 자연어 검색 쿼리 (예: "tanks moving through forest", "병력 집결")
        top_k: 반환할 상위 결과 수 (기본값: 5)

    Returns:
        {
            "status": "success" | "no_results" | "error",
            "results": [{"segment_id", "video_id", "start_time", "end_time",
                         "description", "similarity_score", "detections"}, ...],
            "query": str,
            "total_found": int
        }
    """
    if not _selected_video_ids:
        return {"status": "error", "message": "선택된 비디오가 없습니다.", "results": []}

    if _embedding_generator is None:
        return {"status": "error", "message": "임베딩 생성기가 초기화되지 않았습니다.", "results": []}

    try:
        query_emb = _embedding_generator.generate_text_embedding(query)
        all_results = []

        # 각 비디오의 컬렉션 매니저에서 검색
        for video_id in _selected_video_ids:
            manager = _get_manager_for_video(video_id)
            if manager is None:
                continue
            results = manager.semantic_search(query_emb, video_ids=[video_id], top_k=top_k)
            for seg, score in results:
                all_results.append({**seg, "similarity_score": round(score, 4)})

        # 유사도 점수로 정렬
        all_results.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)
        top_results = all_results[:top_k]

        return {
            "status": "success" if top_results else "no_results",
            "query": query,
            "total_found": len(top_results),
            "results": top_results,
        }
    except Exception as e:
        logger.error(f"Semantic search error: {e}")
        return {"status": "error", "message": str(e), "results": []}


@tool
def query_video_by_object(object_class: str) -> dict:
    """
    특정 군사 객체 유형이 등장하는 비디오 세그먼트를 검색합니다.
    객체별 타임라인(언제, 얼마나 등장했는지)을 반환합니다.

    Args:
        object_class: 탐지할 객체 유형 ("tank", "soldier", "truck",
                      "armored_vehicle", "helicopter", "artillery")

    Returns:
        {
            "status": "success" | "no_results" | "error",
            "object_class": str,
            "timeline": [{"video_id", "start_time", "end_time",
                          "count", "segment_id"}, ...],
            "total_segments": int
        }
    """
    if not _selected_video_ids:
        return {"status": "error", "message": "선택된 비디오가 없습니다.", "timeline": []}

    try:
        all_segments = []
        for video_id in _selected_video_ids:
            manager = _get_manager_for_video(video_id)
            if manager is None:
                continue
            segments = manager.object_search(object_class, video_ids=[video_id])
            for seg in segments:
                count = len(seg.get("matched_detections", seg.get("detections", [])))
                all_segments.append({
                    "video_id": seg["video_id"],
                    "segment_id": seg["segment_id"],
                    "start_time": seg["start_time"],
                    "end_time": seg["end_time"],
                    "count": count,
                    "description": seg.get("description", ""),
                })

        all_segments.sort(key=lambda x: x["start_time"])
        return {
            "status": "success" if all_segments else "no_results",
            "object_class": object_class,
            "total_segments": len(all_segments),
            "timeline": all_segments,
            "message": (
                f"{len(all_segments)}개 세그먼트에서 '{object_class}' 탐지됨"
                if all_segments else f"'{object_class}' 객체가 탐지되지 않았습니다."
            ),
        }
    except Exception as e:
        logger.error(f"Object search error: {e}")
        return {"status": "error", "message": str(e), "timeline": []}


@tool
def query_video_by_event(keyword: str) -> dict:
    """
    AI가 생성한 세그먼트 설명에서 키워드를 검색합니다.
    "movement", "urban", "convoy" 같은 키워드로 이벤트를 찾습니다.

    Args:
        keyword: 검색할 키워드 (예: "convoy", "movement", "urban area", "정찰")

    Returns:
        {
            "status": "success" | "no_results" | "error",
            "keyword": str,
            "results": [{"segment_id", "video_id", "start_time", "end_time",
                         "description"}, ...],
            "total_found": int
        }
    """
    if not _selected_video_ids:
        return {"status": "error", "message": "선택된 비디오가 없습니다.", "results": []}

    try:
        all_results = []
        for video_id in _selected_video_ids:
            manager = _get_manager_for_video(video_id)
            if manager is None:
                continue
            segments = manager.keyword_search(keyword, video_ids=[video_id])
            all_results.extend(segments)

        all_results.sort(key=lambda x: x["start_time"])
        return {
            "status": "success" if all_results else "no_results",
            "keyword": keyword,
            "total_found": len(all_results),
            "results": all_results,
        }
    except Exception as e:
        logger.error(f"Event search error: {e}")
        return {"status": "error", "message": str(e), "results": []}


@tool
def get_video_summary(video_id: str) -> dict:
    """
    특정 비디오의 요약 통계를 반환합니다.
    전체 길이, 세그먼트 수, 객체 유형별 탐지 횟수를 포함합니다.

    Args:
        video_id: 요약할 비디오 ID (예: "v-a1b2c3d4")

    Returns:
        {
            "video_id": str,
            "filename": str,
            "segment_count": int,
            "total_duration": float,
            "object_counts": {"tank": int, "soldier": int, ...}
        }
    """
    manager = _get_manager_for_video(video_id)
    if manager is None:
        return {"status": "error", "message": f"비디오 '{video_id}'를 찾을 수 없습니다."}
    try:
        return {**manager.get_video_summary(video_id), "status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tool
def get_segment_details(segment_id: str) -> dict:
    """
    특정 세그먼트의 상세 정보를 반환합니다.
    탐지된 객체의 좌표, 신뢰도, AI 설명 등을 포함합니다.

    Args:
        segment_id: 세그먼트 ID (예: "v-a1b2c3d4_seg0003")

    Returns:
        {
            "segment_id": str,
            "video_id": str,
            "start_time": float,
            "end_time": float,
            "description": str,
            "detections": [{"class_name", "confidence", "bbox"}, ...]
        }
    """
    # segment_id에서 video_id 추출 (예: v-a1b2c3d4_seg0003 → v-a1b2c3d4)
    parts = segment_id.split("_seg")
    if len(parts) < 2:
        return {"status": "error", "message": f"잘못된 세그먼트 ID 형식: {segment_id}"}

    video_id = parts[0]
    manager = _get_manager_for_video(video_id)
    if manager is None:
        return {"status": "error", "message": f"세그먼트 '{segment_id}'를 찾을 수 없습니다."}

    try:
        segments = manager.get_segments(video_id)
        for seg in segments:
            if seg["segment_id"] == segment_id:
                return {**seg, "status": "success"}
        return {"status": "no_results", "message": f"세그먼트 '{segment_id}'를 찾을 수 없습니다."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tool
def set_active_videos(video_ids: List[str]) -> dict:
    """
    에이전트가 쿼리할 활성 비디오 목록을 설정합니다.

    Args:
        video_ids: 활성화할 비디오 ID 목록

    Returns:
        {"status": "ok", "active_videos": [...], "count": int}
    """
    set_selected_video_ids(video_ids)
    return {
        "status": "ok",
        "active_videos": video_ids,
        "count": len(video_ids),
        "message": f"{len(video_ids)}개 비디오가 활성화되었습니다.",
    }
