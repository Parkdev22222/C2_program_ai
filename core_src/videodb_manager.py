"""
로컬 비디오 데이터베이스 매니저
영상 세그먼트, 임베딩, 메타데이터를 파일 시스템에 저장/조회
"""
import json
import uuid
import logging
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class VideoSegment:
    def __init__(
        self,
        segment_id: str,
        video_id: str,
        start_time: float,
        end_time: float,
        embedding: Optional[np.ndarray] = None,
        detections: Optional[List[Dict]] = None,
        description: str = "",
    ):
        self.segment_id = segment_id
        self.video_id = video_id
        self.start_time = start_time
        self.end_time = end_time
        self.embedding = embedding
        self.detections = detections or []
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "video_id": self.video_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "detections": self.detections,
            "description": self.description,
        }


class VideoDBManager:
    def __init__(self, config: dict, collection_name: str = "default"):
        self.config = config
        self.collection_name = collection_name
        base = Path(config.get("storage_path", "./data/videodb"))
        self.collection_path = base / collection_name
        self.videos_path = self.collection_path / "videos"
        self.segments_path = self.collection_path / "segments"
        self.embeddings_path = self.collection_path / "embeddings"
        self._ensure_dirs()
        self._video_index: Dict[str, Dict] = self._load_video_index()

    def _ensure_dirs(self):
        for p in [self.videos_path, self.segments_path, self.embeddings_path]:
            p.mkdir(parents=True, exist_ok=True)

    def _load_video_index(self) -> Dict[str, Dict]:
        index_file = self.collection_path / "video_index.json"
        if index_file.exists():
            with open(index_file) as f:
                return json.load(f)
        return {}

    def _save_video_index(self):
        index_file = self.collection_path / "video_index.json"
        with open(index_file, "w") as f:
            json.dump(self._video_index, f, indent=2, ensure_ascii=False)

    def register_video(self, video_path: str, metadata: Dict = None) -> str:
        video_id = f"v-{uuid.uuid4().hex[:8]}"
        self._video_index[video_id] = {
            "video_id": video_id,
            "original_path": str(video_path),
            "filename": Path(video_path).name,
            "registered_at": datetime.now().isoformat(),
            "segment_count": 0,
            "metadata": metadata or {},
        }
        self._save_video_index()
        logger.info(f"Registered video {video_id}: {video_path}")
        return video_id

    def add_segment(self, segment: VideoSegment):
        seg_file = self.segments_path / f"{segment.segment_id}.json"
        with open(seg_file, "w") as f:
            json.dump(segment.to_dict(), f, indent=2, ensure_ascii=False)

        if segment.embedding is not None:
            emb_file = self.embeddings_path / f"{segment.segment_id}.npy"
            np.save(emb_file, segment.embedding)

        if segment.video_id in self._video_index:
            self._video_index[segment.video_id]["segment_count"] += 1
            self._save_video_index()

    def get_segments(self, video_id: str) -> List[Dict[str, Any]]:
        segments = []
        for seg_file in self.segments_path.glob("*.json"):
            with open(seg_file) as f:
                data = json.load(f)
            if data.get("video_id") == video_id:
                segments.append(data)
        return sorted(segments, key=lambda x: x["start_time"])

    def get_all_segments(self, video_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        segments = []
        for seg_file in self.segments_path.glob("*.json"):
            with open(seg_file) as f:
                data = json.load(f)
            if video_ids is None or data.get("video_id") in video_ids:
                segments.append(data)
        return sorted(segments, key=lambda x: x["start_time"])

    def semantic_search(
        self,
        query_embedding: np.ndarray,
        video_ids: Optional[List[str]] = None,
        top_k: int = 5,
    ) -> List[Tuple[Dict, float]]:
        results = []
        for emb_file in self.embeddings_path.glob("*.npy"):
            seg_id = emb_file.stem
            seg_file = self.segments_path / f"{seg_id}.json"
            if not seg_file.exists():
                continue
            with open(seg_file) as f:
                seg_data = json.load(f)
            if video_ids and seg_data.get("video_id") not in video_ids:
                continue
            emb = np.load(emb_file)
            similarity = float(np.dot(query_embedding, emb) / (
                np.linalg.norm(query_embedding) * np.linalg.norm(emb) + 1e-8
            ))
            results.append((seg_data, similarity))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def object_search(
        self,
        class_name: str,
        video_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        results = []
        for seg_file in self.segments_path.glob("*.json"):
            with open(seg_file) as f:
                data = json.load(f)
            if video_ids and data.get("video_id") not in video_ids:
                continue
            matched = [d for d in data.get("detections", []) if d.get("class_name") == class_name]
            if matched:
                results.append({**data, "matched_detections": matched})
        return sorted(results, key=lambda x: x["start_time"])

    def keyword_search(
        self,
        keyword: str,
        video_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        results = []
        keyword_lower = keyword.lower()
        for seg_file in self.segments_path.glob("*.json"):
            with open(seg_file) as f:
                data = json.load(f)
            if video_ids and data.get("video_id") not in video_ids:
                continue
            if keyword_lower in data.get("description", "").lower():
                results.append(data)
        return sorted(results, key=lambda x: x["start_time"])

    def get_video_summary(self, video_id: str) -> Dict[str, Any]:
        segments = self.get_segments(video_id)
        if not segments:
            return {"video_id": video_id, "segment_count": 0, "total_duration": 0}

        object_counts: Dict[str, int] = {}
        for seg in segments:
            for det in seg.get("detections", []):
                cn = det.get("class_name", "unknown")
                object_counts[cn] = object_counts.get(cn, 0) + 1

        return {
            "video_id": video_id,
            "filename": self._video_index.get(video_id, {}).get("filename", ""),
            "segment_count": len(segments),
            "total_duration": segments[-1]["end_time"] if segments else 0,
            "object_counts": object_counts,
        }

    def list_videos(self) -> List[Dict[str, Any]]:
        return list(self._video_index.values())

    def switch_collection(self, collection_name: str):
        self.collection_name = collection_name
        base = Path(self.config.get("storage_path", "./data/videodb"))
        self.collection_path = base / collection_name
        self.videos_path = self.collection_path / "videos"
        self.segments_path = self.collection_path / "segments"
        self.embeddings_path = self.collection_path / "embeddings"
        self._ensure_dirs()
        self._video_index = self._load_video_index()
