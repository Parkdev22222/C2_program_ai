"""
SAM3 기반 객체 탐지 및 추적 모듈

동작 확인된 API (transformers Sam3VideoModel / Sam3VideoProcessor):

  # 모델 로딩
  model = Sam3VideoModel.from_pretrained(weights_path).to(device, dtype=dtype)
  processor = Sam3VideoProcessor.from_pretrained(weights_path)

  # 비디오 세션
  inference_session = processor.init_video_session(
      video=pil_frames,
      inference_device=device,
      processing_device="cpu",
      video_storage_device="cpu",
      dtype=dtype,
  )
  inference_session = processor.add_text_prompt(
      inference_session=inference_session, text="soldier"
  )
  for model_outputs in model.propagate_in_video_iterator(
      inference_session=inference_session, max_frame_num_to_track=N
  ):
      processed = processor.postprocess_outputs(inference_session, model_outputs)
      # processed: {object_ids, scores, boxes (XYXY abs), masks}
"""

import gc
import logging
import os
import numpy as np
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── 탐지 결과 컨테이너 ────────────────────────────────────────────────────────
class Detection:
    def __init__(
        self,
        class_name: str,
        confidence: float,
        bbox: List[float],
        mask: Optional[np.ndarray] = None,
        track_id: Optional[int] = None,
    ):
        self.class_name = class_name
        self.confidence = confidence
        self.bbox = bbox          # [x1, y1, x2, y2] normalized (0~1)
        self.mask = mask
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


# ── 보조 함수 ─────────────────────────────────────────────────────────────────
def _iou(a: Tuple, b: Tuple) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / (ua + 1e-6)


def _nms(detections: List[Detection], iou_threshold: float) -> List[Detection]:
    if not detections:
        return []
    by_class: Dict[str, List[Detection]] = {}
    for d in detections:
        by_class.setdefault(d.class_name, []).append(d)
    kept: List[Detection] = []
    for dets in by_class.values():
        dets = sorted(dets, key=lambda x: x.confidence, reverse=True)
        remaining = list(range(len(dets)))
        while remaining:
            best = remaining.pop(0)
            kept.append(dets[best])
            bx = tuple(dets[best].bbox)
            remaining = [
                i for i in remaining
                if _iou(bx, tuple(dets[i].bbox)) < iou_threshold
            ]
    return kept


def _mask_to_normalized_bbox(mask: np.ndarray, h: int, w: int) -> Optional[List[float]]:
    rows, cols = np.any(mask, axis=1), np.any(mask, axis=0)
    if not rows.any():
        return None
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    return [cmin / w, rmin / h, cmax / w, rmax / h]


def _to_numpy(val) -> np.ndarray:
    if val is None:
        return np.array([])
    if hasattr(val, "cpu"):
        return val.cpu().numpy()
    return np.asarray(val)


def _resize_mask(mask_bool: np.ndarray, h: int, w: int) -> np.ndarray:
    if mask_bool.shape == (h, w):
        return mask_bool
    from PIL import Image as _PIL
    return np.array(
        _PIL.fromarray(mask_bool.astype(np.uint8) * 255, "L").resize((w, h), _PIL.NEAREST)
    ) > 127


# ── postprocess_outputs 결과 → Detection 리스트 ───────────────────────────────
def _postprocess_to_detections(
    processed: dict,
    class_name: str,
    orig_h: int,
    orig_w: int,
    confidence_threshold: float,
    min_mask_area_ratio: float,
    id_to_label: Optional[Dict[int, str]] = None,
) -> List[Detection]:
    """
    processor.postprocess_outputs() 결과 dict → Detection 리스트.

    processed 키:
      object_ids : Tensor[N]
      scores     : Tensor[N]
      boxes      : Tensor[N, 4]  XYXY 절대 좌표
      masks      : Tensor[N, H, W] 또는 Tensor[N, 1, H, W]
    """
    obj_ids = processed.get("object_ids")
    scores  = processed.get("scores")
    boxes   = processed.get("boxes")
    masks   = processed.get("masks")

    if obj_ids is None:
        return []

    obj_ids_list = obj_ids.tolist() if hasattr(obj_ids, "tolist") else list(obj_ids)
    scores_list  = scores.tolist()  if (scores is not None and hasattr(scores, "tolist")) else [1.0] * len(obj_ids_list)
    boxes_np     = _to_numpy(boxes)   # (N, 4) or empty
    masks_tensor = masks              # keep as tensor for dim check

    detections: List[Detection] = []
    for i, obj_id in enumerate(obj_ids_list):
        score = float(scores_list[i]) if i < len(scores_list) else 1.0
        if score < confidence_threshold:
            continue

        label = class_name
        if id_to_label is not None:
            label = id_to_label.get(int(obj_id), class_name)

        # 마스크 처리
        mask_np = None
        if masks_tensor is not None and i < len(masks_tensor):
            m = masks_tensor[i]
            if hasattr(m, "dim") and m.dim() == 3:
                m = m.squeeze(0)
            m_np = _to_numpy(m).astype(bool)
            m_np = _resize_mask(m_np, orig_h, orig_w)
            if m_np.sum() / (orig_h * orig_w) >= min_mask_area_ratio:
                mask_np = m_np

        # 바운딩박스
        if mask_np is not None and mask_np.any():
            bbox_norm = _mask_to_normalized_bbox(mask_np, orig_h, orig_w)
            if bbox_norm is None:
                continue
        elif boxes_np.ndim == 2 and i < len(boxes_np):
            x1, y1, x2, y2 = boxes_np[i].tolist()
            if (x2 - x1) < 2 or (y2 - y1) < 2:
                continue
            bbox_norm = [x1 / orig_w, y1 / orig_h, x2 / orig_w, y2 / orig_h]
        else:
            continue

        detections.append(Detection(
            class_name=label,
            confidence=score,
            bbox=bbox_norm,
            mask=mask_np,
            track_id=int(obj_id),
        ))

    return detections


