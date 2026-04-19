"""
SAM3 기반 객체 탐지 및 추적 모듈

Image detection API:
    inference_state = processor.set_image(pil_image)
    output = processor.set_text_prompt(state=inference_state, prompt=class_name)
    masks, boxes, scores = output["masks"], output["boxes"], output["scores"]
    del inference_state  # GPU 메모리 즉시 해제

Video tracking API:
    response = video_predictor.handle_request({"type": "start_session", "resource_path": path})
    session_id = response["session_id"]
    response = video_predictor.handle_request({"type": "add_prompt", "session_id": session_id,
                                               "frame_index": 0, "text": class_name})
    output = response["outputs"]  # {"boxes", "scores", "masks"}
    video_predictor.handle_request({"type": "end_session", "session_id": session_id})
"""
import gc
import sys
import os
import tempfile
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
        self.bbox = bbox  # [x1, y1, x2, y2] normalized (0~1)
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


def _iou(a: Tuple, b: Tuple) -> float:
    """두 bbox (x1,y1,x2,y2) 간 IoU 계산."""
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
    """클래스별 NMS."""
    if not detections:
        return []
    by_class: Dict[str, List[Detection]] = {}
    for d in detections:
        by_class.setdefault(d.class_name, []).append(d)

    kept = []
    for dets in by_class.values():
        dets = sorted(dets, key=lambda x: x.confidence, reverse=True)
        remaining = list(range(len(dets)))
        while remaining:
            best = remaining.pop(0)
            kept.append(dets[best])
            # best bbox (pixel)
            bx = tuple(dets[best].bbox)
            remaining = [
                i for i in remaining
                if _iou(bx, tuple(dets[i].bbox)) < iou_threshold
            ]
    return kept


def _mask_to_normalized_bbox(mask: np.ndarray, h: int, w: int) -> Optional[List[float]]:
    """마스크 → 정규화된 bbox [x1n, y1n, x2n, y2n]."""
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any():
        return None
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    return [cmin / w, rmin / h, cmax / w, rmax / h]


def _output_to_tensor(val):
    """CPU numpy array로 변환."""
    if val is None:
        return np.array([])
    if hasattr(val, "cpu"):
        val = val.cpu().numpy()
    return np.asarray(val)


