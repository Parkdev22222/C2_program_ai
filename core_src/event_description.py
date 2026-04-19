"""
이벤트 설명 생성 모듈 (경량 VLM 사용)
군사 영상의 각 세그먼트에 대한 자연어 설명 생성
"""
import logging
import numpy as np
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

MILITARY_DESCRIPTION_PROMPT = (
    "You are a military reconnaissance analyst. Describe this battlefield scene briefly in English. "
    "Focus on: (1) military vehicles or personnel visible, (2) movement direction and speed, "
    "(3) terrain and environment, (4) any tactical significance. Be concise (2-3 sentences)."
)


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
        try:
            import torch
            from transformers import AutoProcessor, AutoModelForVision2Seq

            model_name = self.config.get("model_name", "HuggingFaceTB/SmolVLM2-2.2B-Instruct")
            logger.info(f"Loading event description model: {model_name}")
            self._processor = AutoProcessor.from_pretrained(model_name)
            self._model = AutoModelForVision2Seq.from_pretrained(
                model_name,
                torch_dtype="auto",
                device_map=self.device,
            )
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

        import torch
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
