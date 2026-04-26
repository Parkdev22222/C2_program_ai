#!/usr/bin/env python3
"""
SAM3 비디오 객체 탐지 및 추적 스크립트 (Transformers API)

참고: Sam3TrackerVideoModel + Sam3TrackerVideoProcessor (HuggingFace transformers)
      propagate_in_video_iterator로 전체 프레임 자동 추적

사용 예시:
  # 텍스트 프롬프트 (Sam3Processor로 frame 0 탐지 → 박스를 트래커에 전달)
  python sam3_track_video.py --video input.mp4 --prompt "soldier"
  python sam3_track_video.py --video input.mp4 --prompt "soldier" --prompt "tank"

  # 포인트 프롬프트 (직접 좌표 지정)
  python sam3_track_video.py --video input.mp4 --point 640,360

  # models_config.yaml의 target_classes 사용
  python sam3_track_video.py --video samples/sample.mp4 --use-config --step 5
"""
import argparse
import gc
import logging
import os
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
from PIL import Image

warnings.filterwarnings("ignore", category=UserWarning, module="sam3")
warnings.filterwarnings("ignore", message="Input type.*bias type")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# 색상 팔레트
# ─────────────────────────────────────────────────────────────────
_COLORS_BGR = [
    (0, 255, 0),    # green
    (0, 0, 255),    # red
    (255, 0, 0),    # blue
    (0, 255, 255),  # yellow
    (255, 0, 255),  # magenta
    (0, 165, 255),  # orange
    (255, 128, 0),  # sky blue
    (128, 0, 255),  # purple
    (0, 255, 128),  # spring green
    (128, 255, 0),  # chartreuse
]

def _color(idx: int) -> Tuple[int, int, int]:
    return _COLORS_BGR[idx % len(_COLORS_BGR)]


# ─────────────────────────────────────────────────────────────────
# 설정 로딩
# ─────────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        import yaml
        cfg_path = Path(__file__).parent / "config" / "models_config.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        od = cfg.get("object_detection", {})
        return {
            "sam3_path":       od.get("sam3_path", ""),
            "checkpoint_path": od.get("checkpoint_path", ""),
            "target_classes":  od.get("target_classes", []),
            "conf_threshold":  od.get("confidence_threshold", 0.01),
        }
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────
# 모델 로딩
# ─────────────────────────────────────────────────────────────────

def load_tracker_model(hf_model_id: str, device: torch.device, dtype: torch.dtype):
    """Sam3TrackerVideoModel + Sam3TrackerVideoProcessor 로딩."""
    from transformers import Sam3TrackerVideoModel, Sam3TrackerVideoProcessor

    logger.info(f"Sam3TrackerVideoModel 로딩: {hf_model_id}")
    model = Sam3TrackerVideoModel.from_pretrained(hf_model_id).to(device, dtype=dtype)
    model.eval()
    processor = Sam3TrackerVideoProcessor.from_pretrained(hf_model_id)
    logger.info("Sam3TrackerVideoModel 로딩 완료")
    return model, processor


def load_image_detector(sam3_path: str, checkpoint_path: str, device: torch.device):
    """텍스트 프롬프트 초기 탐지용 Sam3Processor 로딩."""
    if sam3_path and sam3_path not in sys.path:
        sys.path.insert(0, sam3_path)

    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor

    logger.info(f"Sam3Processor (image) 로딩: {checkpoint_path}")
    image_model = build_sam3_image_model(checkpoint_path=checkpoint_path)
    image_model = image_model.to(device=device, dtype=torch.float32).eval()
    img_processor = Sam3Processor(image_model)
    logger.info("Sam3Processor 로딩 완료")
    return img_processor


# ─────────────────────────────────────────────────────────────────
# 비디오 로딩 / 저장
# ─────────────────────────────────────────────────────────────────

