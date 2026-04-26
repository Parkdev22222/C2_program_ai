#!/usr/bin/env python3
"""
SAM3 비디오 객체 탐지 및 추적 스크립트

사용 예시:
  python sam3_track_video.py --video input.mp4 --prompt "soldier"
  python sam3_track_video.py --video input.mp4 --prompt "soldier" --prompt "tank" --output result.mp4
  python sam3_track_video.py --video input.mp4 --prompt "soldier" --step 5 --frame 0
  python sam3_track_video.py --video samples/sample.mp4 --use-config   # models_config.yaml 클래스 사용
"""
import argparse
import gc
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# 색상 팔레트 (클래스별 고정 색상)
# ─────────────────────────────────────────────────────────────────
_COLORS = [
    (0, 255, 0),    # green
    (0, 0, 255),    # red (BGR)
    (255, 0, 0),    # blue (BGR)
    (0, 255, 255),  # yellow
    (255, 0, 255),  # magenta
    (255, 165, 0),  # orange
    (0, 128, 255),  # deep orange
    (128, 0, 255),  # purple
    (0, 255, 128),  # spring green
    (255, 128, 0),  # sky blue
]

def _class_color(class_name: str, class_list: List[str]) -> Tuple[int, int, int]:
    try:
        idx = class_list.index(class_name)
    except ValueError:
        idx = hash(class_name) % len(_COLORS)
    return _COLORS[idx % len(_COLORS)]


# ─────────────────────────────────────────────────────────────────
# SAM3 로딩
# ─────────────────────────────────────────────────────────────────

def load_predictor(sam3_path: str, checkpoint_path: str):
    if sam3_path and sam3_path not in sys.path:
        sys.path.insert(0, sam3_path)
        logger.info(f"SAM3 path: {sam3_path}")

    from sam3.model_builder import build_sam3_video_predictor
    logger.info(f"SAM3 video predictor 로딩: {checkpoint_path}")
    predictor = build_sam3_video_predictor(checkpoint_path=checkpoint_path)
    logger.info("SAM3 video predictor 로딩 완료")
    return predictor


# ─────────────────────────────────────────────────────────────────
# 비디오 로딩 / 저장
# ─────────────────────────────────────────────────────────────────

