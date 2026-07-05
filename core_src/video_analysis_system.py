"""
비디오 분석 시스템 - 전체 파이프라인 오케스트레이터
업로드 → 세그먼트 분할 → 임베딩 생성 → 이벤트 설명
"""
import cv2
import uuid
import logging
import numpy as np
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from .videodb_manager import VideoDBManager, VideoSegment
from .model_manager import ModelManager

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"


class VideoAnalysisSystem:
    def __init__(
        self,
        collection_name: str = "default",
        embedding_generator=None,
        description_generator=None,
    ):
        self.collection_name = collection_name
        self._load_configs()

        # 공유 모델 인스턴스 사용 (None이면 ModelManager에서 로딩)
        self._owned_models = (embedding_generator is None)
        if embedding_generator is None:
            mm = ModelManager()
            self.embedding_generator = mm.get_embedding_generator()
            self.description_generator = mm.get_description_generator()
        else:
            self.embedding_generator = embedding_generator
            self.description_generator = description_generator

        self.videodb = VideoDBManager(self._videodb_config, collection_name)

    def _load_configs(self):
        with open(CONFIG_DIR / "videodb_config.yaml") as f:
            self._videodb_config = yaml.safe_load(f)["videodb"]
        with open(CONFIG_DIR / "models_config.yaml") as f:
            models_cfg = yaml.safe_load(f)
        self._segment_duration = self._videodb_config.get("segment_duration", 5)

    def analyze_video(
        self,
        video_path: str,
        segment_duration: Optional[int] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        seg_dur = segment_duration or self._segment_duration
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        logger.info(f"Starting analysis: {video_path}")
        video_id = self.videodb.register_video(str(video_path))

        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps
        frames_per_segment = int(fps * seg_dur)

        logger.info(f"Video: {duration:.1f}s, {fps:.1f}fps, {total_frames} frames")

        segment_results = []
        segment_idx = 0
        current_frames = []
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            current_frames.append(frame_rgb)
            frame_idx += 1

            if len(current_frames) >= frames_per_segment:
                start_t = segment_idx * seg_dur
                end_t = start_t + seg_dur
                result = self._process_segment(
                    current_frames, video_id, segment_idx, start_t, end_t
                )
                segment_results.append(result)
                segment_idx += 1
                current_frames = []

        # 마지막 불완전한 세그먼트 처리
        if current_frames:
            start_t = segment_idx * seg_dur
            end_t = duration
            result = self._process_segment(
                current_frames, video_id, segment_idx, start_t, end_t
            )
            segment_results.append(result)

        cap.release()

        summary = self._build_summary(video_id, segment_results, duration)
        logger.info(f"Analysis complete: {video_id}, {len(segment_results)} segments")
        return summary

    def _process_segment(
        self,
        frames: List[np.ndarray],
        video_id: str,
        segment_idx: int,
        start_t: float,
        end_t: float,
    ) -> Dict[str, Any]:
        segment_id = f"{video_id}_seg{segment_idx:04d}"
        key_frame_idx = len(frames) // 2
        key_frame = frames[key_frame_idx]

        # 객체 탐지 제거됨 — 세그먼트는 임베딩·설명만 생성 (DB 스키마 호환을 위해 빈 목록 유지)
        det_dicts = []

        # 임베딩 생성 (키 프레임 기준)
        embedding = self.embedding_generator.generate([key_frame])[0]

        # 이벤트 설명 생성
        description = self.description_generator.describe_segment(key_frame, det_dicts)

        segment = VideoSegment(
            segment_id=segment_id,
            video_id=video_id,
            start_time=start_t,
            end_time=end_t,
            embedding=embedding,
            detections=det_dicts,
            description=description,
        )
        self.videodb.add_segment(segment)

        return {
            "segment_id": segment_id,
            "start_time": start_t,
            "end_time": end_t,
            "detection_count": len(det_dicts),
            "description": description,
        }

    def _build_summary(
        self,
        video_id: str,
        segment_results: List[Dict],
        duration: float,
    ) -> Dict[str, Any]:
        total_detections = sum(s["detection_count"] for s in segment_results)
        return {
            "video_id": video_id,
            "collection": self.collection_name,
            "duration": duration,
            "segment_count": len(segment_results),
            "total_detections": total_detections,
            "segments": segment_results,
            "status": "completed",
        }

    def query_video(
        self,
        query: str,
        video_ids: List[str],
        query_type: str = "semantic",
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        if query_type == "semantic":
            q_emb = self.embedding_generator.generate_text_embedding(query)
            results = self.videodb.semantic_search(q_emb, video_ids=video_ids, top_k=top_k)
            return [{"segment": seg, "score": score} for seg, score in results]
        elif query_type == "object":
            return self.videodb.object_search(query, video_ids=video_ids)
        elif query_type == "event":
            return self.videodb.keyword_search(query, video_ids=video_ids)
        else:
            raise ValueError(f"Unknown query_type: {query_type}")

    def switch_collection(self, collection_name: str):
        self.collection_name = collection_name
        self.videodb.switch_collection(collection_name)

    def cleanup(self):
        if self._owned_models:
            ModelManager().cleanup()