class SAM3ObjectDetector:
    """
    SAM3 기반 군사 객체 탐지 및 추적.

    - 단일 프레임 탐지: processor.set_image() + processor.set_text_prompt()
    - 다중 프레임 추적: video_predictor.handle_request() + IoU 매칭
    """

    def __init__(self, config: dict):
        self.config = config
        self.device = config.get("device", "cuda")
        self.target_classes = config.get("target_classes", [])
        self.confidence_threshold = config.get("confidence_threshold", 0.3)
        self.iou_threshold = config.get("iou_threshold", 0.5)
        self.min_mask_area_ratio = config.get("min_mask_area_ratio", 0.001)

        sam3_path = os.environ.get("SAM3_PATH") or config.get("sam3_path", "")
        self.checkpoint_path = os.environ.get("SAM3_CHECKPOINT") or config.get("checkpoint_path", "")

        if not sam3_path or not self.checkpoint_path:
            logger.warning(
                "SAM3 경로 미설정. 환경 변수 SAM3_PATH / SAM3_CHECKPOINT 또는 "
                "models_config.yaml의 sam3_path / checkpoint_path를 설정하세요."
            )

        if sam3_path and sam3_path not in sys.path:
            sys.path.insert(0, sam3_path)
            logger.info(f"SAM3 path added: {sam3_path}")

        self._image_model = None
        self._processor = None
        self._video_predictor = None
        self._load_models()

    # ──────────────────────────────────────────────────────
    # 모델 로딩
    # ──────────────────────────────────────────────────────

    def _load_models(self):
        import warnings
        # video predictor BFloat16/float 불일치 경고 억제
        warnings.filterwarnings(
            "ignore",
            message="Input type.*BFloat16.*bias type",
            category=UserWarning,
        )
        # transformers 신버전 processor_kwargs 경고 억제 (SAM3 내부 호환성 이슈)
        warnings.filterwarnings(
            "ignore",
            message="Kwargs passed to.*processor.*processor_kwargs",
        )

        try:
            import torch
            from sam3.model_builder import build_sam3_image_model
            from sam3.model.sam3_image_processor import Sam3Processor

            dtype_str = self.config.get("dtype", "bfloat16")
            dtype_map = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}
            torch_dtype = dtype_map.get(dtype_str, torch.bfloat16)

            logger.info(f"SAM3 image model 로딩 ({dtype_str}): {self.checkpoint_path}")
            self._image_model = build_sam3_image_model(checkpoint_path=self.checkpoint_path)
            self._image_model = self._image_model.to(device=self.device, dtype=torch_dtype).eval()
            self._processor = Sam3Processor(self._image_model)
            logger.info("SAM3 image model loaded successfully")

            # video predictor: dtype 불일치 + processor_kwargs 이슈로 탐지 실패 사례 있음
            # 로드만 해두고 실제 탐지는 image model을 기본으로 사용
            try:
                from sam3.model_builder import build_sam3_video_predictor
                logger.info("SAM3 video predictor 로딩")
                self._video_predictor = build_sam3_video_predictor(checkpoint_path=self.checkpoint_path)
                logger.info("SAM3 video predictor loaded")
            except Exception as e:
                logger.warning(f"SAM3 video predictor 로딩 실패 (image model으로 대체): {e}")
                self._video_predictor = None

        except ModuleNotFoundError as e:
            logger.error(f"SAM3 모듈 없음: {e}  →  sam3_path 확인 필요")
        except FileNotFoundError as e:
            logger.error(f"SAM3 체크포인트 없음: {e}  →  checkpoint_path 확인 필요")
        except Exception as e:
            logger.error(f"SAM3 로딩 실패: {e}", exc_info=True)

    # ──────────────────────────────────────────────────────
    # 단일 프레임 탐지
    # ──────────────────────────────────────────────────────

    def detect(self, frame: np.ndarray) -> List[Detection]:
        if self._processor is None:
            return []
        try:
            return self._detect_frame(frame)
        except Exception as e:
            logger.error(f"SAM3 detect error: {e}")
            return []

    def _detect_frame(self, frame: np.ndarray) -> List[Detection]:
        import torch
        from PIL import Image

        pil_image = Image.fromarray(frame).convert("RGB")
        h, w = frame.shape[:2]
        all_dets: List[Detection] = []

        for class_name in self.target_classes:
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            try:
                dets = self._detect_class(pil_image, class_name, w, h)
                all_dets.extend(dets)
            except torch.cuda.OutOfMemoryError:
                logger.warning(f"OOM on class '{class_name}' — skipping")
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception as e:
                logger.warning(f"detect class '{class_name}': {e}")

        return _nms(all_dets, self.iou_threshold)

    def _detect_class(
        self,
        pil_image,
        class_name: str,
        orig_w: int,
        orig_h: int,
    ) -> List[Detection]:
        """SAM3 set_image + set_text_prompt로 단일 클래스 탐지."""
        import torch

        with torch.no_grad():
            inference_state = self._processor.set_image(pil_image)
            output = self._processor.set_text_prompt(
                state=inference_state,
                prompt=class_name,
            )
        del inference_state  # GPU 텐서 즉시 해제

        # 디버그: 실제 반환값 확인 (탐지 0건 진단용)
        _raw_scores = _output_to_tensor(output.get("scores", [])).flatten()
        logger.debug(
            f"set_text_prompt('{class_name}') → "
            f"scores={len(_raw_scores)} 개, "
            f"max={float(_raw_scores.max()) if len(_raw_scores) else 'N/A':.3f}, "
            f"keys={list(output.keys())}"
        )

        masks_out  = _output_to_tensor(output.get("masks",  []))
        boxes_out  = _output_to_tensor(output.get("boxes",  []))
        scores_out = _output_to_tensor(output.get("scores", [])).flatten()

        detections: List[Detection] = []
        for i, score in enumerate(scores_out):
            score = float(score)
            if score < self.confidence_threshold:
                continue

            # 마스크 처리
            mask_np = None
            if masks_out.ndim >= 3 and i < len(masks_out):
                raw = np.squeeze(masks_out[i]).astype(bool)
                if raw.shape != (orig_h, orig_w):
                    from PIL import Image as PILImage
                    pm = PILImage.fromarray(raw.astype(np.uint8) * 255, "L")
                    pm = pm.resize((orig_w, orig_h), PILImage.NEAREST)
                    raw = np.array(pm) > 127
                # 면적 필터
                if raw.sum() / (orig_h * orig_w) < self.min_mask_area_ratio:
                    continue
                mask_np = raw

            # bbox 결정: 마스크 우선, 없으면 boxes
            if mask_np is not None and mask_np.any():
                bbox_norm = _mask_to_normalized_bbox(mask_np, orig_h, orig_w)
                if bbox_norm is None:
                    continue
            elif i < len(boxes_out):
                x1, y1, x2, y2 = boxes_out[i].tolist()
                if (x2 - x1) < 4 or (y2 - y1) < 4:
                    continue
                bbox_norm = [x1 / orig_w, y1 / orig_h, x2 / orig_w, y2 / orig_h]
            else:
                continue

            detections.append(Detection(
                class_name=class_name,
                confidence=score,
                bbox=bbox_norm,
                mask=mask_np,
            ))

        return detections

    # ──────────────────────────────────────────────────────
    # 다중 프레임 추적
    # ──────────────────────────────────────────────────────

    def track_segment(
        self,
        frames: List[np.ndarray],
        seed_detections: Optional[List[Detection]] = None,
    ) -> List[Dict[str, Any]]:
        """
        세그먼트 전체 프레임에 대해 탐지 + IoU 추적을 수행합니다.

        메모리 절약을 위해 detection_frame_step마다 한 번씩만 SAM3를 실행하고,
        중간 프레임은 이전 탐지 결과를 그대로 이어붙입니다.
        """
        if not frames:
            return []

        # N 프레임마다 한 번 탐지 (config: detection_frame_step, 기본 5)
        step = max(1, self.config.get("detection_frame_step", 5))

        # image model을 기본으로 사용 (video predictor는 dtype/processor_kwargs 이슈로 불안정)
        return self._track_with_image_model(frames, step)

    def _detect_frame_via_predictor(
        self,
        tmp_path: str,
        h: int,
        w: int,
    ) -> List[Detection]:
        """video predictor handle_request 세션으로 단일 프레임 탐지."""
        import torch

        response = self._video_predictor.handle_request(
            request=dict(type="start_session", resource_path=tmp_path)
        )
        session_id = response["session_id"]
        all_dets: List[Detection] = []

        for class_name in self.target_classes:
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            try:
                resp = self._video_predictor.handle_request(
                    request=dict(
                        type="add_prompt",
                        session_id=session_id,
                        frame_index=0,
                        text=class_name,
                    )
                )
                output = resp.get("outputs", {})
                boxes_out  = _output_to_tensor(output.get("boxes",  []))
                scores_out = _output_to_tensor(output.get("scores", [])).flatten()
                masks_out  = _output_to_tensor(output.get("masks",  []))

                for i, score in enumerate(scores_out):
                    score = float(score)
                    if score < self.confidence_threshold:
                        continue

                    mask_np = None
                    if masks_out.ndim >= 3 and i < len(masks_out):
                        raw = np.squeeze(masks_out[i]).astype(bool)
                        if raw.shape != (h, w):
                            from PIL import Image as PILImage
                            pm = PILImage.fromarray(raw.astype(np.uint8) * 255, "L")
                            pm = pm.resize((w, h), PILImage.NEAREST)
                            raw = np.array(pm) > 127
                        if raw.sum() / (h * w) < self.min_mask_area_ratio:
                            continue
                        mask_np = raw

                    if mask_np is not None and mask_np.any():
                        bbox_norm = _mask_to_normalized_bbox(mask_np, h, w)
                        if bbox_norm is None:
                            continue
                    elif i < len(boxes_out):
                        x1, y1, x2, y2 = boxes_out[i].tolist()
                        if (x2 - x1) < 4 or (y2 - y1) < 4:
                            continue
                        bbox_norm = [x1 / w, y1 / h, x2 / w, y2 / h]
                    else:
                        continue

                    all_dets.append(Detection(
                        class_name=class_name,
                        confidence=score,
                        bbox=bbox_norm,
                        mask=mask_np,
                    ))
            except Exception as e:
                logger.warning(f"predictor class '{class_name}': {e}")

        try:
            self._video_predictor.handle_request(
                request=dict(type="end_session", session_id=session_id)
            )
        except Exception:
            pass

        return _nms(all_dets, self.iou_threshold)

    def _assign_track_ids(
        self,
        curr_dets: List[Detection],
        prev_dets: List[Detection],
        next_track_id: int,
    ) -> int:
        """IoU 기반 track_id 이어붙이기. 변경된 next_track_id를 반환."""
        used_prev = set()
        for det in curr_dets:
            best_iou, best_prev = 0.0, None
            for pi, prev in enumerate(prev_dets):
                if pi in used_prev or prev.class_name != det.class_name:
                    continue
                iou = _iou(tuple(det.bbox), tuple(prev.bbox))
                if iou > best_iou:
                    best_iou, best_prev = iou, pi
            if best_prev is not None and best_iou >= 0.1:
                det.track_id = prev_dets[best_prev].track_id
                used_prev.add(best_prev)
            else:
                det.track_id = next_track_id
                next_track_id += 1
        return next_track_id

    def _track_with_video_predictor(self, frames: List[np.ndarray], step: int = 5) -> List[Dict[str, Any]]:
        """
        video predictor + IoU 매칭으로 세그먼트 추적.
        step 프레임마다 한 번 SAM3 실행, 중간 프레임은 직전 탐지 결과 재사용.
        """
        import torch
        from PIL import Image

        results: List[Dict[str, Any]] = []
        prev_dets: List[Detection] = []
        next_track_id = 0

        for frame_idx, frame in enumerate(frames):
            h, w = frame.shape[:2]

            if frame_idx % step == 0:
                # SAM3 실행 프레임
                pil_img = Image.fromarray(frame).convert("RGB")
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    pil_img.save(tmp_path, format="JPEG", quality=95)
                    curr_dets = self._detect_frame_via_predictor(tmp_path, h, w)
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            else:
                # 중간 프레임: 직전 탐지 결과 복사 (track_id 유지)
                curr_dets = [
                    Detection(d.class_name, d.confidence, d.bbox[:], track_id=d.track_id)
                    for d in prev_dets
                ]

            next_track_id = self._assign_track_ids(curr_dets, prev_dets, next_track_id)

            if curr_dets:
                results.append({
                    "frame_index": frame_idx,
                    "detections": [d.to_dict() for d in curr_dets],
                })
            prev_dets = curr_dets

        return results

    def _track_with_image_model(self, frames: List[np.ndarray], step: int = 5) -> List[Dict[str, Any]]:
        """image model 직접 탐지 + IoU 추적 (video predictor 없을 때 폴백).
        step 프레임마다 한 번 탐지, 중간 프레임은 직전 결과 재사용."""
        import torch

        results: List[Dict[str, Any]] = []
        prev_dets: List[Detection] = []
        next_track_id = 0

        for frame_idx, frame in enumerate(frames):
            if frame_idx % step == 0:
                curr_dets = self._detect_frame(frame)
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            else:
                curr_dets = [
                    Detection(d.class_name, d.confidence, d.bbox[:], track_id=d.track_id)
                    for d in prev_dets
                ]

            next_track_id = self._assign_track_ids(curr_dets, prev_dets, next_track_id)

            if curr_dets:
                results.append({
                    "frame_index": frame_idx,
                    "detections": [d.to_dict() for d in curr_dets],
                })
            prev_dets = curr_dets

        return results

    def detect_video_segment(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        """track_segment() 호환 래퍼."""
        return self.track_segment(frames)


# 기존 코드와의 호환성을 위한 별칭
ObjectDetector = SAM3ObjectDetector