def load_video(video_path: str) -> Tuple[List[np.ndarray], float, int, int]:
    """mp4/avi → BGR numpy frame list + (fps, w, h)"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"영상을 열 수 없습니다: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    logger.info(f"영상: {w}×{h}  {fps:.1f}fps  {total}프레임")

    frames = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame)
    cap.release()
    return frames, fps, w, h


def bgr_frames_to_pil_rgb(frames: List[np.ndarray]) -> List[Image.Image]:
    return [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames]


def save_video(frames: List[np.ndarray], output_path: str, fps: float, w: int, h: int):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
    for f in frames:
        out.write(f)
    out.release()
    logger.info(f"저장 완료: {output_path}")


# ─────────────────────────────────────────────────────────────────
# 텍스트 → 포인트/박스 변환 (Sam3Processor 사용)
# ─────────────────────────────────────────────────────────────────

def detect_boxes_with_text(
    img_processor,
    pil_frame: Image.Image,
    class_name: str,
    conf_threshold: float,
) -> List[Tuple[int, int, int, int]]:
    """Sam3Processor.set_text_prompt으로 frame에서 박스 탐지 → [(x1,y1,x2,y2), ...]"""
    w, h = pil_frame.size
    try:
        with torch.no_grad():
            state = img_processor.set_image(pil_frame)
            output = img_processor.set_text_prompt(prompt=class_name, state=state)
        del state

        boxes_raw  = output.get("boxes",  None)
        scores_raw = output.get("scores", None)
        masks_raw  = output.get("masks",  None)

        if boxes_raw is None and masks_raw is None:
            return []

        def _np(v):
            if v is None: return np.array([])
            return v.cpu().numpy() if hasattr(v, "cpu") else np.asarray(v)

        boxes_np  = _np(boxes_raw)
        scores_np = _np(scores_raw).flatten()
        masks_np  = _np(masks_raw)

        result = []
        for i, score in enumerate(scores_np):
            if float(score) < conf_threshold:
                continue
            # 마스크 우선으로 bbox 추출
            if masks_np.ndim >= 3 and i < len(masks_np):
                mask = np.squeeze(masks_np[i]).astype(bool)
                if mask.shape != (h, w):
                    pm = Image.fromarray(mask.astype(np.uint8) * 255, "L")
                    mask = np.array(pm.resize((w, h), Image.NEAREST)) > 127
                rows = np.where(np.any(mask, axis=1))[0]
                cols = np.where(np.any(mask, axis=0))[0]
                if len(rows) and len(cols):
                    result.append((int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])))
            elif i < len(boxes_np):
                x1, y1, x2, y2 = boxes_np[i].tolist()
                if max(x2, y2) <= 1.0:
                    x1, y1, x2, y2 = x1*w, y1*h, x2*w, y2*h
                result.append((int(x1), int(y1), int(x2), int(y2)))
        return result
    except Exception as e:
        logger.warning(f"텍스트 탐지 실패 '{class_name}': {e}")
        return []


def box_to_center_point(box: Tuple[int,int,int,int]) -> Tuple[int,int]:
    x1, y1, x2, y2 = box
    return ((x1 + x2) // 2, (y1 + y2) // 2)


# ─────────────────────────────────────────────────────────────────
# 마스크 후처리 + 렌더링
# ─────────────────────────────────────────────────────────────────

def tensor_mask_to_np(mask_tensor: torch.Tensor, target_h: int, target_w: int) -> np.ndarray:
    """pred_masks (1, 1, H, W) 또는 (1, H, W) → bool (H, W)"""
    m = mask_tensor.squeeze()
    if m.dtype != torch.bool:
        m = m > 0.0
    m = m.cpu().numpy()
    if m.shape != (target_h, target_w):
        pil = Image.fromarray(m.astype(np.uint8) * 255, "L")
        m = np.array(pil.resize((target_w, target_h), Image.NEAREST)) > 127
    return m


def draw_mask_and_box(
    frame: np.ndarray,
    mask: np.ndarray,
    label: str,
    color: Tuple[int,int,int],
    alpha: float = 0.45,
) -> np.ndarray:
    out = frame.copy()

    # 마스크 오버레이
    if mask is not None and mask.any():
        overlay = out.copy()
        overlay[mask] = color
        out = cv2.addWeighted(overlay, alpha, out, 1 - alpha, 0)

        # 마스크에서 bbox 계산
        rows = np.where(np.any(mask, axis=1))[0]
        cols = np.where(np.any(mask, axis=0))[0]
        if len(rows) and len(cols):
            x1, y1 = int(cols[0]), int(rows[0])
            x2, y2 = int(cols[-1]), int(rows[-1])
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            (tw, th), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(out, (x1, y1 - th - bl - 4), (x1 + tw, y1), color, -1)
            cv2.putText(out, label, (x1, y1 - bl - 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return out


# ─────────────────────────────────────────────────────────────────
# 핵심 추적 로직
# ─────────────────────────────────────────────────────────────────

def run_tracking(
    model,
    processor,
    img_processor,           # None이면 포인트 모드
    bgr_frames: List[np.ndarray],
    prompts: List[str],      # 텍스트 프롬프트 목록
    manual_points: List[Tuple[int,int]],  # 직접 지정 포인트 목록
    device: torch.device,
    dtype: torch.dtype,
    conf_threshold: float,
    prompt_frame_idx: int = 0,
) -> List[np.ndarray]:

    h, w = bgr_frames[0].shape[:2]
    pil_frames = bgr_frames_to_pil_rgb(bgr_frames)

    # ── 세션 초기화 ──────────────────────────────────────────────
    logger.info("비디오 세션 초기화 중...")
    inference_session = processor.init_video_session(
        video=pil_frames,
        inference_device=device,
        dtype=dtype,
    )
    logger.info(f"세션 초기화 완료  ({len(pil_frames)}프레임, {w}×{h})")

    # ── 객체별 프롬프트 등록 ─────────────────────────────────────
    obj_id = 1
    obj_label_map: Dict[int, str] = {}   # obj_id → label

    # 1) 텍스트 프롬프트 → 박스 탐지 → 중심점으로 변환
    if img_processor is not None and prompts:
        pil_frame0 = pil_frames[prompt_frame_idx]
        for class_name in prompts:
            boxes = detect_boxes_with_text(
                img_processor, pil_frame0, class_name, conf_threshold
            )
            if not boxes:
                logger.warning(f"'{class_name}': frame {prompt_frame_idx}에서 탐지 없음 — 건너뜀")
                continue

            for box in boxes:
                cx, cy = box_to_center_point(box)
                logger.info(f"'{class_name}' obj_id={obj_id}  박스={box}  중심=({cx},{cy})")
                processor.add_inputs_to_inference_session(
                    inference_session=inference_session,
                    frame_idx=prompt_frame_idx,
                    obj_ids=obj_id,
                    input_points=[[[[cx, cy]]]],
                    input_labels=[[[1]]],
                )
                obj_label_map[obj_id] = class_name
                obj_id += 1

    # 2) 수동 포인트 프롬프트
    for cx, cy in manual_points:
        label = f"obj{obj_id}"
        logger.info(f"수동 포인트 obj_id={obj_id}  ({cx},{cy})")
        processor.add_inputs_to_inference_session(
            inference_session=inference_session,
            frame_idx=prompt_frame_idx,
            obj_ids=obj_id,
            input_points=[[[[cx, cy]]]],
            input_labels=[[[1]]],
        )
        obj_label_map[obj_id] = label
        obj_id += 1

    if obj_id == 1:
        logger.error("등록된 객체가 없습니다. 원본 영상을 그대로 저장합니다.")
        return bgr_frames[:]

    logger.info(f"총 {obj_id - 1}개 객체 등록 완료")

    # ── frame 0 선택적 단일 프레임 세그멘테이션 (선택) ───────────
    try:
        outputs = model(
            inference_session=inference_session,
            frame_idx=prompt_frame_idx,
        )
        logger.info(f"frame {prompt_frame_idx} 세그멘테이션 완료")
    except Exception as e:
        logger.warning(f"단일 프레임 세그멘테이션 실패: {e}")

    # ── propagate_in_video_iterator로 전체 추적 ─────────────────
    logger.info("전체 비디오 추적 시작 (propagate_in_video_iterator)...")
    # frame_idx → {obj_id: mask_np} 저장
    video_segments: Dict[int, Dict[int, np.ndarray]] = {}

    for output in model.propagate_in_video_iterator(inference_session):
        fidx = output.frame_idx
        masks_pp = processor.post_process_masks(
            [output.pred_masks],
            original_sizes=[[inference_session.video_height, inference_session.video_width]],
            binarize=True,
        )[0]   # shape: (num_objects, 1, H, W) or (num_objects, H, W)

        seg = {}
        obj_ids_out = output.obj_ids if hasattr(output, "obj_ids") else list(obj_label_map.keys())
        for oi, oid in enumerate(obj_ids_out):
            if oi < len(masks_pp):
                mask_t = masks_pp[oi]
                seg[int(oid)] = tensor_mask_to_np(mask_t, h, w)
        video_segments[fidx] = seg

        if fidx % 30 == 0:
            logger.info(f"  추적 진행: frame {fidx}/{len(bgr_frames)}")

    logger.info(f"추적 완료: {len(video_segments)}프레임")

    # ── 결과 렌더링 ───────────────────────────────────────────────
    annotated = [f.copy() for f in bgr_frames]

    for fidx, seg in video_segments.items():
        if fidx >= len(annotated):
            continue
        for oid, mask in seg.items():
            label = obj_label_map.get(oid, f"obj{oid}")
            color = _color(oid - 1)
            annotated[fidx] = draw_mask_and_box(annotated[fidx], mask, label, color)

    # 프레임 번호 표시
    for i, frame in enumerate(annotated):
        cv2.putText(frame, f"Frame {i+1}/{len(annotated)}",
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 220, 220), 1, cv2.LINE_AA)

    return annotated


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SAM3 비디오 객체 탐지 및 추적 (Transformers API)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--video",      required=True, help="입력 영상 (.mp4 / .avi / ...)")
    p.add_argument("--output",     default="",    help="출력 영상 경로 (미입력 시 자동 생성)")
    p.add_argument("--prompt",     action="append", dest="prompts", default=[],
                   metavar="TEXT",  help="탐지할 객체 텍스트 (반복 가능)")
    p.add_argument("--point",      action="append", dest="points", default=[],
                   metavar="X,Y",   help="수동 포인트 프롬프트 (예: --point 640,360)")
    p.add_argument("--use-config", action="store_true",
                   help="models_config.yaml의 target_classes를 프롬프트로 사용")
    p.add_argument("--hf-model",   default="facebook/sam3",
                   help="HuggingFace 모델 ID (기본: facebook/sam3)")
    p.add_argument("--sam3-path",  default="", help="sam3 레포 경로 (텍스트 탐지용)")
    p.add_argument("--checkpoint", default="", help="sam3 가중치 경로 (텍스트 탐지용)")
    p.add_argument("--conf",       type=float, default=None, help="confidence threshold")
    p.add_argument("--frame",      type=int,   default=0,    help="프롬프트 기준 프레임 인덱스")
    p.add_argument("--dtype",      default="bfloat16",
                   choices=["bfloat16", "float16", "float32"],
                   help="모델 dtype (기본: bfloat16)")
    p.add_argument("--device",     default="",
                   help="cuda / cpu (미입력 시 자동 감지)")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    cfg = load_config()

    # 장치 / dtype
    if args.device:
        device = torch.device(args.device)
    else:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dtype_map = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}
    dtype = dtype_map[args.dtype]
    logger.info(f"device={device}  dtype={args.dtype}")

    # 프롬프트 결정
    prompts = args.prompts[:]
    if args.use_config or (not prompts and not args.points):
        prompts = cfg.get("target_classes", []) or prompts
    if not prompts and not args.points:
        parser.error("--prompt / --point / --use-config 중 하나를 지정하세요.")

    # 포인트 파싱
    manual_points: List[Tuple[int,int]] = []
    for pt in args.points:
        try:
            x, y = pt.split(",")
            manual_points.append((int(x), int(y)))
        except Exception:
            parser.error(f"--point 형식 오류: '{pt}'  →  예: --point 640,360")

    conf_threshold = args.conf if args.conf is not None else cfg.get("conf_threshold", 0.01)

    # 출력 경로
    video_path = Path(args.video)
    output_path = args.output or str(video_path.parent / f"{video_path.stem}_tracked.mp4")

    logger.info(f"입력   : {video_path}")
    logger.info(f"출력   : {output_path}")
    logger.info(f"프롬프트: {prompts}")
    logger.info(f"포인트 : {manual_points}")
    logger.info(f"conf   : {conf_threshold}")

    # 모델 로딩
    tracker_model, tracker_proc = load_tracker_model(args.hf_model, device, dtype)

    # 텍스트 탐지용 image processor (있을 때만)
    img_processor = None
    if prompts:
        sam3_path = args.sam3_path or cfg.get("sam3_path", "")
        checkpoint = args.checkpoint or cfg.get("checkpoint_path", "")
        if sam3_path and checkpoint:
            try:
                img_processor = load_image_detector(sam3_path, checkpoint, device)
            except Exception as e:
                logger.warning(f"image detector 로딩 실패: {e}")
                logger.warning("텍스트 프롬프트 없이 포인트 모드로만 동작합니다.")
        else:
            logger.warning("sam3_path / checkpoint 미설정 — 텍스트 탐지 불가")

    # 영상 로딩
    bgr_frames, fps, w, h = load_video(str(video_path))

    # 추적 실행
    annotated = run_tracking(
        model=tracker_model,
        processor=tracker_proc,
        img_processor=img_processor,
        bgr_frames=bgr_frames,
        prompts=prompts,
        manual_points=manual_points,
        device=device,
        dtype=dtype,
        conf_threshold=conf_threshold,
        prompt_frame_idx=args.frame,
    )

    # 저장
    save_video(annotated, output_path, fps, w, h)
    print(f"\n완료: {output_path}")


if __name__ == "__main__":
    main()
