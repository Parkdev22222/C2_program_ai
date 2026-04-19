"""
이미지 임베딩 생성 모듈
MobileCLIP-S2: open_clip 사용 / 일반 CLIP: transformers 사용
"""
import logging
import numpy as np
from typing import List

logger = logging.getLogger(__name__)

_MOBILECLIP_NAMES = {"mobileclip", "apple/mobileclip"}


class EmbeddingGenerator:
    def __init__(self, config: dict):
        self.config = config
        self.device = config.get("device", "cuda")
        self.image_size = config.get("image_size", 336)
        self.batch_size = config.get("batch_size", 8)
        self._model = None
        self._processor = None
        self._tokenizer = None
        self._backend = None  # "open_clip" | "transformers"
        self._load_model()

    # ──────────────────────────────────────────────────────────
    # 로딩
    # ──────────────────────────────────────────────────────────

    def _load_model(self):
        model_name = self.config.get("model_name", "openai/clip-vit-large-patch14-336")
        is_mobileclip = any(k in model_name.lower() for k in _MOBILECLIP_NAMES)

        if is_mobileclip:
            self._load_open_clip(model_name)
        else:
            self._load_transformers_clip(model_name)

        # 둘 다 실패하면 transformers 기본 CLIP 시도
        if self._model is None:
            logger.warning("Primary model failed. Trying openai/clip-vit-large-patch14-336 as fallback.")
            self._load_transformers_clip("openai/clip-vit-large-patch14-336")

    def _load_open_clip(self, hf_model_name: str):
        """open_clip으로 MobileCLIP 계열 로드."""
        try:
            import torch
            import open_clip

            logger.info(f"Loading MobileCLIP via open_clip: {hf_model_name}")
            # HuggingFace Hub에서 직접 로드
            hf_hub_name = hf_model_name if hf_model_name.startswith("hf-hub:") else f"hf-hub:{hf_model_name}"
            model, _, preprocess = open_clip.create_model_and_transforms(hf_hub_name)
            tokenizer = open_clip.get_tokenizer(hf_hub_name)

            model = model.to(self.device)
            if self.device == "cuda":
                model = model.half()
            model.eval()

            self._model = model
            self._processor = preprocess
            self._tokenizer = tokenizer
            self._backend = "open_clip"
            logger.info("MobileCLIP loaded via open_clip successfully")
        except Exception as e:
            logger.warning(f"open_clip load failed for {hf_model_name}: {e}")

    def _load_transformers_clip(self, model_name: str):
        """transformers CLIPModel로 표준 CLIP 로드."""
        try:
            import torch
            from transformers import CLIPProcessor, CLIPModel

            logger.info(f"Loading CLIP via transformers: {model_name}")
            self._processor = CLIPProcessor.from_pretrained(model_name)
            self._model = CLIPModel.from_pretrained(
                model_name,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
            ).to(self.device)
            self._backend = "transformers"
            logger.info(f"CLIP embedding model loaded: {model_name}")
        except Exception as e:
            logger.warning(f"transformers CLIP load failed for {model_name}: {e}")

    # ──────────────────────────────────────────────────────────
    # 추론
    # ──────────────────────────────────────────────────────────

    def generate(self, frames: List[np.ndarray]) -> np.ndarray:
        if self._model is None:
            dim = self.config.get("embedding_dim", 512)
            return np.random.randn(len(frames), dim).astype(np.float32)
        try:
            return self._run_batch_inference(frames)
        except Exception as e:
            logger.error(f"Embedding generation error: {e}")
            dim = self.config.get("embedding_dim", 512)
            return np.zeros((len(frames), dim), dtype=np.float32)

    @staticmethod
    def _to_tensor(feats, import_torch=None):
        """
        transformers 버전에 따라 get_image/text_features()가
        tensor 대신 BaseModelOutputWithPooling 등 객체를 반환할 수 있음.
        항상 tensor로 추출한다.
        """
        import torch
        if isinstance(feats, torch.Tensor):
            return feats
        # dataclass / named output 객체: pooler_output 또는 last_hidden_state CLS 토큰 사용
        if hasattr(feats, "pooler_output") and feats.pooler_output is not None:
            return feats.pooler_output
        if hasattr(feats, "last_hidden_state"):
            return feats.last_hidden_state[:, 0]
        # 최후 수단: 첫 번째 요소
        return feats[0]

    def _run_batch_inference(self, frames: List[np.ndarray]) -> np.ndarray:
        import torch
        import torch.nn.functional as F
        from PIL import Image

        all_embeddings = []
        for i in range(0, len(frames), self.batch_size):
            batch = [Image.fromarray(f) for f in frames[i: i + self.batch_size]]

            if self._backend == "open_clip":
                imgs = torch.stack([self._processor(img) for img in batch]).to(self.device)
                if self.device == "cuda":
                    imgs = imgs.half()
                with torch.no_grad(), torch.amp.autocast("cuda", enabled=(self.device == "cuda")):
                    feats = self._model.encode_image(imgs)
            else:
                inputs = self._processor(images=batch, return_tensors="pt", padding=True).to(self.device)
                with torch.no_grad():
                    feats = self._to_tensor(self._model.get_image_features(**inputs))

            feats = F.normalize(feats.float(), dim=-1)
            all_embeddings.append(feats.cpu().numpy())
        return np.concatenate(all_embeddings, axis=0)

    def generate_text_embedding(self, text: str) -> np.ndarray:
        if self._model is None:
            return np.random.randn(self.config.get("embedding_dim", 512)).astype(np.float32)
        import torch
        import torch.nn.functional as F

        if self._backend == "open_clip":
            tokens = self._tokenizer([text]).to(self.device)
            with torch.no_grad(), torch.amp.autocast("cuda", enabled=(self.device == "cuda")):
                feats = self._model.encode_text(tokens)
        else:
            inputs = self._processor(text=[text], return_tensors="pt", padding=True).to(self.device)
            with torch.no_grad():
                feats = self._to_tensor(self._model.get_text_features(**inputs))

        feats = F.normalize(feats.float(), dim=-1)
        return feats.cpu().numpy()[0]

    def compute_similarity(self, query_embedding: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
        q = query_embedding / (np.linalg.norm(query_embedding) + 1e-8)
        e = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-8)
        return (e @ q).astype(np.float32)
