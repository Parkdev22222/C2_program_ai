"""
이미지 임베딩 생성 모듈 (CLIP 계열 모델 사용)
"""
import logging
import numpy as np
from typing import List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class EmbeddingGenerator:
    def __init__(self, config: dict):
        self.config = config
        self.device = config.get("device", "cuda")
        self.image_size = config.get("image_size", 336)
        self.batch_size = config.get("batch_size", 8)
        self._model = None
        self._processor = None
        self._load_model()

    def _load_model(self):
        try:
            import torch
            from transformers import CLIPProcessor, CLIPModel

            model_name = self.config.get("model_name", "openai/clip-vit-large-patch14-336")
            logger.info(f"Loading embedding model: {model_name}")
            self._processor = CLIPProcessor.from_pretrained(model_name)
            self._model = CLIPModel.from_pretrained(
                model_name,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
            ).to(self.device)
            logger.info("Embedding model loaded successfully")
        except Exception as e:
            logger.warning(f"Failed to load embedding model: {e}. Using random embeddings.")
            self._model = None

    def generate(self, frames: List[np.ndarray]) -> np.ndarray:
        if self._model is None:
            dim = self.config.get("embedding_dim", 1024)
            return np.random.randn(len(frames), dim).astype(np.float32)
        try:
            return self._run_batch_inference(frames)
        except Exception as e:
            logger.error(f"Embedding generation error: {e}")
            dim = self.config.get("embedding_dim", 1024)
            return np.zeros((len(frames), dim), dtype=np.float32)

    def _run_batch_inference(self, frames: List[np.ndarray]) -> np.ndarray:
        import torch
        from PIL import Image

        all_embeddings = []
        for i in range(0, len(frames), self.batch_size):
            batch = frames[i : i + self.batch_size]
            images = [Image.fromarray(f) for f in batch]
            inputs = self._processor(images=images, return_tensors="pt", padding=True).to(self.device)
            with torch.no_grad():
                feats = self._model.get_image_features(**inputs)
                feats = feats / feats.norm(dim=-1, keepdim=True)
            all_embeddings.append(feats.cpu().float().numpy())
        return np.concatenate(all_embeddings, axis=0)

    def compute_similarity(self, query_embedding: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
        q = query_embedding / (np.linalg.norm(query_embedding) + 1e-8)
        e = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-8)
        return (e @ q).astype(np.float32)

    def generate_text_embedding(self, text: str) -> np.ndarray:
        if self._model is None:
            return np.random.randn(self.config.get("embedding_dim", 1024)).astype(np.float32)
        import torch

        inputs = self._processor(text=[text], return_tensors="pt", padding=True).to(self.device)
        with torch.no_grad():
            feats = self._model.get_text_features(**inputs)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.cpu().float().numpy()[0]
