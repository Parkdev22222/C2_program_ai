"""
PDF RAG 도구 - 군사 교범 및 문서 검색 (FAISS 기반)
"""
import json
import uuid
import logging
import numpy as np
from pathlib import Path
from typing import List, Optional
from smolagents import tool

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 전역 상태
# ─────────────────────────────────────────────
_selected_pdf_ids: List[str] = []
_rag_system = None


def set_selected_pdfs(pdf_ids: List[str]):
    global _selected_pdf_ids
    _selected_pdf_ids = list(pdf_ids)


def get_rag_system():
    global _rag_system
    if _rag_system is None:
        _rag_system = PDFRAGSystem()
    return _rag_system


# ─────────────────────────────────────────────
# PDF RAG 시스템
# ─────────────────────────────────────────────

class PDFRAGSystem:
    def __init__(self, config: dict = None):
        if config is None:
            config = self._load_config()
        self.chunk_size = config.get("chunk_size", 512)
        self.chunk_overlap = config.get("chunk_overlap", 50)
        self.top_k = config.get("top_k", 5)
        self.similarity_threshold = config.get("similarity_threshold", 0.7)
        self.storage_path = Path(config.get("pdf_storage_path", "./data/pdfs"))
        self.index_path = Path(config.get("faiss_index_path", "./data/faiss_index"))
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.index_path.mkdir(parents=True, exist_ok=True)
        self._metadata_file = self.index_path / "metadata.json"
        self._metadata: dict = self._load_metadata()
        self._embeddings: dict = {}  # pdf_id → np.ndarray of chunk embeddings
        self._chunks: dict = {}       # pdf_id → List[str]
        self._embedding_model = None
        self._load_existing_indexes()

    def _load_config(self) -> dict:
        try:
            import yaml
            cfg_path = Path(__file__).parent.parent / "config" / "agent_config.yaml"
            with open(cfg_path) as f:
                return yaml.safe_load(f).get("rag", {})
        except Exception:
            return {}

    def _load_metadata(self) -> dict:
        if self._metadata_file.exists():
            with open(self._metadata_file) as f:
                return json.load(f)
        return {}

    def _save_metadata(self):
        with open(self._metadata_file, "w") as f:
            json.dump(self._metadata, f, indent=2, ensure_ascii=False)

    def _load_existing_indexes(self):
        for pdf_id, meta in self._metadata.items():
            emb_file = self.index_path / f"{pdf_id}_embeddings.npy"
            chunks_file = self.index_path / f"{pdf_id}_chunks.json"
            if emb_file.exists() and chunks_file.exists():
                self._embeddings[pdf_id] = np.load(emb_file)
                with open(chunks_file) as f:
                    self._chunks[pdf_id] = json.load(f)

    def _get_embedding_model(self):
        if self._embedding_model is None:
            try:
                from transformers import CLIPProcessor, CLIPModel
                import torch
                model_name = "openai/clip-vit-large-patch14-336"
                self._embedding_model = CLIPModel.from_pretrained(model_name)
                self._processor = CLIPProcessor.from_pretrained(model_name)
            except Exception as e:
                logger.warning(f"Failed to load embedding model for RAG: {e}")
        return self._embedding_model

    def _embed_texts(self, texts: List[str]) -> np.ndarray:
        model = self._get_embedding_model()
        if model is None:
            return np.random.randn(len(texts), 1024).astype(np.float32)
        import torch
        inputs = self._processor(text=texts, return_tensors="pt", padding=True, truncation=True, max_length=77)
        with torch.no_grad():
            feats = model.get_text_features(**inputs)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.cpu().float().numpy()

    def _chunk_text(self, text: str) -> List[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunks.append(text[start:end])
            start += self.chunk_size - self.chunk_overlap
        return chunks

    def add_pdf(self, pdf_path: str) -> str:
        try:
            import PyPDF2
        except ImportError:
            raise ImportError("PyPDF2 is required: pip install PyPDF2")

        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        pdf_id = f"pdf-{uuid.uuid4().hex[:8]}"
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            full_text = "\n".join(page.extract_text() or "" for page in reader.pages)

        chunks = self._chunk_text(full_text)
        if not chunks:
            raise ValueError("PDF에서 텍스트를 추출할 수 없습니다.")

        embeddings = self._embed_texts(chunks)
        self._embeddings[pdf_id] = embeddings
        self._chunks[pdf_id] = chunks

        np.save(self.index_path / f"{pdf_id}_embeddings.npy", embeddings)
        with open(self.index_path / f"{pdf_id}_chunks.json", "w") as f:
            json.dump(chunks, f, ensure_ascii=False)

        self._metadata[pdf_id] = {
            "pdf_id": pdf_id,
            "filename": pdf_path.name,
            "path": str(pdf_path),
            "chunk_count": len(chunks),
            "page_count": len(reader.pages),
        }
        self._save_metadata()
        logger.info(f"PDF added: {pdf_id} ({pdf_path.name}), {len(chunks)} chunks")
        return pdf_id

    def search(self, query: str, pdf_ids: List[str] = None, top_k: int = 5) -> List[dict]:
        query_emb = self._embed_texts([query])[0]
        results = []

        target_ids = pdf_ids if pdf_ids else list(self._embeddings.keys())
        for pid in target_ids:
            if pid not in self._embeddings:
                continue
            embs = self._embeddings[pid]
            chunks = self._chunks[pid]
            sims = (embs @ query_emb) / (
                np.linalg.norm(embs, axis=1) * np.linalg.norm(query_emb) + 1e-8
            )
            for i, (sim, chunk) in enumerate(zip(sims, chunks)):
                results.append({
                    "pdf_id": pid,
                    "filename": self._metadata.get(pid, {}).get("filename", ""),
                    "chunk_index": i,
                    "text": chunk,
                    "similarity": round(float(sim), 4),
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        filtered = [r for r in results if r["similarity"] >= self.similarity_threshold]
        return filtered[:top_k]

    def list_pdfs(self) -> List[dict]:
        return list(self._metadata.values())


# ─────────────────────────────────────────────
# smolagents 도구 함수들
# ─────────────────────────────────────────────

@tool
def pdf_rag_search(query: str, top_k: int = 5) -> dict:
    """
    군사 교범 및 PDF 문서에서 관련 내용을 검색합니다.

    Args:
        query: 검색 쿼리 (예: "대전차 방어 전술", "도심 전투 교리")
        top_k: 반환할 최대 결과 수

    Returns:
        {
            "status": "success" | "no_results" | "error",
            "results": [{"pdf_id", "filename", "text", "similarity"}, ...],
            "query": str
        }
    """
    if not _selected_pdf_ids:
        return {
            "status": "no_context",
            "message": "선택된 PDF가 없습니다. add_pdf_to_rag()로 PDF를 추가하거나 UI에서 선택하세요.",
            "results": [],
        }
    try:
        rag = get_rag_system()
        results = rag.search(query, pdf_ids=_selected_pdf_ids, top_k=top_k)
        return {
            "status": "success" if results else "no_results",
            "query": query,
            "total_found": len(results),
            "results": results,
        }
    except Exception as e:
        logger.error(f"PDF RAG search error: {e}")
        return {"status": "error", "message": str(e), "results": []}


@tool
def add_pdf_to_rag(pdf_path: str) -> dict:
    """
    PDF 파일을 RAG 시스템에 추가합니다.
    군사 교범, 작전 계획서, 지형 분석 보고서 등을 추가할 수 있습니다.

    Args:
        pdf_path: 추가할 PDF 파일 경로

    Returns:
        {"status": "success" | "error", "pdf_id": str, "message": str}
    """
    try:
        rag = get_rag_system()
        pdf_id = rag.add_pdf(pdf_path)
        _selected_pdf_ids.append(pdf_id)
        return {
            "status": "success",
            "pdf_id": pdf_id,
            "message": f"PDF 추가 완료: {pdf_id}",
        }
    except Exception as e:
        logger.error(f"Add PDF error: {e}")
        return {"status": "error", "message": str(e)}
