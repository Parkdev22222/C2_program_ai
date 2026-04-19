"""
SAM3 기반 객체 탐지 및 추적 모듈
"""
import sys
import logging
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class Detection:
    def __init__(self, class_name: str, confidence: float, bbox: List[float],
                 mask: Optional[np.ndarray] = None, track_id: Optional[int] = None):
        self.class_name = class_name
        self.confidence = confidence
        self.bbox = bbox  # [x1, y1, x2, y2] normalized
        self.mask = mask  # (H, W) bool array or None
        self.track_id = track_id

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "bbox": [round(v, 4) for v in self.bbox],
        }
        if self.track_id is not None:
            d["track_id"] = self.track_id
        return d


class SAM3ObjectDetector:
    """
    SAM3 기반 객체 탐지 및 추적.

    - 단일 프레임 탐지: build_sam3_image_model + Sam3Processor
    - 다중 프레임 추적: build_sam3_video_predictor
    """

    def __init__(self, config: dict):
        self.config = config
        self.device = config.get("device", "cuda")
        self.target_classes = config.get("target_classes", [])
        self.confidence_threshold = config.get("confidence_threshold", 0.3)
        self.iou_threshold = config.get("iou_threshold", 0.5)
        self.min_mask_area_ratio = config.get("min_mask_area_ratio", 0.001)

        sam3_path = config.get("sam3_path", "")
        self.checkpoint_path = config.get("checkpoint_path", "")

        # SAM3 레포를 sys.path에 추가
        if sam3_path and sam3_path not in sys.path:
            sys.path.insert(0, sam3_path)
            logger.info(f"Added SAM3 path to sys.path: {sam3_path}")

        self._image_model = None
        self._processor = None
        self._video_predictor = None
        self._load_models()

    def _load_models(self):
        try:
            import torch
            from sam3.model_builder import build_sam3_image_model, build_sam3_video_predictor
            from sam3.model.sam3_image_processor import Sam3Processor

            logger.info(f"Loading SAM3 image model from: {self.checkpoint_path}")
            self._image_model = build_sam3_image_model(checkpoint_path=self.checkpoint_path)
            self._image_model = self._image_model.to(self.device).eval()
            self._processor = Sam3Processor(self._image_model)

            logger.info("Loading SAM3 video predictor")
            self._video_predictor = build_sam3_video_predictor(checkpoint_path=self.checkpoint_path)
            logger.info("SAM3 models loaded successfully")
        except Exception as e:
            logger.warning(f"Failed to load SAM3 models: {e}. Falling back to dummy detector.")
            self._image_model = None
            self._processor = None
            self._video_predictor = None

    # ──────────────────────────────────────────────
    # 단일 프레임 탐지
    # ──────────────────────────────────────────────

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """단일 프레임에서 군사 객체를 탐지합니다."""
        if self._image_model is None:
            return []
        try:
            return self._run_image_inference(frame)
        except Exception as e:
            logger.error(f"SAM3 image detection error: {e}")
            return []

    def _run_image_inference(self, frame: np.ndarray) -> List[Detection]:
        import torch
        from PIL import Image

        image = Image.fromarray(frame)
        h, w = frame.shape[:2]
        detections = []

        # Sam3Processor는 클래스 이름 리스트를 텍스트 쿼리로 받음
        for class_name in self.target_classes:
            try:
                with torch.no_grad():
                    results = self._processor.predict(
                        image=image,
                        text=class_name,
                        confidence_threshold=self.confidence_threshold,
                        iou_threshold=self.iou_threshold,
                    )

                if results is None:
                    continue

                # results: list of dicts with keys: box, score, mask
                for r in results:
                    score = float(r.get("score", 0.0))
                    if score < self.confidence_threshold:
                        continue

                    box = r.get("box", None)
                    mask = r.get("mask", None)

                    # 마스크 면적 필터 (너무 작은 오탐 제거)
                    if mask is not None:
                        mask_arr = np.array(mask, dtype=bool)
                        if mask_arr.sum() / (h * w) < self.min_mask_area_ratio:
                            continue
                    else:
                        mask_arr = None

                    if box is not None:
                        x1, y1, x2, y2 = [float(v) for v in box]
                        bbox_norm = [x1 / w, y1 / h, x2 / w, y2 / h]
                    else:
                        bbox_norm = [0.0, 0.0, 1.0, 1.0]

                    detections.append(Detection(
                        class_name=class_name,
                        confidence=score,
                        bbox=bbox_norm,
                        mask=mask_arr,
                    ))
            except Exception as e:
                logger.debug(f"SAM3 predict failed for class '{class_name}': {e}")
                continue

        return detections

    # ──────────────────────────────────────────────
    # 다중 프레임 추적
    # ──────────────────────────────────────────────

    def track_segment(
        self,
        frames: List[np.ndarray],
        seed_detections: Optional[List[Detection]] = None,
    ) -> List[Dict[str, Any]]:
        """
        SAM3 video predictor로 세그먼트 전체 프레임을 추적합니다.

        Args:
            frames: RGB numpy 프레임 리스트
            seed_detections: 첫 프레임의 탐지 결과 (None이면 내부적으로 detect() 호출)

        Returns:
            프레임별 탐지 결과 리스트: [{"frame_index": i, "detections": [det_dict, ...]}, ...]
        """
        if self._video_predictor is None or not frames:
            return self._fallback_track(frames)

        try:
            return self._run_video_tracking(frames, seed_detections)
        except Exception as e:
            logger.error(f"SAM3 video tracking error: {e}. Falling back to per-frame detection.")
            return self._fallback_track(frames)

    def _run_video_tracking(
        self,
        frames: List[np.ndarray],
        seed_detections: Optional[List[Detection]],
    ) -> List[Dict[str, Any]]:
        import torch
        from PIL import Image

        # 첫 프레임 탐지 (seed가 없으면 직접 탐지)
        if seed_detections is None:
            seed_detections = self.detect(frames[0])

        if not seed_detections:
            return []

        h, w = frames[0].shape[:2]
        results_per_frame: List[Dict[str, Any]] = []

        with torch.inference_mode():
            # video predictor 초기화 (PIL Image 리스트 전달)
            pil_frames = [Image.fromarray(f) for f in frames]
            state = self._video_predictor.init_state(frames=pil_frames)

            # 첫 프레임에 seed 마스크/박스 등록
            for track_id, det in enumerate(seed_detections):
                x1n, y1n, x2n, y2n = det.bbox
                # predictor는 절대 픽셀 좌표를 받음
                box_abs = np.array([x1n * w, y1n * h, x2n * w, y2n * h], dtype=np.float32)

                if det.mask is not None:
                    self._video_predictor.add_new_mask(
                        inference_state=state,
                        frame_idx=0,
                        obj_id=track_id,
                        mask=det.mask,
                    )
                else:
                    self._video_predictor.add_new_points_or_box(
                        inference_state=state,
                        frame_idx=0,
                        obj_id=track_id,
                        box=box_abs,
                    )

            # 전체 프레임 전파 추적
            track_id_to_class = {i: d.class_name for i, d in enumerate(seed_detections)}
            track_id_to_conf = {i: d.confidence for i, d in enumerate(seed_detections)}

            frame_det_map: Dict[int, List[Dict]] = {}
            for frame_idx, obj_ids, mask_logits in self._video_predictor.propagate_in_video(state):
                frame_dets = []
                masks = (mask_logits > 0.0).squeeze(1).cpu().numpy()  # (N, H, W)

                for local_idx, obj_id in enumerate(obj_ids):
                    mask = masks[local_idx]  # (H, W)
                    area_ratio = mask.sum() / (h * w)
                    if area_ratio < self.min_mask_area_ratio:
                        continue

                    # 마스크에서 bbox 역산
                    rows = np.any(mask, axis=1)
                    cols = np.any(mask, axis=0)
                    rmin, rmax = np.where(rows)[0][[0, -1]]
                    cmin, cmax = np.where(cols)[0][[0, -1]]
                    bbox_norm = [cmin / w, rmin / h, cmax / w, rmax / h]

                    class_name = track_id_to_class.get(obj_id, "unknown")
                    confidence = track_id_to_conf.get(obj_id, 1.0)

                    frame_dets.append(Detection(
                        class_name=class_name,
                        confidence=confidence,
                        bbox=bbox_norm,
                        track_id=int(obj_id),
                    ).to_dict())

                if frame_dets:
                    frame_det_map[frame_idx] = frame_dets

            # 결과를 정렬된 리스트로 변환
            for fi in sorted(frame_det_map.keys()):
                results_per_frame.append({
                    "frame_index": fi,
                    "detections": frame_det_map[fi],
                })

        return results_per_frame

    def _fallback_track(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        """video predictor 실패 시 매 프레임 독립 탐지로 대체."""
        all_results = []
        for i, frame in enumerate(frames):
            dets = self.detect(frame)
            if dets:
                all_results.append({
                    "frame_index": i,
                    "detections": [d.to_dict() for d in dets],
                })
        return all_results

    def detect_video_segment(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        """track_segment()의 호환 래퍼 (VideoAnalysisSystem에서 호출)."""
        return self.track_segment(frames)


# 기존 코드와의 호환성을 위한 별칭
ObjectDetector = SAM3ObjectDetector
