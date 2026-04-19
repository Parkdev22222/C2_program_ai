"""
SAM2 기반 객체 탐지 모듈
"""
import logging
import numpy as np
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class Detection:
    def __init__(self, class_name: str, confidence: float, bbox: List[float]):
        self.class_name = class_name
        self.confidence = confidence
        self.bbox = bbox  # [x1, y1, x2, y2] normalized

    def to_dict(self) -> Dict[str, Any]:
        return {
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "bbox": [round(v, 4) for v in self.bbox],
        }


class ObjectDetector:
    def __init__(self, config: dict):
        self.config = config
        self.device = config.get("device", "cuda")
        self.target_classes = config.get("target_classes", [])
        self.confidence_threshold = config.get("confidence_threshold", 0.3)
        self.iou_threshold = config.get("iou_threshold", 0.5)
        self._model = None
        self._processor = None
        self._load_model()

    def _load_model(self):
        try:
            from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
            import torch

            model_name = self.config.get("model_name", "facebook/sam2-hiera-large")
            logger.info(f"Loading object detection model: {model_name}")
            self._processor = AutoProcessor.from_pretrained(model_name)
            self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
                model_name,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
            ).to(self.device)
            logger.info("Object detection model loaded successfully")
        except Exception as e:
            logger.warning(f"Failed to load detection model: {e}. Using dummy detector.")
            self._model = None

    def detect(self, frame: np.ndarray) -> List[Detection]:
        if self._model is None:
            return self._dummy_detect(frame)
        try:
            return self._run_inference(frame)
        except Exception as e:
            logger.error(f"Detection error: {e}")
            return []

    def _run_inference(self, frame: np.ndarray) -> List[Detection]:
        import torch
        from PIL import Image

        image = Image.fromarray(frame)
        text_queries = ". ".join(self.target_classes)
        inputs = self._processor(images=image, text=text_queries, return_tensors="pt").to(self.device)

        with torch.no_grad():
            outputs = self._model(**inputs)

        results = self._processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            box_threshold=self.confidence_threshold,
            text_threshold=self.confidence_threshold,
            target_sizes=[image.size[::-1]],
        )[0]

        detections = []
        h, w = frame.shape[:2]
        for score, label, box in zip(results["scores"], results["labels"], results["boxes"]):
            if score.item() >= self.confidence_threshold:
                x1, y1, x2, y2 = box.tolist()
                detections.append(Detection(
                    class_name=label,
                    confidence=score.item(),
                    bbox=[x1 / w, y1 / h, x2 / w, y2 / h],
                ))
        return detections

    def _dummy_detect(self, frame: np.ndarray) -> List[Detection]:
        return []

    def detect_video_segment(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        all_detections = []
        for i, frame in enumerate(frames):
            dets = self.detect(frame)
            if dets:
                all_detections.append({
                    "frame_index": i,
                    "detections": [d.to_dict() for d in dets],
                })
        return all_detections
