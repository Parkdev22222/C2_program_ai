"""
컨텍스트 스캐너 - 현재 선택된 비디오/PDF 컨텍스트 관리
"""
import logging
from typing import List, Dict, Any, Optional, Set

logger = logging.getLogger(__name__)


class ContextScanner:
    """
    에이전트가 쿼리할 활성 컨텍스트(비디오, PDF)를 추적합니다.
    """

    def __init__(self):
        self._selected_video_ids: Set[str] = set()
        self._selected_pdf_ids: Set[str] = set()
        self._video_metadata: Dict[str, Dict] = {}
        self._pdf_metadata: Dict[str, Dict] = {}

    def set_selected_videos(self, video_ids: List[str]):
        self._selected_video_ids = set(video_ids)
        logger.debug(f"Selected videos: {video_ids}")

    def add_video(self, video_id: str, metadata: Dict = None):
        self._selected_video_ids.add(video_id)
        if metadata:
            self._video_metadata[video_id] = metadata

    def remove_video(self, video_id: str):
        self._selected_video_ids.discard(video_id)

    def set_selected_pdfs(self, pdf_ids: List[str]):
        self._selected_pdf_ids = set(pdf_ids)

    def add_pdf(self, pdf_id: str, metadata: Dict = None):
        self._selected_pdf_ids.add(pdf_id)
        if metadata:
            self._pdf_metadata[pdf_id] = metadata

    def get_selected_video_ids(self) -> List[str]:
        return list(self._selected_video_ids)

    def get_selected_pdf_ids(self) -> List[str]:
        return list(self._selected_pdf_ids)

    def get_context_summary(self) -> Dict[str, Any]:
        return {
            "selected_videos": list(self._selected_video_ids),
            "selected_pdfs": list(self._selected_pdf_ids),
            "video_metadata": {
                vid: self._video_metadata.get(vid, {})
                for vid in self._selected_video_ids
            },
            "pdf_metadata": {
                pid: self._pdf_metadata.get(pid, {})
                for pid in self._selected_pdf_ids
            },
        }

    def clear(self):
        self._selected_video_ids.clear()
        self._selected_pdf_ids.clear()