# ── SAM3ObjectDetector ────────────────────────────────────────────────────────
class SAM3ObjectDetector:
    """
    SAM3 기반 군사 객체 탐지 및 추적.

    단일 프레임 탐지 : init_video_session(1프레임) → add_text_prompt → propagate
    다중 프레임 추적 : init_video_session(N프레임) → add_text_prompt × M → propagate_in_video_iterator
    """

    def __init__(self, config: dict):
        self.config = config
        self.device = config.get("device", "cuda")
        self.target_classes: List[str] = config.get("target_classes", [])
        self.confidence_threshold: float = config.get("confidence_threshold", 0.3)
        self.iou_threshold: float = config.get("iou_threshold", 0.5)
        self.min_mask_area_ratio: float = config.get("min_mask_area_ratio", 0.001)

        # 가중치 경로: 환경변수 > config > 기본 HF 모델 ID
        self.weights_path: str = (
            os.environ.get("SAM3_WEIGHTS")
            or config.get("checkpoint_path", "")
            or "facebook/sam3"
        )

        self._model = None
        self._processor = None
        self._dtype = None
        self._load_models()

    # ── 모델 로딩 ─────────────────────────────────────────────────────────────
    def _load_models(self) -> None:
        import warnings
        import torch
        from transformers import Sam3VideoModel, Sam3VideoProcessor

        warnings.filterwarnings("ignore", category=UserWarning)

        dtype_str = self.config.get("dtype", "bfloat16")
        dtype_map = {
            "bfloat16": torch.bfloat16,
            "float16":  torch.float16,
            "float32":  torch.float32,
        }
        self._dtype = dtype_map.get(dtype_str, torch.bfloat16)

        try:
            logger.info(f"SAM3 모델 로딩: {self.weights_path}  dtype={dtype_str}")
            self._model = (
                Sam3VideoModel.from_pretrained(self.weights_path)
                .to(self.device, dtype=self._dtype)
                .eval()
            )
            self._processor = Sam3VideoProcessor.from_pretrained(self.weights_path)
            logger.info("SAM3 모델 로딩 완료")
        except Exception as e:
            logger.error(f"SAM3 로딩 실패: {e}", exc_info=True)

    # ── 단일 프레임 탐지 ──────────────────────────────────────────────────────
    def detect(self, frame: np.ndarray) -> List[Detection]:
        """RGB numpy 프레임 1장 탐지 (video_analysis_system이 RGB로 전달함)."""
        if self._model is None or self._processor is None:
            return []
        try:
            return self._detect_frame(frame)
        except Exception as e:
            logger.error(f"detect 오류: {e}", exc_info=True)
            return []

    def _detect_frame(self, frame: np.ndarray) -> List[Detection]:
        import torch
        from PIL import Image

        h, w = frame.shape[:2]
        # video_analysis_system이 BGR→RGB 변환 후 넘기므로 그대로 사용
        pil = Image.fromarray(frame).convert("RGB")

        # 1프레임짜리 비디오 세션으로 탐지
        inference_session = self._processor.init_video_session(
            video=[pil],
            inference_device=torch.device(self.device),
            processing_device="cpu",
            video_storage_device="cpu",
            dtype=self._dtype,
        )

        for class_name in self.target_classes:
            inference_session = self._processor.add_text_prompt(
                inference_session=inference_session,
                text=class_name,
            )

        all_dets: List[Detection] = []
        with torch.no_grad():
            for model_outputs in self._model.propagate_in_video_iterator(
                inference_session=inference_session,
                max_frame_num_to_track=1,
            ):
                processed = self._processor.postprocess_outputs(
                    inference_session, model_outputs
                )
                for class_name in self.target_classes:
                    dets = _postprocess_to_detections(
                        processed, class_name, h, w,
                        self.confidence_threshold, self.min_mask_area_ratio,
                    )
                    canonical = class_name.split()[-1].replace(" ", "_")
                    for d in dets:
                        d.class_name = canonical
                    all_dets.extend(dets)

        gc.collect()
        return _nms(all_dets, self.iou_threshold)

    # ── 다중 프레임 추적 ──────────────────────────────────────────────────────
    def track_segment(
        self,
        frames: List[np.ndarray],
        seed_detections: Optional[List[Detection]] = None,
    ) -> List[Dict[str, Any]]:
        """
        RGB numpy 프레임 리스트 전체에 대해 SAM3 비디오 추적 수행.
        (video_analysis_system이 BGR→RGB 변환 후 전달함)
        결과: [{"frame_index": int, "detections": [det.to_dict(), ...]}, ...]
        """
        if not frames:
            return []
        if self._model is None or self._processor is None:
            logger.error(
                "SAM3 모델 미로딩 — checkpoint_path를 확인하세요. "
                f"현재 경로: {self.weights_path}"
            )
            return []
        # 예외를 삼키지 않고 그대로 전파 → UI에서 실제 오류 확인 가능
        return self._track_with_video_model(frames)

    def _track_with_video_model(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        import torch
        from PIL import Image

        h, w = frames[0].shape[:2]

        # video_analysis_system이 이미 BGR→RGB 변환 후 넘기므로 그대로 PIL로 변환
        pil_frames = [Image.fromarray(f).convert("RGB") for f in frames]

        logger.info(f"SAM3 비디오 세션 초기화: {len(pil_frames)}프레임  {w}×{h}")
        inference_session = self._processor.init_video_session(
            video=pil_frames,
            inference_device=torch.device(self.device),
            processing_device="cpu",
            video_storage_device="cpu",
            dtype=self._dtype,
        )

        # 각 텍스트 프롬프트를 독립 세션으로 처리해 obj_id → class_name 정확히 매핑
        # (단일 세션에 여러 add_text_prompt를 연속 호출하면 obj_id 귀속 불명확)
        all_frame_dets: Dict[int, List[Detection]] = {i: [] for i in range(len(frames))}

        for class_name in self.target_classes:
            canonical = class_name.split()[-1].replace(" ", "_")
            logger.info(f"텍스트 프롬프트 처리: '{class_name}'")

            # 클래스별 별도 세션
            session = self._processor.init_video_session(
                video=pil_frames,
                inference_device=torch.device(self.device),
                processing_device="cpu",
                video_storage_device="cpu",
                dtype=self._dtype,
            )
            session = self._processor.add_text_prompt(
                inference_session=session,
                text=class_name,
            )

            with torch.no_grad():
                for model_outputs in self._model.propagate_in_video_iterator(
                    inference_session=session
                ):
                    frame_idx = model_outputs.frame_idx
                    processed = self._processor.postprocess_outputs(session, model_outputs)

                    obj_ids_t = processed.get("object_ids")
                    n_obj = len(obj_ids_t) if obj_ids_t is not None else 0

                    if frame_idx == 0:
                        logger.info(
                            f"  '{class_name}' frame=0 탐지: {n_obj}개  "
                            f"scores={processed.get('scores')}"
                        )

                    # 이 세션의 모든 obj_id → class_name 매핑
                    id_to_label: Dict[int, str] = {}
                    if obj_ids_t is not None:
                        for oid in obj_ids_t.tolist():
                            id_to_label[int(oid)] = canonical

                    dets = _postprocess_to_detections(
                        processed,
                        class_name=canonical,
                        orig_h=h,
                        orig_w=w,
                        confidence_threshold=self.confidence_threshold,
                        min_mask_area_ratio=self.min_mask_area_ratio,
                        id_to_label=id_to_label,
                    )
                    all_frame_dets[frame_idx].extend(dets)

            gc.collect()

        logger.info(
            f"추적 완료 — "
            f"탐지 프레임 수: {sum(1 for d in all_frame_dets.values() if d)}"
        )

        results: List[Dict[str, Any]] = []
        for frame_idx in range(len(frames)):
            curr_dets = _nms(all_frame_dets.get(frame_idx, []), self.iou_threshold)
            if curr_dets:
                results.append({
                    "frame_index": frame_idx,
                    "detections": [d.to_dict() for d in curr_dets],
                })

        return results

    # ── 호환성 래퍼 ───────────────────────────────────────────────────────────
    def detect_video_segment(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        return self.track_segment(frames)


# 기존 코드와의 호환성을 위한 별칭
ObjectDetector = SAM3ObjectDetector
