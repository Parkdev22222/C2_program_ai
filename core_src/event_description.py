"""
이벤트 설명 생성 모듈 (경량 VLM)
SmolVLM2 / Idefics3 계열 모델 지원
transformers 버전에 따라 AutoModelForVision2Seq → AutoModelForCausalLM 순으로 시도
"""
import logging
import numpy as np
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

MILITARY_DESCRIPTION_PROMPT = (
    "You are a military reconnaissance analyst. Describe this battlefield scene briefly in English. "
    "Focus on: (1) military vehicles or personnel visible, (2) movement direction and speed, "
    "(3) terrain and environment, (4) any tactical significance. Be concise (2-3 sentences)."
)


def _load_vlm_model(model_name: str, device: str):
    """
    VLM 모델을 transformers 버전에 맞게 로드합니다.
    AutoModelForVision2Seq → Idefics3ForConditionalGeneration → AutoModelForCausalLM 순 시도.
    """
    from transformers import AutoProcessor

    processor = AutoProcessor.from_pretrained(model_name)

    # 1순위: AutoModelForVision2Seq (transformers < 4.48 등 구버전)
    try:
        from transformers import AutoModelForVision2Seq
        model = AutoModelForVision2Seq.from_pretrained(
            model_name, torch_dtype="auto", device_map=device
        )
        logger.info(f"Loaded {model_name} via AutoModelForVision2Seq")
        return processor, model
    except (ImportError, AttributeError):
        pass
    except Exception as e:
        logger.debug(f"AutoModelForVision2Seq failed: {e}")

    # 2순위: Idefics3ForConditionalGeneration (SmolVLM/SmolVLM2 전용)
    try:
        from transformers import Idefics3ForConditionalGeneration
        model = Idefics3ForConditionalGeneration.from_pretrained(
            model_name, torch_dtype="auto", device_map=device
        )
        logger.info(f"Loaded {model_name} via Idefics3ForConditionalGeneration")
        return processor, model
    except (ImportError, AttributeError):
        pass
    except Exception as e:
        logger.debug(f"Idefics3ForConditionalGeneration failed: {e}")

    # 3순위: AutoModelForCausalLM (최신 transformers)
    from transformers import AutoModelForCausalLM
    model = AutoModelForCausalLM.from_pretrained(
        model_name, torch_dtype="auto", device_map=device
    )
    logger.info(f"Loaded {model_name} via AutoModelForCausalLM")
    return processor, model


class EventDescriptionGenerator:
    def __init__(self, config: dict):
        self.config = config
        self.device = config.get("device", "cuda")
        self.temperature = config.get("temperature", 0.7)
        self.max_new_tokens = config.get("max_new_tokens", 512)
        self.batch_size = config.get("batch_size", 4)
        self._model = None
        self._processor = None
        self._load_model()

    def _load_model(self):
        model_name = self.config.get("model_name", "HuggingFaceTB/SmolVLM2-2.2B-Instruct")
        logger.info(f"Loading event description model: {model_name}")
        try:
            self._processor, self._model = _load_vlm_model(model_name, self.device)
            logger.info("Event description model loaded successfully")
        except Exception as e:
            logger.warning(f"Failed to load description model: {e}. Using dummy generator.")
            self._model = None

    def describe_frame(self, frame: np.ndarray, detections: List[Dict] = None) -> str:
        if self._model is None:
            return self._dummy_description(detections)
        try:
            return self._run_inference(frame)
        except Exception as e:
            logger.error(f"Description generation error: {e}")
            return self._dummy_description(detections)

    def _run_inference(self, frame: np.ndarray) -> str:
        import torch
        from PIL import Image

        image = Image.fromarray(frame)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": MILITARY_DESCRIPTION_PROMPT},
                ],
            }
        ]

        prompt = self._processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = self._processor(text=prompt, images=[image], return_tensors="pt")
        inputs = {k: v.to(self._model.device) for k, v in inputs.items()}

        with torch.no_grad():
            output = self._model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                temperature=self.temperature,
                do_sample=self.temperature > 0,
            )
        generated = output[0][inputs["input_ids"].shape[1]:]
        return self._processor.decode(generated, skip_special_tokens=True).strip()

    def _dummy_description(self, detections: List[Dict] = None) -> str:
        if not detections:
            return "No significant military activity detected in this segment."
        classes = [d.get("class_name", "unknown") for d in detections[:3]]
        return f"Detected military assets: {', '.join(classes)}. Further analysis required."

    def describe_segment(self, key_frame: np.ndarray, detections: List[Dict] = None) -> str:
        return self.describe_frame(key_frame, detections)
