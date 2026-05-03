#!/usr/bin/env python3
"""
SAM3 MP4 영상 객체 탐지 및 추적 스크립트

사용법:
  # 텍스트 프롬프트 (sam3 image model로 탐지 → 트래커에 전달)
  python sam3_track_video.py --video input.mp4 --prompt "soldier"
  python sam3_track_video.py --video input.mp4 --prompt "soldier" --prompt "tank"

  # 수동 클릭 포인트 (좌표 직접 지정)
  python sam3_track_video.py --video input.mp4 --point 640,360

  # models_config.yaml의 target_classes 자동 사용
  python sam3_track_video.py --video samples/sample.mp4 --use-config
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

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*bias type.*")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────────────────────────────────

COLORS_BGR = [
    (0, 230, 0),    (0, 0, 230),    (230, 0, 0),
    (0, 230, 230),  (230, 0, 230),  (0, 165, 255),
    (128, 0, 255),  (0, 255, 128),  (255, 128, 0),
    (0, 128, 255),
]

def get_color(idx: int) -> Tuple[int, int, int]:
    return COLORS_BGR[idx % len(COLORS_BGR)]


# ──────────────────────────────────────────────────────────────────────────────
# 설정 로딩
# ──────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        import yaml
        path = Path(__file__).parent / "config" / "models_config.yaml"
        cfg = yaml.safe_load(path.read_text())["object_detection"]
        return {
            "sam3_path":       cfg.get("sam3_path", ""),
            "checkpoint_path": cfg.get("checkpoint_path", ""),
            "target_classes":  cfg.get("target_classes", []),
            "conf_threshold":  cfg.get("confidence_threshold", 0.01),
        }
    except Exception:
        return {}


# ──────────────────────────────────────────────────────────────────────────────
# 모델 로딩
# ──────────────────────────────────────────────────────────────────────────────

def load_tracker(hf_model_id: str, device: torch.device, dtype: torch.dtype):
    """HuggingFace Sam3TrackerVideoModel + Sam3TrackerVideoProcessor."""
    from transformers import Sam3TrackerVideoModel, Sam3TrackerVideoProcessor
    log.info(f"SAM3 tracker 모델 로딩: {hf_model_id}")
    model = Sam3TrackerVideoModel.from_pretrained(hf_model_id).to(device, dtype=dtype)
    model.eval()
    proc = Sam3TrackerVideoProcessor.from_pretrained(hf_model_id)
    log.info("SAM3 tracker 로딩 완료")
    return model, proc


def load_image_detector(sam3_path: str, ckpt: str, device: torch.device):
    """텍스트 프롬프트 초기 탐지용 sam3 패키지 이미지 모델."""
    if sam3_path and sam3_path not in sys.path:
        sys.path.insert(0, sam3_path)
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor
    log.info(f"SAM3 image detector 로딩: {ckpt}")
    m = build_sam3_image_model(checkpoint_path=ckpt)
    m = m.to(device=device, dtype=torch.float32).eval()
    log.info("SAM3 image detector 로딩 완료")
    return Sam3Processor(m)


# ──────────────────────────────────────────────────────────────────────────────
# 비디오 I/O
# ──────────────────────────────────────────────────────────────────────────────

def read_video(path: str) -> Tuple[List[np.ndarray], float, int, int]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(f"영상을 열 수 없습니다: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    log.info(f"영상: {w}×{h}  {fps:.1f}fps  {total}프레임")
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    return frames, fps, w, h


def write_video(frames: List[np.ndarray], path: str, fps: float, w: int, h: int):
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in frames:
        out.write(f)
    out.release()
    log.info(f"결과 영상 저장: {path}  ({len(frames)}프레임)")


# ──────────────────────────────────────────────────────────────────────────────
# 텍스트 → 포인트 변환 (sam3 image model 사용)
# ──────────────────────────────────────────────────────────────────────────────

def text_to_points(
    img_proc,
    frame_bgr: np.ndarray,
    class_name: str,
    conf_thr: float,
) -> List[Tuple[int, int]]:
    """
    sam3 image model로 class_name 탐지 → 각 객체의 중심 좌표 반환.
    탐지 실패 시 빈 리스트 반환.
    """
    h, w = frame_bgr.shape[:2]
    pil = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))

    with torch.no_grad():
        state = img_proc.set_image(pil)
        out   = img_proc.set_text_prompt(prompt=class_name, state=state)
    del state
    gc.collect()

    def _np(v):
        if v is None: return np.array([])
        return v.cpu().numpy() if hasattr(v, "cpu") else np.asarray(v)

    masks_np  = _np(out.get("masks"))
    boxes_np  = _np(out.get("boxes"))
    scores_np = _np(out.get("scores")).flatten()

    points = []
    for i, score in enumerate(scores_np):
        if float(score) < conf_thr:
            continue
        # 마스크 중심
        if masks_np.ndim >= 3 and i < len(masks_np):
            mask = np.squeeze(masks_np[i]).astype(bool)
            if mask.shape != (h, w):
                mask = np.array(
                    Image.fromarray(mask.astype(np.uint8) * 255, "L").resize((w, h), Image.NEAREST)
                ) > 127
            ys, xs = np.where(mask)
            if len(xs):
                points.append((int(xs.mean()), int(ys.mean())))
        # 박스 중심
        elif i < len(boxes_np):
            x1, y1, x2, y2 = boxes_np[i].tolist()
            if max(x2, y2) <= 1.0:
                x1, y1, x2, y2 = x1*w, y1*h, x2*w, y2*h
            points.append((int((x1+x2)/2), int((y1+y2)/2)))

    log.info(f"  '{class_name}': {len(points)}개 탐지")
    return points


# ──────────────────────────────────────────────────────────────────────────────
# 마스크 후처리
# ──────────────────────────────────────────────────────────────────────────────

def to_bool_mask(tensor: torch.Tensor, h: int, w: int) -> np.ndarray:
    m = tensor.squeeze()
    if m.dtype != torch.bool:
        m = m > 0.0
    m = m.cpu().numpy().astype(bool)
    if m.shape != (h, w):
        m = np.array(
            Image.fromarray(m.astype(np.uint8) * 255, "L").resize((w, h), Image.NEAREST)
        ) > 127
    return m


# ──────────────────────────────────────────────────────────────────────────────
# 프레임 렌더링
# ──────────────────────────────────────────────────────────────────────────────

def render_frame(
    frame: np.ndarray,
    segments: Dict[int, np.ndarray],          # obj_id → mask
    id_to_label: Dict[int, str],
    alpha: float = 0.45,
) -> np.ndarray:
    out = frame.copy()

    for obj_id, mask in segments.items():
        if mask is None or not mask.any():
            continue
        color = get_color(obj_id - 1)
        label = id_to_label.get(obj_id, f"obj{obj_id}")

        # 마스크 오버레이
        overlay = out.copy()
        overlay[mask] = color
        out = cv2.addWeighted(overlay, alpha, out, 1 - alpha, 0)

        # 바운딩 박스
        ys, xs = np.where(mask)
        x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)

        # 라벨
        (tw, th), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(out, (x1, y1 - th - bl - 5), (x1 + tw + 4, y1), color, -1)
        cv2.putText(out, label, (x1 + 2, y1 - bl - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    return out


# ──────────────────────────────────────────────────────────────────────────────
# 핵심 로직
# ──────────────────────────────────────────────────────────────────────────────

def track(
    tracker_model,
    tracker_proc,
    img_detector,                      # None이면 포인트 모드만
    bgr_frames: List[np.ndarray],
    text_prompts: List[str],
    manual_points: List[Tuple[int, int]],
    conf_thr: float,
    prompt_frame: int,
    device: torch.device,
    dtype: torch.dtype,
) -> List[np.ndarray]:

    h, w = bgr_frames[0].shape[:2]
    pil_frames = [
        Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
        for f in bgr_frames
    ]

    # ── 1. 비디오 세션 초기화 ─────────────────────────────────────
    log.info("비디오 세션 초기화 중...")
    session = tracker_proc.init_video_session(
        video=pil_frames,
        inference_device=device,
        dtype=dtype,
    )
    log.info(f"세션 초기화 완료  ({len(pil_frames)}프레임  {w}×{h})")

    id_to_label: Dict[int, str] = {}
    next_id = 1

    # ── 2. 텍스트 프롬프트 → 포인트 → 세션 등록 ──────────────────
    if img_detector is not None:
        ref_frame = bgr_frames[prompt_frame]
        for class_name in text_prompts:
            pts = text_to_points(img_detector, ref_frame, class_name, conf_thr)
            if not pts:
                log.warning(f"'{class_name}' frame {prompt_frame}에서 탐지 안됨 — 건너뜀")
                continue
            for cx, cy in pts:
                log.info(f"  등록: '{class_name}'  obj_id={next_id}  point=({cx},{cy})")
                tracker_proc.add_inputs_to_inference_session(
                    inference_session=session,
                    frame_idx=prompt_frame,
                    obj_ids=next_id,
                    input_points=[[[[cx, cy]]]],
                    input_labels=[[[1]]],
                )
                id_to_label[next_id] = class_name
                next_id += 1

    # ── 3. 수동 포인트 등록 ───────────────────────────────────────
    for cx, cy in manual_points:
        label = f"obj{next_id}"
        log.info(f"  등록: 수동 포인트  obj_id={next_id}  point=({cx},{cy})")
        tracker_proc.add_inputs_to_inference_session(
            inference_session=session,
            frame_idx=prompt_frame,
            obj_ids=next_id,
            input_points=[[[[cx, cy]]]],
            input_labels=[[[1]]],
        )
        id_to_label[next_id] = label
        next_id += 1

    if next_id == 1:
        log.error("등록된 객체 없음 — 원본 영상을 저장합니다.")
        return bgr_frames[:]

    log.info(f"총 {next_id - 1}개 객체 등록 완료")

    # ── 4. 전체 영상 전파 (propagate_in_video_iterator) ───────────
    log.info("전체 비디오 추적 시작...")
    video_segments: Dict[int, Dict[int, np.ndarray]] = {}  # frame_idx → {obj_id: mask}

    for output in tracker_model.propagate_in_video_iterator(session):
        fidx = output.frame_idx

        # post_process_masks: binarize=True → bool 마스크
        masks_pp = tracker_proc.post_process_masks(
            [output.pred_masks],
            original_sizes=[[session.video_height, session.video_width]],
            binarize=True,
        )[0]  # (num_objects, 1, H, W)

        obj_ids = (
            output.obj_ids.tolist()
            if hasattr(output, "obj_ids") and output.obj_ids is not None
            else list(id_to_label.keys())
        )

        seg = {}
        for oi, oid in enumerate(obj_ids):
            if oi < len(masks_pp):
                seg[int(oid)] = to_bool_mask(masks_pp[oi], h, w)
        video_segments[fidx] = seg

        if fidx % 60 == 0:
            log.info(f"  추적 진행: {fidx}/{len(bgr_frames)} 프레임")

    log.info(f"추적 완료: {len(video_segments)}프레임 처리됨")

    # ── 5. 결과 렌더링 ────────────────────────────────────────────
    annotated = [f.copy() for f in bgr_frames]
    for fidx, seg in video_segments.items():
        if fidx >= len(annotated):
            continue
        annotated[fidx] = render_frame(annotated[fidx], seg, id_to_label)

    # 프레임 카운터 오버레이
    total = len(annotated)
    for i, f in enumerate(annotated):
        cv2.putText(f, f"{i+1}/{total}", (8, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220, 220, 220), 1, cv2.LINE_AA)

    return annotated


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def build_args():
    p = argparse.ArgumentParser(
        description="SAM3 비디오 객체 탐지 및 추적",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--video",      required=True, help="입력 영상 경로 (.mp4 등)")
    p.add_argument("--output",     default="",    help="출력 경로 (기본: <입력>_tracked.mp4)")
    p.add_argument("--prompt",     action="append", dest="prompts", default=[],
                   metavar="TEXT", help="탐지 텍스트 (반복 가능, 예: --prompt soldier)")
    p.add_argument("--point",      action="append", dest="points",  default=[],
                   metavar="X,Y",  help="수동 포인트 (예: --point 640,360)")
    p.add_argument("--use-config", action="store_true",
                   help="models_config.yaml의 target_classes 사용")
    p.add_argument("--frame",      type=int, default=0,
                   help="프롬프트 기준 프레임 번호 (기본: 0)")
    p.add_argument("--conf",       type=float, default=None,
                   help="탐지 신뢰도 임계값 (기본: config 또는 0.01)")
    p.add_argument("--hf-model",   default="facebook/sam3",
                   help="HuggingFace 모델 ID (기본: facebook/sam3)")
    p.add_argument("--sam3-path",  default="",
                   help="sam3 레포 경로 (텍스트 탐지용, config에서 자동 로딩 가능)")
    p.add_argument("--checkpoint", default="",
                   help="sam3 가중치 경로 (텍스트 탐지용, config에서 자동 로딩 가능)")
    p.add_argument("--dtype",      default="bfloat16",
                   choices=["bfloat16", "float16", "float32"])
    return p.parse_args()


def main():
    args = build_args()
    cfg  = load_config()

    # 장치 / dtype
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dtype  = {"bfloat16": torch.bfloat16, "float16": torch.float16,
               "float32": torch.float32}[args.dtype]
    log.info(f"device={device}  dtype={args.dtype}")

    # 프롬프트 결정
    prompts = list(args.prompts)
    if args.use_config or (not prompts and not args.points):
        prompts = cfg.get("target_classes", []) or prompts

    # 수동 포인트 파싱
    manual_points: List[Tuple[int, int]] = []
    for raw in args.points:
        try:
            x, y = raw.split(",")
            manual_points.append((int(x), int(y)))
        except Exception:
            raise SystemExit(f"[오류] --point 형식 잘못됨: '{raw}'  →  예: --point 640,360")

    if not prompts and not manual_points:
        raise SystemExit("[오류] --prompt / --point / --use-config 중 하나를 지정하세요.")

    conf_thr = args.conf if args.conf is not None else cfg.get("conf_threshold", 0.01)

    # 출력 경로
    vp = Path(args.video)
    output_path = args.output or str(vp.parent / f"{vp.stem}_tracked.mp4")
    log.info(f"입력: {vp}")
    log.info(f"출력: {output_path}")
    log.info(f"프롬프트: {prompts}  포인트: {manual_points}  conf: {conf_thr}")

    # 모델 로딩
    tracker_model, tracker_proc = load_tracker(args.hf_model, device, dtype)

    img_detector = None
    if prompts:
        sp = args.sam3_path or cfg.get("sam3_path", "")
        ck = args.checkpoint or cfg.get("checkpoint_path", "")
        if sp and ck:
            try:
                img_detector = load_image_detector(sp, ck, device)
            except Exception as e:
                log.warning(f"image detector 로딩 실패: {e}")
        else:
            log.warning("sam3_path / checkpoint 미설정 → 텍스트 탐지 불가")

    # 영상 로딩
    bgr_frames, fps, w, h = read_video(str(vp))

    # 탐지 + 추적
    result_frames = track(
        tracker_model=tracker_model,
        tracker_proc=tracker_proc,
        img_detector=img_detector,
        bgr_frames=bgr_frames,
        text_prompts=prompts,
        manual_points=manual_points,
        conf_thr=conf_thr,
        prompt_frame=min(args.frame, len(bgr_frames) - 1),
        device=device,
        dtype=dtype,
    )

    # 저장
    write_video(result_frames, output_path, fps, w, h)
    print(f"\n결과 저장 완료: {output_path}")


if __name__ == "__main__":
    main()