def load_video_frames(video_path: str) -> Tuple[List[np.ndarray], float, int, int]:
    """MP4 → (frames, fps, width, height)"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"영상을 열 수 없습니다: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    logger.info(f"영상 정보: {w}×{h}, {fps:.1f}fps, {total}프레임")

    frames = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame)  # BGR numpy array
    cap.release()
    logger.info(f"프레임 로딩 완료: {len(frames)}개")
    return frames, fps, w, h


def save_video(frames: List[np.ndarray], output_path: str, fps: float, w: int, h: int):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
    for frame in frames:
        writer.write(frame)
    writer.release()
    logger.info(f"영상 저장 완료: {output_path}")


def frames_to_jpeg_dir(frames: List[np.ndarray], tmp_dir: str):
    """OpenCV BGR frames → JPEG 파일로 저장 (SAM3 input 형식)"""
    from PIL import Image
    for i, frame in enumerate(frames):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        Image.fromarray(rgb).save(
            os.path.join(tmp_dir, f"{i:05d}.jpg"),
            format="JPEG", quality=95,
        )


# ─────────────────────────────────────────────────────────────────
# SAM3 output 파싱
# ─────────────────────────────────────────────────────────────────

def parse_output(output: dict, conf_threshold: float, orig_h: int, orig_w: int):
    """SAM3 output dict → (masks, boxes, scores) numpy arrays."""
    def _to_np(v):
        if v is None:
            return np.array([])
        if hasattr(v, "cpu"):
            return v.cpu().numpy()
        return np.asarray(v)

    masks_raw  = _to_np(output.get("masks",        []))
    boxes_raw  = _to_np(output.get("boxes",        []))
    scores_raw = _to_np(output.get("scores",       [])).flatten()
    logits_raw = _to_np(output.get("masks_logits", []))

    # scores 없으면 logits에서 추출
    if len(scores_raw) == 0 and logits_raw.ndim >= 3 and logits_raw.shape[0] > 0:
        import torch
        probs = torch.sigmoid(torch.as_tensor(logits_raw).float())
        scores_raw = (probs.reshape(probs.shape[0], -1) > 0.5).float().mean(dim=1).numpy()

    masks_out, boxes_out, scores_out = [], [], []
    for i, score in enumerate(scores_raw):
        if float(score) < conf_threshold:
            continue

        # 마스크 처리
        mask_np = None
        if masks_raw.ndim >= 3 and i < len(masks_raw):
            raw = np.squeeze(masks_raw[i]).astype(bool)
            if raw.shape != (orig_h, orig_w):
                from PIL import Image as PILImage
                pm = PILImage.fromarray(raw.astype(np.uint8) * 255, "L")
                raw = np.array(pm.resize((orig_w, orig_h), PILImage.NEAREST)) > 127
            mask_np = raw
        elif logits_raw.ndim >= 3 and i < len(logits_raw):
            raw_l = np.squeeze(logits_raw[i])
            raw = (1 / (1 + np.exp(-raw_l.astype(np.float32)))) > 0.5
            if raw.shape != (orig_h, orig_w):
                from PIL import Image as PILImage
                pm = PILImage.fromarray(raw.astype(np.uint8) * 255, "L")
                raw = np.array(pm.resize((orig_w, orig_h), PILImage.NEAREST)) > 127
            mask_np = raw

        # bbox (픽셀 좌표)
        box_px = None
        if mask_np is not None and mask_np.any():
            rows = np.where(np.any(mask_np, axis=1))[0]
            cols = np.where(np.any(mask_np, axis=0))[0]
            box_px = (int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1]))
        elif i < len(boxes_raw):
            x1, y1, x2, y2 = boxes_raw[i].tolist()
            # boxes가 정규화(0~1)인지 픽셀인지 판별
            if max(x2, y2) <= 1.0:
                box_px = (int(x1 * orig_w), int(y1 * orig_h), int(x2 * orig_w), int(y2 * orig_h))
            else:
                box_px = (int(x1), int(y1), int(x2), int(y2))

        if box_px is not None:
            masks_out.append(mask_np)
            boxes_out.append(box_px)
            scores_out.append(float(score))

    return masks_out, boxes_out, scores_out


# ─────────────────────────────────────────────────────────────────
# 프레임 주석 렌더링
# ─────────────────────────────────────────────────────────────────

def draw_detections(
    frame: np.ndarray,
    class_name: str,
    masks: list,
    boxes: list,
    scores: list,
    color: Tuple[int, int, int],
    alpha: float = 0.4,
) -> np.ndarray:
    """마스크 오버레이 + bbox + 라벨을 프레임에 그립니다."""
    result = frame.copy()

    for mask, box, score in zip(masks, boxes, scores):
        # 마스크 오버레이
        if mask is not None and mask.any():
            overlay = result.copy()
            overlay[mask] = color
            result = cv2.addWeighted(overlay, alpha, result, 1 - alpha, 0)

        # 바운딩 박스
        if box is not None:
            x1, y1, x2, y2 = box
            cv2.rectangle(result, (x1, y1), (x2, y2), color, 2)

            # 라벨 배경
            label = f"{class_name} {score:.2f}"
            (lw, lh), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(result, (x1, y1 - lh - baseline - 4), (x1 + lw, y1), color, -1)
            cv2.putText(
                result, label, (x1, y1 - baseline - 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA,
            )

    return result


def draw_frame_info(frame: np.ndarray, frame_idx: int, total: int) -> np.ndarray:
    label = f"Frame {frame_idx + 1}/{total}"
    cv2.putText(frame, label, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
    return frame


# ─────────────────────────────────────────────────────────────────
# 핵심 트래킹 로직
# ─────────────────────────────────────────────────────────────────

def track_video(
    predictor,
    frames: List[np.ndarray],
    prompts: List[str],
    conf_threshold: float = 0.01,
    prompt_frame_index: int = 0,
    step: int = 1,
) -> List[np.ndarray]:
    """
    SAM3 video predictor로 객체 탐지 및 추적 수행.

    1. 프레임을 JPEG 디렉토리로 저장
    2. start_session(resource_path=jpeg_dir)
    3. 각 클래스마다 add_prompt(frame_index=prompt_frame_index, text=class_name)
    4. step 간격으로 나머지 프레임도 add_prompt 호출 (SAM3는 propagate_in_video 미지원)
    5. 결과를 프레임에 렌더링
    """
    h, w = frames[0].shape[:2]
    annotated = [f.copy() for f in frames]

    with tempfile.TemporaryDirectory() as tmp_dir:
        logger.info(f"{len(frames)}개 프레임 저장 중 → {tmp_dir}")
        frames_to_jpeg_dir(frames, tmp_dir)

        # 세션 시작
        resp = predictor.handle_request(
            request=dict(type="start_session", resource_path=tmp_dir)
        )
        session_id = resp["session_id"]
        logger.info(f"세션 시작: {session_id}")

        try:
            # 탐지할 프레임 인덱스 목록 (prompt_frame_index 포함)
            detect_indices = sorted(set(
                [prompt_frame_index] +
                list(range(0, len(frames), max(1, step)))
            ))

            for fidx in detect_indices:
                if fidx >= len(frames):
                    continue

                for class_name in prompts:
                    gc.collect()
                    try:
                        add_resp = predictor.handle_request(
                            request=dict(
                                type="add_prompt",
                                session_id=session_id,
                                frame_index=fidx,
                                text=class_name,
                            )
                        )
                        output = add_resp.get("outputs", {})
                        masks, boxes, scores = parse_output(output, conf_threshold, h, w)

                        if masks or boxes:
                            color = _class_color(class_name, prompts)
                            annotated[fidx] = draw_detections(
                                annotated[fidx], class_name, masks, boxes, scores, color
                            )
                            logger.info(
                                f"Frame {fidx:4d} | {class_name}: {len(boxes)}개 탐지 "
                                f"(scores: {[f'{s:.3f}' for s in scores]})"
                            )
                        else:
                            logger.debug(f"Frame {fidx:4d} | {class_name}: 탐지 없음")

                    except Exception as e:
                        logger.warning(f"Frame {fidx} add_prompt '{class_name}': {e}")

        finally:
            try:
                predictor.handle_request(
                    request=dict(type="close_session", session_id=session_id)
                )
                logger.info(f"세션 종료: {session_id}")
            except Exception:
                pass

    # 프레임 번호 표시
    for i in range(len(annotated)):
        draw_frame_info(annotated[i], i, len(frames))

    return annotated


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def load_config_defaults() -> dict:
    """models_config.yaml에서 SAM3 기본값 로딩."""
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SAM3 비디오 객체 탐지 및 추적",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--video",      required=True, help="입력 영상 경로 (.mp4 / .avi / ...)")
    p.add_argument("--output",     default="",    help="출력 영상 경로 (미입력 시 자동 생성)")
    p.add_argument("--prompt",     action="append", dest="prompts", default=[],
                   metavar="TEXT", help="탐지할 객체 텍스트 (반복 가능: --prompt soldier --prompt tank)")
    p.add_argument("--use-config", action="store_true",
                   help="models_config.yaml의 target_classes를 프롬프트로 사용")
    p.add_argument("--sam3-path",  default="", help="SAM3 레포 경로 (미입력 시 config 사용)")
    p.add_argument("--checkpoint", default="", help="SAM3 가중치 경로 (미입력 시 config 사용)")
    p.add_argument("--conf",       type=float, default=None, help="confidence threshold (기본 0.01)")
    p.add_argument("--step",       type=int,   default=1,
                   help="탐지 프레임 간격 (1=전체, 5=5프레임마다, 기본 1)")
    p.add_argument("--frame",      type=int,   default=0,
                   help="주 프롬프트 프레임 인덱스 (기본 0)")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    cfg = load_config_defaults()

    sam3_path       = args.sam3_path  or cfg.get("sam3_path", "")
    checkpoint_path = args.checkpoint or cfg.get("checkpoint_path", "")
    conf_threshold  = args.conf       if args.conf is not None else cfg.get("conf_threshold", 0.01)

    # 프롬프트 결정
    prompts = args.prompts[:]
    if args.use_config or not prompts:
        prompts = cfg.get("target_classes", []) or prompts
    if not prompts:
        parser.error("--prompt 또는 --use-config 로 탐지할 객체를 지정하세요.")

    # 출력 경로 자동 생성
    video_path = Path(args.video)
    output_path = args.output or str(video_path.parent / f"{video_path.stem}_tracked.mp4")

    logger.info(f"입력: {video_path}")
    logger.info(f"출력: {output_path}")
    logger.info(f"프롬프트: {prompts}")
    logger.info(f"confidence threshold: {conf_threshold}")
    logger.info(f"탐지 간격: {args.step}프레임마다")

    # 모델 로딩
    predictor = load_predictor(sam3_path, checkpoint_path)

    # 영상 로딩
    frames, fps, w, h = load_video_frames(str(video_path))

    # 탐지 + 추적
    annotated = track_video(
        predictor=predictor,
        frames=frames,
        prompts=prompts,
        conf_threshold=conf_threshold,
        prompt_frame_index=args.frame,
        step=args.step,
    )

    # 결과 저장
    save_video(annotated, output_path, fps, w, h)
    print(f"\n완료: {output_path}")


if __name__ == "__main__":
    main()
