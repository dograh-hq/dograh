"""
Local MPS Document Processing — based on luminai/turbovec pipeline.

Replaces Dograh MPS document processing with a local pipeline:
- PDF: pypdf native → Mistral OCR fallback (same as luminai)
- DOCX: python-docx
- TXT: plain text
- Embeddings: Google Gemini (or OpenAI-compatible fallback)
- Vector store: turbovec TurboQuantVectorDb (4-bit quantized, ~8x compression)
- Chunking: configurable size/overlap

Run:
    python -m api.services.local_mps_doc_processor

Configure in api/.env:
    MPS_API_URL=http://localhost:9002
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.getenv("LOCAL_MPS_DATA_DIR", "/tmp/dograh_local_mps"))
CHUNK_SIZE = int(os.getenv("LOCAL_MPS_CHUNK_SIZE", "512"))
CHUNK_OVERLAP = int(os.getenv("LOCAL_MPS_CHUNK_OVERLAP", "64"))
EMBEDDING_DIM = int(os.getenv("LOCAL_MPS_EMBEDDING_DIM", "768"))
EMBEDDING_PROVIDER = os.getenv("LOCAL_MPS_EMBEDDING_PROVIDER", "openai")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1")
EMBEDDING_MODEL = os.getenv("LOCAL_MPS_EMBEDDING_MODEL", "text-embedding-3-small")
# Vertex AI settings
VERTEX_PROJECT_ID = os.getenv("VERTEX_PROJECT_ID", "")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")

_MIN_TEXT_LENGTH = 50

app = FastAPI(title="Local MPS Document Processor")


# ────────────────────────────────────────────────────────────────────
# Document Parsing (from luminai document_parser.py)
# ────────────────────────────────────────────────────────────────────
def normalize_extracted_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            lines.append("")
            continue
        line = re.sub(r"\s+", " ", line)
        lines.append(line)
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from PDF using pypdf."""
    from pypdf import PdfReader
    reader = PdfReader(file_path)
    pages_text = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)
    full_text = normalize_extracted_text("\n\n".join(pages_text))
    if len(full_text.strip()) < _MIN_TEXT_LENGTH:
        raise ValueError("PDF contains insufficient extractable text (< 50 chars).")
    logger.info("PDF: %s → %d chars from %d pages", file_path, len(full_text), len(pages_text))
    return full_text


def extract_text_from_docx(file_path: str) -> str:
    """Extract text from DOCX using python-docx."""
    from docx import Document
    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    full_text = normalize_extracted_text("\n\n".join(paragraphs))
    if len(full_text.strip()) < _MIN_TEXT_LENGTH:
        raise ValueError("DOCX contains insufficient extractable text.")
    logger.info("DOCX: %s → %d chars", file_path, len(full_text))
    return full_text


def extract_text_from_txt(file_path: str) -> str:
    """Extract text from plain text file."""
    full_text = normalize_extracted_text(Path(file_path).read_text(encoding="utf-8"))
    if len(full_text.strip()) < _MIN_TEXT_LENGTH:
        raise ValueError("TXT contains insufficient text (< 50 chars).")
    logger.info("TXT: %s → %d chars", file_path, len(full_text))
    return full_text


def extract_text_from_file(file_path: str, file_type: str) -> str:
    """Dispatch to the correct parser based on file type."""
    ext = file_type.lower().split("/")[-1] if "/" in file_type else file_type.lower()
    # Normalize common MIME subtypes to canonical extensions
    _mime_map = {"plain": "txt", "vnd.openxmlformats-officedocument.wordprocessingml.document": "docx"}
    ext = _mime_map.get(ext, ext)
    parsers = {
        "pdf": extract_text_from_pdf,
        "docx": extract_text_from_docx,
        "doc": extract_text_from_docx,
        "txt": extract_text_from_txt,
        "json": extract_text_from_txt,
    }
    if ext not in parsers:
        raise ValueError(f"Unsupported file type: {file_type}. Supported: pdf, docx, txt, json")
    return parsers[ext](file_path)


# ────────────────────────────────────────────────────────────────────
# Chunking
# ────────────────────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks at word boundaries."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        chunk = " ".join(words[start:start + chunk_size])
        chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


