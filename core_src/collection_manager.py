"""
컬렉션 매니저 - 여러 비디오 컬렉션 관리
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class CollectionManager:
    def __init__(self, config: dict):
        self.config = config
        self.base_path = Path(config.get("storage_path", "./data/videodb"))
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._index_file = self.base_path / "collections.json"
        self._collections: Dict[str, Dict] = self._load_index()

    def _load_index(self) -> Dict[str, Dict]:
        if self._index_file.exists():
            with open(self._index_file) as f:
                return json.load(f)
        default = {
            "default": {
                "name": "default",
                "display_name": "기본 컬렉션",
                "created_at": datetime.now().isoformat(),
                "video_count": 0,
            }
        }
        self._save_index(default)
        return default

    def _save_index(self, data: Dict = None):
        with open(self._index_file, "w") as f:
            json.dump(data or self._collections, f, indent=2, ensure_ascii=False)

    def create_collection(self, name: str, display_name: str = "") -> Dict[str, Any]:
        if name in self._collections:
            return self._collections[name]
        collection = {
            "name": name,
            "display_name": display_name or name,
            "created_at": datetime.now().isoformat(),
            "video_count": 0,
        }
        self._collections[name] = collection
        coll_path = self.base_path / name
        coll_path.mkdir(parents=True, exist_ok=True)
        self._save_index()
        logger.info(f"Created collection: {name}")
        return collection

    def delete_collection(self, name: str) -> bool:
        if name == "default":
            logger.warning("Cannot delete default collection")
            return False
        if name not in self._collections:
            return False
        import shutil
        coll_path = self.base_path / name
        if coll_path.exists():
            shutil.rmtree(coll_path)
        del self._collections[name]
        self._save_index()
        logger.info(f"Deleted collection: {name}")
        return True

    def list_collections(self) -> List[Dict[str, Any]]:
        return list(self._collections.values())

    def get_collection(self, name: str) -> Optional[Dict[str, Any]]:
        return self._collections.get(name)

    def increment_video_count(self, collection_name: str):
        if collection_name in self._collections:
            self._collections[collection_name]["video_count"] = (
                self._collections[collection_name].get("video_count", 0) + 1
            )
            self._save_index()
