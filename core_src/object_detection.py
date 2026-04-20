"""
SAM3 기반 객체 탐지 및 추적 모듈

Image detection API (공식):
    inference_state = processor.set_image(pil_image)
    output = processor.set_text_prompt(prompt=class_name, state=inference_state)
    masks, boxes, scores = output["masks"], output["boxes"], output["scores"]
    # 선택적 후처리 (threshold 조절):
    processed = processor.post_process_instance_segmentation(
        output, threshold=0.1, mask_threshold=0.5, target_sizes=[(h, w)]
    )

Video tracking API (공식):
    response = video_predictor.handle_request({"type": "start_session", "resource_path": jpeg_dir})
    session_id = response["session_id"]
    response = video_predictor.handle_request({"type": "add_prompt", "session_id": session_id,
                                               "frame_index": 0, "text": class_name})
    video_predictor.handle_request({"type": "propagate_in_video", "session_id": session_id})
    video_predictor.handle_request({"type": "close_session", "session_id": session_id})
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


def _parse_detections(
    output: dict,
    class_name: str,
    orig_h: int,
    orig_w: int,
    confidence_threshold: float,
    min_mask_area_ratio: float,
) -> List[Detection]:
    """SAM3 output dict → Detection 리스트 변환 (이미지/비디오 공통)."""
    masks_out  = _output_to_tensor(output.get("masks",        []))
    boxes_out  = _output_to_tensor(output.get("boxes",        []))
    scores_out = _output_to_tensor(output.get("scores",       [])).flatten()
    logits_out = _output_to_tensor(output.get("masks_logits", []))

    # scores가 없을 때 masks_logits에서 추출
    if len(scores_out) == 0 and logits_out.ndim >= 3 and logits_out.shape[0] > 0:
        import torch as _t
        _probs = _t.sigmoid(_t.as_tensor(logits_out).float())
        scores_out = (_probs.reshape(_probs.shape[0], -1) > 0.5).float().mean(dim=1).numpy()

    logger.debug(f"_parse_detections '{class_name}': scores={len(scores_out)}, masks={masks_out.shape}")

    detections: List[Detection] = []
    for i, score in enumerate(scores_out):
        score = float(score)
        if score < confidence_threshold:
            continue

        mask_np = None
        if masks_out.ndim >= 3 and i < len(masks_out):
            raw = np.squeeze(masks_out[i]).astype(bool)
            if raw.shape != (orig_h, orig_w):
                from PIL import Image as PILImage
                pm = PILImage.fromarray(raw.astype(np.uint8) * 255, "L")
                raw = np.array(pm.resize((orig_w, orig_h), PILImage.NEAREST)) > 127
            if raw.sum() / (orig_h * orig_w) >= min_mask_area_ratio:
                mask_np = raw
        elif logits_out.ndim >= 3 and i < len(logits_out):
            raw_logit = np.squeeze(logits_out[i])
            raw = (1 / (1 + np.exp(-raw_logit.astype(np.float32)))) > 0.5
            if raw.shape != (orig_h, orig_w):
                from PIL import Image as PILImage
                pm = PILImage.fromarray(raw.astype(np.uint8) * 255, "L")
                raw = np.array(pm.resize((orig_w, orig_h), PILImage.NEAREST)) > 127
            if raw.sum() / (orig_h * orig_w) >= min_mask_area_ratio:
                mask_np = raw

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


class SAM3ObjectDetector:
    """
    SAM3 기반 군사 객체 탐지 및 추적.

    - 단일 프레임 탐지: processor.set_image() + processor.set_text_prompt()
      + processor.post_process_instance_segmentation()
    - 다중 프레임 추적: video_predictor.handle_request()
      start_session → add_prompt(frame=0) → propagate_in_video → close_session
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
        warnings.filterwarnings("ignore", message="Input type.*should be the same", category=UserWarning)
        warnings.filterwarnings("ignore", message="Input type.*BFloat16", category=UserWarning)
        warnings.filterwarnings("ignore", message="Input type.*FloatTensor", category=UserWarning)
        warnings.filterwarnings("ignore", message="Kwargs passed to")
        warnings.filterwarnings("ignore", message="processor_kwargs")
        warnings.filterwarnings("ignore", message="Input type.*and bias type")
        warnings.filterwarnings("ignore", category=UserWarning, module="sam3")

        try:
            import torch
            from sam3.model_builder import build_sam3_image_model
            from sam3.model.sam3_image_processor import Sam3Processor

            dtype_str = self.config.get("dtype", "float32")
            dtype_map = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}
            torch_dtype = dtype_map.get(dtype_str, torch.float32)

            logger.info(f"SAM3 image model 로딩 ({dtype_str}): {self.checkpoint_path}")
            self._image_model = build_sam3_image_model(checkpoint_path=self.checkpoint_path)
            self._image_model = self._image_model.to(device=self.device, dtype=torch_dtype).eval()
            self._processor = Sam3Processor(self._image_model)
            logger.info("SAM3 image model loaded successfully")

            # video predictor: propagate_in_video 미지원 확인됨 → 로딩 생략
            # track_segment는 image model + IoU 추적으로 동작
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
                canonical = class_name.split()[-1].replace(" ", "_")
                for d in dets:
                    d.class_name = canonical
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
        """SAM3 공식 API: set_image → set_text_prompt → post_process_instance_segmentation."""
        import torch

        with torch.no_grad():
            inference_state = self._processor.set_image(pil_image)
            # 공식 시그니처: set_text_prompt(prompt, state)
            output = self._processor.set_text_prompt(
                prompt=class_name,
                state=inference_state,
            )
        del inference_state

        # ── post_process_instance_segmentation (공식 docs의 threshold 조절 메서드) ──
        # threshold 기본값 0.5를 confidence_threshold로 낮춰 더 많은 탐지 허용
        if hasattr(self._processor, "post_process_instance_segmentation"):
            try:
                processed = self._processor.post_process_instance_segmentation(
                    output,
                    threshold=self.confidence_threshold,
                    mask_threshold=0.5,
                    target_sizes=[(orig_h, orig_w)],
                )
                # processed: List[dict] with keys "masks", "scores", "labels"
                if processed and isinstance(processed, (list, tuple)) and len(processed) > 0:
                    result = processed[0]
                    masks_pp  = _output_to_tensor(result.get("masks",  []))
                    scores_pp = _output_to_tensor(result.get("scores", [])).flatten()
                    logger.debug(
                        f"post_process '{class_name}': scores={len(scores_pp)}, masks={masks_pp.shape}"
                    )
                    if len(scores_pp) > 0:
                        return self._build_detections_from_pp(
                            masks_pp, scores_pp, class_name, orig_h, orig_w
                        )
            except Exception as e:
                logger.debug(f"post_process_instance_segmentation 오류: {e}")

        # post_process 없거나 결과 없을 때 → output 직접 파싱
        return _parse_detections(
            output, class_name, orig_h, orig_w,
            self.confidence_threshold, self.min_mask_area_ratio,
        )

    def _build_detections_from_pp(
        self,
        masks_pp: np.ndarray,
        scores_pp: np.ndarray,
        class_name: str,
        orig_h: int,
        orig_w: int,
    ) -> List[Detection]:
        """post_process_instance_segmentation 결과 → Detection 리스트."""
        detections = []
        for i, score in enumerate(scores_pp):
            score = float(score)
            if score < self.confidence_threshold:
                continue
            if masks_pp.ndim < 2 or i >= len(masks_pp):
                continue
            raw = np.squeeze(masks_pp[i]).astype(bool)
            if raw.shape != (orig_h, orig_w):
                from PIL import Image as PILImage
                pm = PILImage.fromarray(raw.astype(np.uint8) * 255, "L")
                raw = np.array(pm.resize((orig_w, orig_h), PILImage.NEAREST)) > 127
            if raw.sum() / (orig_h * orig_w) < self.min_mask_area_ratio:
                continue
            bbox_norm = _mask_to_normalized_bbox(raw, orig_h, orig_w)
            if bbox_norm is None:
                continue
            detections.append(Detection(
                class_name=class_name,
                confidence=score,
                bbox=bbox_norm,
                mask=raw,
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
        """세그먼트 전체 프레임에 대해 탐지 + 추적을 수행합니다.

        video predictor 사용 가능 시: SAM3 propagate_in_video로 추적 (권장)
        video predictor 없을 시: image model + IoU 매칭으로 폴백
        """
        if not frames:
            return []

        step = max(1, self.config.get("detection_frame_step", 5))
        return self._track_with_image_model(frames, step)

    def _track_with_video_predictor(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        """
        SAM3 공식 video predictor API로 추적:
        1. 프레임을 temp JPEG 디렉토리에 저장
        2. start_session(resource_path=jpeg_dir)
        3. 각 클래스마다 add_prompt(frame_index=0, text=class_name)
        4. propagate_in_video → 전체 프레임 자동 추적
        5. close_session
        """
        import torch
        from PIL import Image

        if not frames:
            return []

        h, w = frames[0].shape[:2]
        # frame_idx → Detection 리스트
        frame_dets: Dict[int, List[Detection]] = {i: [] for i in range(len(frames))}

        with tempfile.TemporaryDirectory() as tmp_dir:
            # 1. 프레임 저장 (SAM3 video predictor는 JPEG 디렉토리 또는 MP4 경로를 받음)
            logger.info(f"SAM3 video predictor: {len(frames)}개 프레임을 {tmp_dir}에 저장 중")
            for i, frame in enumerate(frames):
                Image.fromarray(frame).convert("RGB").save(
                    os.path.join(tmp_dir, f"{i:05d}.jpg"),
                    format="JPEG", quality=95,
                )

            # 2. 세션 시작
            try:
                resp = self._video_predictor.handle_request(
                    request=dict(type="start_session", resource_path=tmp_dir)
                )
            except Exception as e:
                logger.error(f"start_session 실패: {e}")
                raise

            session_id = resp["session_id"]
            logger.info(f"SAM3 video session 시작: {session_id}")

            try:
                # 3. 각 클래스에 대해 frame 0에 텍스트 프롬프트 추가
                for class_name in self.target_classes:
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    try:
                        add_resp = self._video_predictor.handle_request(
                            request=dict(
                                type="add_prompt",
                                session_id=session_id,
                                frame_index=0,
                                text=class_name,
                            )
                        )
                        # frame 0 결과 파싱
                        output = add_resp.get("outputs", {})
                        dets = _parse_detections(
                            output, class_name, h, w,
                            self.confidence_threshold, self.min_mask_area_ratio,
                        )
                        canonical = class_name.split()[-1].replace(" ", "_")
                        for d in dets:
                            d.class_name = canonical
                        frame_dets[0].extend(dets)
                        logger.debug(f"add_prompt '{class_name}' frame=0 → {len(dets)}개")
                    except Exception as e:
                        logger.warning(f"add_prompt '{class_name}': {e}")

                # 4. 전체 비디오 전파 — SAM3 버전에 따라 request type이 다를 수 있음
                _propagate_types = [
                    "propagate_in_video",
                    "propagate",
                    "track",
                    "track_in_video",
                    "run_propagation",
                ]
                _propagated = False
                for _ptype in _propagate_types:
                    try:
                        prop_resp = self._video_predictor.handle_request(
                            request=dict(type=_ptype, session_id=session_id)
                        )
                        logger.info(f"propagate request type '{_ptype}' 성공")
                        self._parse_propagate_response(prop_resp, frame_dets, h, w)
                        _propagated = True
                        break
                    except Exception as e:
                        err = str(e)
                        if "invalid request type" in err or "unknown" in err.lower():
                            logger.debug(f"'{_ptype}' 미지원, 다음 시도")
                            continue
                        logger.warning(f"propagate '{_ptype}' 오류: {e}")
                        break
                if not _propagated:
                    logger.warning(
                        "propagate 지원 request type을 찾지 못했습니다. "
                        "frame 0 탐지 결과만 사용합니다."
                    )

            finally:
                # 5. 세션 종료 (공식 docs: close_session)
                try:
                    self._video_predictor.handle_request(
                        request=dict(type="close_session", session_id=session_id)
                    )
                    logger.info(f"SAM3 video session 종료: {session_id}")
                except Exception:
                    pass

        # IoU 기반 track_id 부여
        results: List[Dict[str, Any]] = []
        prev_dets: List[Detection] = []
        next_track_id = 0

        for frame_idx in range(len(frames)):
            curr_dets = _nms(frame_dets.get(frame_idx, []), self.iou_threshold)
            next_track_id = self._assign_track_ids(curr_dets, prev_dets, next_track_id)
            if curr_dets:
                results.append({
                    "frame_index": frame_idx,
                    "detections": [d.to_dict() for d in curr_dets],
                })
            prev_dets = curr_dets

        return results

    def _parse_propagate_response(
        self,
        prop_resp: Any,
        frame_dets: Dict[int, List[Detection]],
        h: int,
        w: int,
    ) -> None:
        """propagate_in_video 응답을 frame_dets에 파싱 (다양한 응답 포맷 처리)."""
        if prop_resp is None:
            return

        # 포맷 A: {"frames": {frame_idx: output_dict, ...}}
        if isinstance(prop_resp, dict):
            frames_data = prop_resp.get("frames", prop_resp.get("outputs", {}))
            if isinstance(frames_data, dict):
                for fidx, fout in frames_data.items():
                    fidx = int(fidx)
                    if not (0 <= fidx < len(frame_dets)):
                        continue
                    if not isinstance(fout, dict):
                        continue
                    for class_name in self.target_classes:
                        dets = _parse_detections(
                            fout, class_name, h, w,
                            self.confidence_threshold, self.min_mask_area_ratio,
                        )
                        canonical = class_name.split()[-1].replace(" ", "_")
                        for d in dets:
                            d.class_name = canonical
                        frame_dets[fidx].extend(dets)
                return

        # 포맷 B: 이터레이터 — (frame_idx, obj_ids, mask_logits) 또는 (frame_idx, output_dict)
        if hasattr(prop_resp, "__iter__"):
            try:
                for item in prop_resp:
                    if not isinstance(item, (tuple, list)) or len(item) < 2:
                        continue
                    fidx = int(item[0])
                    if not (0 <= fidx < len(frame_dets)):
                        continue
                    fout = item[1] if isinstance(item[1], dict) else {}
                    for class_name in self.target_classes:
                        dets = _parse_detections(
                            fout, class_name, h, w,
                            self.confidence_threshold, self.min_mask_area_ratio,
                        )
                        canonical = class_name.split()[-1].replace(" ", "_")
                        for d in dets:
                            d.class_name = canonical
                        frame_dets[fidx].extend(dets)
            except Exception as e:
                logger.debug(f"propagate iterator 파싱 오류: {e}")

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

    def _track_with_image_model(self, frames: List[np.ndarray], step: int = 5) -> List[Dict[str, Any]]:
        """image model 직접 탐지 + IoU 추적 (video predictor 폴백).
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