# ────────────────────────────────────────────────────────────────────
# Embeddings
# ────────────────────────────────────────────────────────────────────
def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Get embeddings from the configured provider."""
    if EMBEDDING_PROVIDER == "openai":
        return _openai_embeddings(texts)
    elif EMBEDDING_PROVIDER == "gemini":
        return _gemini_embeddings(texts)
    elif EMBEDDING_PROVIDER == "vertex":
        return _vertex_embeddings(texts)
    else:
        return _openai_embeddings(texts)


def _openai_embeddings(texts: list[str]) -> list[list[float]]:
    import httpx
    resp = httpx.post(
        f"{OPENAI_API_URL}/embeddings",
        json={"model": EMBEDDING_MODEL, "input": texts},
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        timeout=httpx.Timeout(60.0),
    )
    resp.raise_for_status()
    data = resp.json()
    return [item["embedding"] for item in data["data"]]


def _gemini_embeddings(texts: list[str]) -> list[list[float]]:
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    result = genai.embed_content(
        model="models/text-embedding-004",
        content=texts,
        task_type="retrieval_document",
    )
    embeddings = result.get("embedding", [])
    if embeddings and isinstance(embeddings[0], list):
        return embeddings
    return [embeddings]


def _vertex_embeddings(texts: list[str]) -> list[list[float]]:
    """Get embeddings from Vertex AI (text-embedding-004).

    Auth: reads GOOGLE_APPLICATION_CREDENTIALS (service account JSON path)
    or falls back to VERTEX_CREDENTIALS inline JSON.
    """
    import httpx
    import google.auth
    import google.auth.transport.requests

    project = VERTEX_PROJECT_ID
    location = VERTEX_LOCATION

    if not project:
        raise ValueError("VERTEX_PROJECT_ID is required for Vertex AI embeddings")

    # Try explicit inline credentials first, then ADC
    credentials_json = os.getenv("VERTEX_CREDENTIALS", "")
    if credentials_json:
        import json as _json
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            _json.loads(credentials_json),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    else:
        creds, _project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not project:
            project = _project

    creds.refresh(google.auth.transport.requests.Request())
    token = creds.token

    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/"
        f"projects/{project}/locations/{location}/"
        f"publishers/google/models/text-embedding-004:predict"
    )

    instances = [{"content": t} for t in texts]

    resp = httpx.post(
        url,
        json={"instances": instances},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(60.0),
    )
    resp.raise_for_status()
    data = resp.json()

    # Response shape: {"predictions": [{"embeddings": {"values": [...]}}]}
    return [
        pred["embeddings"]["values"]
        for pred in data["predictions"]
    ]


# ────────────────────────────────────────────────────────────────────
# Vector Store — turbovec-backed (luminai-compatible), numpy fallback
# ────────────────────────────────────────────────────────────────────

try:
    import numpy as np
    from turbovec import TurboQuantIndex, IdMapIndex
    TURBOVEC_AVAILABLE = True
    logger.info("turbovec available — using TurboQuantIndex (4-bit quantized)")
except ImportError:
    TURBOVEC_AVAILABLE = False
    logger.info("turbovec not installed — using numpy fallback")


class TurbovecStore:
    """Vector store backed by turbovec IdMapIndex (same as luminai)."""

    def __init__(self, collection: str):
        self.collection = collection
        self.collection_dir = DATA_DIR / collection
        self.collection_dir.mkdir(parents=True, exist_ok=True)
        self._index: IdMapIndex | None = None
        self._texts: list[str] = []
        self._metadata: list[dict] = []
        self._next_id: int = 0

    def insert(self, texts: list[str], vectors: list[list[float]], metadata: list[dict]) -> None:
        if not vectors:
            return
        vec_array = np.array(vectors, dtype=np.float32)
        ids = np.arange(self._next_id, self._next_id + len(vectors), dtype=np.uint64)

        if self._index is None:
            self._index = IdMapIndex(dim=vec_array.shape[1], bit_width=4)
            self._index.add_with_ids(vec_array, ids)
        else:
            self._index.add_with_ids(vec_array, ids)

        self._texts.extend(texts)
        self._metadata.extend(metadata)
        self._next_id += len(vectors)
        self._save()

    def search(self, query_vector: list[float], limit: int = 5) -> list[dict]:
        if self._index is None:
            return []
        q = np.array([query_vector], dtype=np.float32)
        scores, indices = self._index.search(q, k=limit)
        results = []
        for i, idx in enumerate(indices[0]):
            if int(idx) < len(self._texts) and float(scores[0][i]) > 0:
                results.append({
                    "text": self._texts[int(idx)],
                    "metadata": self._metadata[int(idx)],
                    "score": float(scores[0][i]),
                })
        return results

    def _save(self) -> None:
        if self._index is not None:
            self._index.write(str(self.collection_dir / "index.tvim"))
        data = {"texts": self._texts, "metadata": self._metadata, "next_id": self._next_id}
        with open(self.collection_dir / "store.json", "w") as f:
            json.dump(data, f)

    def _load(self) -> None:
        index_file = self.collection_dir / "index.tvim"
        store_file = self.collection_dir / "store.json"
        if index_file.exists() and store_file.exists():
            self._index = IdMapIndex.load(str(index_file))
            with open(store_file) as f:
                data = json.load(f)
            self._texts = data.get("texts", [])
            self._metadata = data.get("metadata", [])
            self._next_id = data.get("next_id", len(self._texts))


class NumpyStore:
    """Fallback vector store using numpy cosine similarity."""

    def __init__(self, collection: str):
        self.collection = collection
        self.collection_dir = DATA_DIR / collection
        self.collection_dir.mkdir(parents=True, exist_ok=True)
        self._vectors: list[list[float]] = []
        self._metadata: list[dict] = []
        self._texts: list[str] = []

    def insert(self, texts: list[str], vectors: list[list[float]], metadata: list[dict]) -> None:
        self._texts.extend(texts)
        self._vectors.extend(vectors)
        self._metadata.extend(metadata)
        self._save()

    def search(self, query_vector: list[float], limit: int = 5) -> list[dict]:
        if not self._vectors:
            return []
        q = np.array(query_vector)
        db = np.array(self._vectors)
        q_norm = q / (np.linalg.norm(q) + 1e-10)
        db_norm = db / (np.linalg.norm(db, axis=1, keepdims=True) + 1e-10)
        scores = np.dot(db_norm, q_norm)
        top_indices = np.argsort(scores)[-limit:][::-1]
        results = []
        for idx in top_indices:
            if float(scores[idx]) > 0:
                results.append({
                    "text": self._texts[idx],
                    "metadata": self._metadata[idx],
                    "score": float(scores[idx]),
                })
        return results

    def _save(self) -> None:
        data = {"texts": self._texts, "metadata": self._metadata, "vectors": [v for v in self._vectors]}
        with open(self.collection_dir / "store.json", "w") as f:
            json.dump(data, f)

    def _load(self) -> None:
        store_file = self.collection_dir / "store.json"
        if store_file.exists():
            with open(store_file) as f:
                data = json.load(f)
            self._texts = data.get("texts", [])
            self._metadata = data.get("metadata", [])
            self._vectors = data.get("vectors", [])


# ────────────────────────────────────────────────────────────────────
# Storage backends
# ────────────────────────────────────────────────────────────────────
_stores: dict[str, TurbovecStore | NumpyStore] = {}


def _get_store(collection: str) -> TurbovecStore | NumpyStore:
    if collection not in _stores:
        store_cls = TurbovecStore if TURBOVEC_AVAILABLE else NumpyStore
        store = store_cls(collection)
        store._load()
        _stores[collection] = store
    return _stores[collection]


# ────────────────────────────────────────────────────────────────────
# API Endpoints — mirrors Dograh MPS document processing
# ────────────────────────────────────────────────────────────────────
@app.post("/api/v1/document/process")
async def process_document_route(
    file: UploadFile = File(...),
    retrieval_mode: str = Form("full_document"),
    document_uuid: str = Form(""),
    s3_key: str = Form(""),
):
    """Route handler — delegates to process_document()."""
    return await process_document(file, retrieval_mode, document_uuid, s3_key)


async def process_document(
    file: UploadFile,
    retrieval_mode: str = "full_document",
    document_uuid: str = "",
    s3_key: str = "",
) -> dict:
    """
    Process an uploaded document: extract text, optionally chunk and embed.
    Returns the same shape as MPS for drop-in compatibility.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Save temp file
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Extract text
        file_type = file.content_type or suffix.lstrip(".")
        full_text = extract_text_from_file(tmp_path, file_type)

        if retrieval_mode == "chunked" or retrieval_mode == "chunked_search":
            # Chunk + embed
            chunks = chunk_text(full_text)
            embeddings = get_embeddings(chunks) if chunks else []
            store = _get_store(document_uuid or "default")
            metadata = [{"document_uuid": document_uuid, "chunk_index": i, "s3_key": s3_key} for i in range(len(chunks))]
            if chunks and embeddings:
                store.insert(chunks, embeddings, metadata)

            return {
                "document_uuid": document_uuid or hashlib.md5(full_text.encode()).hexdigest(),
                "s3_key": s3_key,
                "status": "completed",
                "retrieval_mode": "chunked",
                "total_chunks": len(chunks),
                "text_preview": full_text[:500],
                "chunks": [{"index": i, "text": c[:200], "embedding_dim": len(embeddings[i]) if i < len(embeddings) else 0} for i, c in enumerate(chunks[:10])],
            }
        else:
            # Full document mode
            return {
                "document_uuid": document_uuid or hashlib.md5(full_text.encode()).hexdigest(),
                "s3_key": s3_key,
                "status": "completed",
                "retrieval_mode": "full_document",
                "full_text": full_text,
                "text_length": len(full_text),
            }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Document processing failed")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/api/v1/document/search")
async def search_route(
    query: str = Form(...),
    document_uuid: str = Form(""),
    limit: int = Form(5),
):
    """Route handler — delegates to search_documents()."""
    return await search_documents(query, document_uuid, limit)


async def search_documents(
    query: str,
    document_uuid: str = "",
    limit: int = 5,
) -> dict:
    """Vector search over processed documents."""
    store = _get_store(document_uuid or "default")
    if not store._vectors:
        return {"results": [], "message": "No documents indexed yet"}

    query_embedding = get_embeddings([query])[0]
    results = store.search(query_embedding, limit=limit)
    return {"results": results, "query": query}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "local-mps-doc-processor"}


if __name__ == "__main__":
    port = int(os.getenv("LOCAL_MPS_PORT", "9002"))
    print(f"Starting Local MPS Document Processor on port {port}")
    print(f"Data directory: {DATA_DIR}")
    print(f"Embedding: {EMBEDDING_PROVIDER} / {EMBEDDING_MODEL} ({EMBEDDING_DIM}d)")
    print(f"Configure Dograh with: MPS_API_URL=http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
