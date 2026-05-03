#!/usr/bin/env python3
"""
SAM3 MP4 영상 객체 탐지 및 추적 스크립트

사용법:
  python sam3_track_video.py --video input.mp4 --text "person"
  python sam3_track_video.py --video input.mp4 --text "soldier" --text "tank"
  python sam3_track_video.py --video input.mp4 --point 640,360
  python sam3_track_video.py --video samples/sample.mp4 --use-config
  python sam3_track_video.py --video input.mp4 --text "car" --output result.mp4
"""

import argparse
import logging
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
from PIL import Image

warnings.filterwarnings("ignore", category=UserWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ── 색상 팔레트 (BGR) ────────────────────────────────────────────────────────
COLORS_BGR = [
    (0, 230, 0),   (0, 0, 230),   (230, 0, 0),
    (0, 230, 230), (230, 0, 230), (0, 165, 255),
    (128, 0, 255), (0, 255, 128), (255, 128, 0),
    (0, 128, 255),
]


def get_color(idx: int) -> Tuple[int, int, int]:
    return COLORS_BGR[int(idx) % len(COLORS_BGR)]


# ── 설정 로딩 ────────────────────────────────────────────────────────────────
def load_config() -> dict:
    try:
        import yaml
        path = Path(__file__).parent / "config" / "models_config.yaml"
        cfg = yaml.safe_load(path.read_text())["object_detection"]
        return {
            "target_classes": cfg.get("target_classes", []),
            "conf_threshold": cfg.get("confidence_threshold", 0.01),
        }
    except Exception:
        return {}


# ── 모델 로딩 ────────────────────────────────────────────────────────────────
def load_model(hf_model_id: str, device: torch.device, dtype: torch.dtype):
    from transformers import Sam3VideoModel, Sam3VideoProcessor

    log.info(f"SAM3 모델 로딩: {hf_model_id}")
    model = Sam3VideoModel.from_pretrained(hf_model_id).to(device, dtype=dtype)
    model.eval()
    processor = Sam3VideoProcessor.from_pretrained(hf_model_id)
    log.info("SAM3 모델 로딩 완료")
    return model, processor


# ── 비디오 로딩 (로컬 MP4 → PIL 프레임 리스트) ───────────────────────────────
def load_local_video(path: str) -> Tuple[List[Image.Image], float, int, int]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(f"영상을 열 수 없습니다: {path}")

    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    log.info(f"영상: {w}×{h}  {fps:.1f}fps  {total}프레임")

    pil_frames: List[Image.Image] = []
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        pil_frames.append(Image.fromarray(rgb))

    cap.release()
    log.info(f"프레임 로딩 완료: {len(pil_frames)}프레임")
    return pil_frames, fps, w, h


# ── 결과 영상 저장 ────────────────────────────────────────────────────────────
def write_video(frames: List[np.ndarray], path: str, fps: float, w: int, h: int) -> None:
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in frames:
        out.write(f)
    out.release()
    log.info(f"결과 영상 저장: {path}  ({len(frames)}프레임)")


# ── 프레임 렌더링 ─────────────────────────────────────────────────────────────
def render_frame(
    pil_frame: Image.Image,
    frame_outputs: dict,
    id_to_label: Dict[int, str],
    conf_thr: float,
    alpha: float = 0.45,
) -> np.ndarray:
    """
    frame_outputs 키:
      object_ids : Tensor[N]
      scores     : Tensor[N]
      boxes      : Tensor[N, 4]  XYXY 절대 좌표
      masks      : Tensor[N, H, W] 또는 Tensor[N, 1, H, W]
    """
    bgr = cv2.cvtColor(np.array(pil_frame), cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]

    obj_ids = frame_outputs.get("object_ids")
    scores  = frame_outputs.get("scores")
    boxes   = frame_outputs.get("boxes")
    masks   = frame_outputs.get("masks")

    if obj_ids is None:
        return bgr

    obj_ids_list = obj_ids.tolist() if hasattr(obj_ids, "tolist") else list(obj_ids)
    scores_list  = scores.tolist()  if (scores is not None and hasattr(scores, "tolist")) else []

    for i, obj_id in enumerate(obj_ids_list):
        score = scores_list[i] if i < len(scores_list) else 1.0
        if score < conf_thr:
            continue

        color = get_color(int(obj_id) - 1)
        label = f"{id_to_label.get(int(obj_id), f'obj{obj_id}')} {score:.2f}"

        # 마스크 오버레이
        if masks is not None and i < len(masks):
            mask_t = masks[i]
            if mask_t.dim() == 3:
                mask_t = mask_t.squeeze(0)
            mask_np = mask_t.cpu().numpy().astype(bool)
            if mask_np.shape != (h, w):
                mask_np = np.array(
                    Image.fromarray(mask_np.astype(np.uint8) * 255, "L")
                    .resize((w, h), Image.NEAREST)
                ) > 127
            overlay = bgr.copy()
            overlay[mask_np] = color
            bgr = cv2.addWeighted(overlay, alpha, bgr, 1 - alpha, 0)

        # 바운딩 박스 + 라벨
        if boxes is not None and i < len(boxes):
            x1, y1, x2, y2 = boxes[i].tolist()
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            cv2.rectangle(bgr, (x1, y1), (x2, y2), color, 2)
            (tw, th), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
            cv2.rectangle(bgr, (x1, y1 - th - bl - 5), (x1 + tw + 4, y1), color, -1)
            cv2.putText(
                bgr, label, (x1 + 2, y1 - bl - 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA,
            )

    return bgr


# ── 핵심 추적 로직 ────────────────────────────────────────────────────────────
def track(
    model,
    processor,
    pil_frames: List[Image.Image],
    text_prompts: List[str],
    manual_points: List[Tuple[int, int]],
    conf_thr: float,
    device: torch.device,
    dtype: torch.dtype,
    max_frames: Optional[int],
) -> Tuple[Dict[int, dict], Dict[int, str]]:
    """
    반환:
      outputs_per_frame : {frame_idx: postprocess 결과 dict}
      id_to_label       : {obj_id: 라벨 문자열}
    """
    # ── 비디오 세션 초기화 ─────────────────────────────────────────
    log.info("비디오 세션 초기화 중...")
    inference_session = processor.init_video_session(
        video=pil_frames,
        inference_device=device,
        processing_device="cpu",
        video_storage_device="cpu",
        dtype=dtype,
    )
    log.info(f"세션 초기화 완료  ({len(pil_frames)}프레임)")

    id_to_label: Dict[int, str] = {}

    # ── 텍스트 프롬프트 등록 ───────────────────────────────────────
    for text in text_prompts:
        log.info(f"텍스트 프롬프트 등록: '{text}'")
        inference_session = processor.add_text_prompt(
            inference_session=inference_session,
            text=text,
        )

    # ── 수동 포인트 등록 ───────────────────────────────────────────
    for idx, (cx, cy) in enumerate(manual_points):
        log.info(f"수동 포인트 등록: ({cx}, {cy})")
        try:
            inference_session = processor.add_point_prompt(
                inference_session=inference_session,
                point=[[cx, cy]],
                label=[1],
                frame_idx=0,
            )
        except AttributeError:
            # add_point_prompt가 없는 버전 대응
            inference_session = processor.add_inputs_to_inference_session(
                inference_session=inference_session,
                frame_idx=0,
                obj_ids=idx + 1,
                input_points=[[[[cx, cy]]]],
                input_labels=[[[1]]],
            )

    # ── 텍스트 기반 obj_id → 라벨 매핑 구성 ──────────────────────
    # 탐지 후 실제 obj_id는 propagate 결과에서 확인
    # 텍스트 순서대로 임시 매핑 (실제 obj_id는 렌더링 시 동적 처리)
    for text in text_prompts:
        for obj_id in range(1, 100):
            if obj_id not in id_to_label:
                id_to_label[obj_id] = text
                break

    for idx, _ in enumerate(manual_points):
        obj_id = len(text_prompts) + idx + 1
        id_to_label[obj_id] = f"obj{obj_id}"

    # ── 전체 영상 전파 ─────────────────────────────────────────────
    log.info("전체 비디오 추적 시작...")
    outputs_per_frame: Dict[int, dict] = {}

    propagate_kwargs = dict(inference_session=inference_session)
    if max_frames is not None:
        propagate_kwargs["max_frame_num_to_track"] = max_frames

    for model_outputs in model.propagate_in_video_iterator(**propagate_kwargs):
        frame_idx = model_outputs.frame_idx
        processed = processor.postprocess_outputs(inference_session, model_outputs)
        outputs_per_frame[frame_idx] = processed

        # postprocess 결과의 obj_id → 라벨 동적 갱신
        obj_ids = processed.get("object_ids")
        if obj_ids is not None:
            for oid in obj_ids.tolist():
                if int(oid) not in id_to_label:
                    id_to_label[int(oid)] = f"obj{oid}"

        if frame_idx % 60 == 0:
            n_obj = len(obj_ids) if obj_ids is not None else 0
            log.info(f"  추적 진행: {frame_idx}/{len(pil_frames)} 프레임  객체 {n_obj}개")

    log.info(f"추적 완료: {len(outputs_per_frame)}프레임 처리됨")
    return outputs_per_frame, id_to_label


# ── CLI ──────────────────────────────────────────────────────────────────────
def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SAM3 비디오 객체 탐지 및 추적",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--video",      required=True,
                   help="입력 영상 경로 (.mp4 등)")
    p.add_argument("--output",     default="",
                   help="출력 경로 (기본: <입력>_tracked.mp4)")
    p.add_argument("--text",       action="append", dest="texts", default=[],
                   metavar="TEXT", help="탐지 텍스트 프롬프트 (반복 가능, 예: --text soldier)")
    p.add_argument("--prompt",     action="append", dest="texts",
                   metavar="TEXT", help="--text의 별칭")
    p.add_argument("--point",      action="append", dest="points", default=[],
                   metavar="X,Y",  help="수동 포인트 (예: --point 640,360)")
    p.add_argument("--use-config", action="store_true",
                   help="models_config.yaml의 target_classes 사용")
    p.add_argument("--conf",       type=float, default=None,
                   help="표시 신뢰도 임계값 (기본: config 또는 0.01)")
    p.add_argument("--max-frames", type=int, default=None,
                   help="추적할 최대 프레임 수 (기본: 전체)")
    p.add_argument("--hf-model",   default="facebook/sam3",
                   help="HuggingFace 모델 ID (기본: facebook/sam3)")
    p.add_argument("--dtype",      default="bfloat16",
                   choices=["bfloat16", "float16", "float32"],
                   help="모델 추론 dtype (기본: bfloat16)")
    return p.parse_args()


def main() -> None:
    args = build_args()
    cfg  = load_config()

    # 장치 / dtype 설정
    try:
        from accelerate import Accelerator
        device = Accelerator().device
    except Exception:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16,
             "float32": torch.float32}[args.dtype]
    log.info(f"device={device}  dtype={args.dtype}")

    # 프롬프트 결정
    texts = list(args.texts or [])
    if args.use_config or (not texts and not args.points):
        texts = cfg.get("target_classes", []) or texts

    # 수동 포인트 파싱
    manual_points: List[Tuple[int, int]] = []
    for raw in (args.points or []):
        try:
            x, y = raw.split(",")
            manual_points.append((int(x), int(y)))
        except Exception:
            raise SystemExit(f"[오류] --point 형식 잘못됨: '{raw}'  →  예: --point 640,360")

    if not texts and not manual_points:
        raise SystemExit("[오류] --text / --point / --use-config 중 하나를 지정하세요.")

    conf_thr = args.conf if args.conf is not None else cfg.get("conf_threshold", 0.01)

    # 출력 경로 결정
    vp = Path(args.video)
    output_path = args.output or str(vp.parent / f"{vp.stem}_tracked.mp4")
    log.info(f"입력:   {vp}")
    log.info(f"출력:   {output_path}")
    log.info(f"텍스트: {texts}")
    log.info(f"포인트: {manual_points}")
    log.info(f"conf:   {conf_thr}")

    # 모델 로딩
    model, processor = load_model(args.hf_model, device, dtype)

    # 로컬 영상 로딩
    pil_frames, fps, w, h = load_local_video(str(vp))

    # 탐지 + 추적
    outputs_per_frame, id_to_label = track(
        model=model,
        processor=processor,
        pil_frames=pil_frames,
        text_prompts=texts,
        manual_points=manual_points,
        conf_thr=conf_thr,
        device=device,
        dtype=dtype,
        max_frames=args.max_frames,
    )

    log.info(f"탐지된 객체: {id_to_label}")

    # 결과 프레임 렌더링
    log.info("결과 프레임 렌더링 중...")
    annotated: List[np.ndarray] = []
    total = len(pil_frames)

    for fidx, pil_frame in enumerate(pil_frames):
        frame_outputs = outputs_per_frame.get(fidx, {})
        bgr = render_frame(pil_frame, frame_outputs, id_to_label, conf_thr)

        # 프레임 카운터 오버레이
        cv2.putText(
            bgr, f"{fidx + 1}/{total}", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220, 220, 220), 1, cv2.LINE_AA,
        )
        annotated.append(bgr)

    # 결과 영상 저장
    write_video(annotated, output_path, fps, w, h)
    print(f"\n결과 저장 완료: {output_path}")
    print(f"처리 프레임: {len(outputs_per_frame)}/{total}")
    print(f"탐지 객체:   {id_to_label}")


if __name__ == "__main__":
    main()
