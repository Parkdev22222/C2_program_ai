"""
싱글톤 ML 모델 매니저 - 모델 중복 로딩 방지
"""
import yaml
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "models_config.yaml"


class ModelManager:
    _instance = None
    _detector = None
    _embedding_generator = None
    _description_generator = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config = cls._instance._load_config()
        return cls._instance

    def _load_config(self) -> dict:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)

    def get_detector(self):
        if self._detector is None:
            from .object_detection import ObjectDetector
            cfg = self._config["object_detection"]
            self._detector = ObjectDetector(cfg)
            logger.info("ObjectDetector loaded")
        return self._detector

    def get_embedding_generator(self):
        if self._embedding_generator is None:
            from .embedding_generator import EmbeddingGenerator
            cfg = self._config["embedding"]
            self._embedding_generator = EmbeddingGenerator(cfg)
            logger.info("EmbeddingGenerator loaded")
        return self._embedding_generator

    def get_description_generator(self):
        if self._description_generator is None:
            from .event_description import EventDescriptionGenerator
            cfg = self._config["event_description"]
            self._description_generator = EventDescriptionGenerator(cfg)
            logger.info("EventDescriptionGenerator loaded")
        return self._description_generator

    def preload_all(self):
        self.get_detector()
        self.get_embedding_generator()
        self.get_description_generator()
        logger.info("All ML models preloaded")

    def cleanup(self):
        import torch
        self._detector = None
        self._embedding_generator = None
        self._description_generator = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("ModelManager cleaned up")
