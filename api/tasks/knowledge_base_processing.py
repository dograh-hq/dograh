"""ARQ background task for processing knowledge base documents.

Document conversion and chunking live in the Model Proxy Service (MPS);
this task downloads the file from S3, calls MPS, then handles the embedding
and DB writes locally. Plain text is the exception -- see
PLAIN_TEXT_EXTENSIONS.
"""

import os
import tempfile
from pathlib import Path

from loguru import logger

from api.db import db_client
from api.db.models import KnowledgeBaseChunkModel
from api.services.gen_ai import build_embedding_service
from api.services.mps_service_key_client import mps_service_key_client
from api.services.storage import storage_fs

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
EMBEDDING_BATCH_SIZE = 64

# Document conversion has no plain-text backend, so text routed through it
# comes back corrupted -- non-ASCII scripts lose characters outright (#634).
# Reading text files here restores the bypass that PR #244 removed. ".json"
# probably needs this too but is unverified, so it stays out for now.
PLAIN_TEXT_EXTENSIONS = {".txt"}


def _size(ascii_chars: int, other_chars: int) -> int:
    """Approximate a BPE token count: ASCII ~4 chars/token, CJK ~1."""
    return ascii_chars // 4 + other_chars


def _estimate_size(text: str) -> int:
    """Approximate token count of ``text``. Not a real tokenizer.

    Only picks chunk boundaries and fills the advisory token_count column,
    so being off by a factor costs nothing.
    """
    ascii_chars = sum(char.isascii() for char in text)
    return _size(ascii_chars, len(text) - ascii_chars)


def _fit_pieces(line: str, budget: int):
    """Yield consecutive slices of ``line`` that each fit inside ``budget``.

    Walks the line once, counting as it goes, so a newline-free file costs
    one pass rather than a rescan per chunk. Counting the real prefix (not
    the line's average character density) keeps every piece inside budget
    even where scripts are mixed.
    """
    if _estimate_size(line) <= budget:
        yield line
        return

    start = index = ascii_chars = other_chars = 0
    while index < len(line):
        if line[index].isascii():
            ascii_chars += 1
        else:
            other_chars += 1
        index += 1
        if _size(ascii_chars, other_chars) >= budget:
            yield line[start:index]
            start, ascii_chars, other_chars = index, 0, 0

    if start < len(line):
        yield line[start:]


def _split_plain_text(text: str, budget: int) -> list[str]:
    """Split into chunks of at most ``budget`` estimated tokens, on line
    boundaries where possible.

    Invariant: ``"".join(_split_plain_text(t, n)) == t``. Silent text
    mutation is the bug this exists to avoid.
    """
    budget = max(budget, 1)
    chunks: list[str] = []
    buffer: list[str] = []
    buffered_ascii = buffered_other = 0

    for line in text.splitlines(keepends=True):
        for piece in _fit_pieces(line, budget):
            ascii_chars = sum(char.isascii() for char in piece)
            other_chars = len(piece) - ascii_chars

            if buffer and (
                _size(buffered_ascii + ascii_chars, buffered_other + other_chars)
                > budget
            ):
                chunks.append("".join(buffer))
                buffer, buffered_ascii, buffered_other = [], 0, 0

            buffer.append(piece)
            buffered_ascii += ascii_chars
            buffered_other += other_chars

    if buffer:
        chunks.append("".join(buffer))
    return chunks


def _read_plain_text(file_path: str) -> str:
    """Read a UTF-8 text file exactly as uploaded.

    ``utf-8-sig`` drops a leading BOM only; ``newline=""`` keeps CRLF from
    being rewritten to LF. Invalid UTF-8 raises rather than substituting
    replacement characters.
    """
    return Path(file_path).read_text(encoding="utf-8-sig", newline="")


async def _embed_texts_in_batches(
    embedding_service,
    texts: list[str],
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> list[list[float]]:
    """Generate embeddings in bounded batches for provider/MPS stability."""
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        logger.info(
            f"Generating embedding batch {start // batch_size + 1} ({len(batch)} texts)"
        )
        embeddings.extend(await embedding_service.embed_texts(batch))
    return embeddings


async def process_knowledge_base_document(
    ctx,
    document_id: int,
    s3_key: str,
    organization_id: int,
    created_by_provider_id: str,
    max_tokens: int = 128,
    retrieval_mode: str = "chunked",
):
    """Process a knowledge base document via MPS: download, call MPS, embed, store.

    Args:
        ctx: ARQ context
        document_id: Database ID of the document
        s3_key: S3 key where the file is stored
        organization_id: Organization ID
        created_by_provider_id: Uploading user's provider ID (for OSS-mode auth to MPS)
        max_tokens: Maximum number of tokens per chunk (default: 128)
        retrieval_mode: "chunked" for vector search or "full_document" for full text
    """
    logger.info(
        f"Processing knowledge base document: document_id={document_id}, "
        f"s3_key={s3_key}, org={organization_id}, mode={retrieval_mode}"
    )

    temp_file_path = None

    try:
        await db_client.update_document_status(document_id, "processing")

        filename = s3_key.split("/")[-1]
        file_extension = os.path.splitext(filename)[1] or ".bin"

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
        temp_file_path = temp_file.name
        temp_file.close()

        logger.info(f"Downloading file from S3: {s3_key}")
        download_success = await storage_fs.adownload_file(s3_key, temp_file_path)
        if not download_success:
            raise Exception(f"Failed to download file from S3: {s3_key}")
        if not os.path.exists(temp_file_path):
            raise FileNotFoundError(f"Downloaded file not found: {temp_file_path}")

        file_size = os.path.getsize(temp_file_path)
        logger.info(f"Downloaded file size: {file_size} bytes")

        if file_size > MAX_FILE_SIZE_BYTES:
            error_message = (
                f"File size ({file_size / (1024 * 1024):.1f}MB) exceeds the "
                f"maximum allowed size of {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB."
            )
            logger.warning(f"Document {document_id}: {error_message}")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

        file_hash = db_client.compute_file_hash(temp_file_path)
        mime_type = db_client.get_mime_type(temp_file_path)

        document = await db_client.get_document_by_id(document_id)
        if not document:
            raise Exception(f"Document {document_id} not found")

        # Reject duplicates (same hash already ingested for this org).
        existing_doc = await db_client.get_document_by_hash(file_hash, organization_id)
        if existing_doc and existing_doc.id != document_id:
            error_message = (
                f"This file is a duplicate of '{existing_doc.filename}'. "
                f"Please delete the duplicate files and consolidate them into a "
                f"single unique file before uploading."
            )
            logger.warning(
                f"Duplicate document detected: {document_id} is duplicate of "
                f"{existing_doc.id} ({existing_doc.filename})"
            )
            await db_client.update_document_metadata(
                document_id,
                file_size_bytes=file_size,
                file_hash=file_hash,
                mime_type=mime_type,
            )
            await db_client.update_document_status(
                document_id,
                "failed",
                error_message=error_message,
                docling_metadata={
                    "duplicate_of": existing_doc.document_uuid,
                    "duplicate_filename": existing_doc.filename,
                },
            )
            return

        await db_client.update_document_metadata(
            document_id,
            file_size_bytes=file_size,
            file_hash=file_hash,
            mime_type=mime_type,
        )

        embeddings_provider = None
        embeddings_api_key = None
        embeddings_model = None
        embeddings_base_url = None
        embeddings_endpoint = None
        embeddings_api_version = None
        if retrieval_mode == "chunked":
            from api.services.configuration.ai_model_configuration import (
                apply_managed_embeddings_base_url,
                get_resolved_ai_model_configuration,
            )

            resolved_config = await get_resolved_ai_model_configuration(
                organization_id=document.organization_id,
            )
            effective_config = resolved_config.effective
            if effective_config.embeddings:
                embeddings_provider = getattr(
                    effective_config.embeddings, "provider", None
                )
                embeddings_api_key = effective_config.embeddings.api_key
                embeddings_model = effective_config.embeddings.model
                embeddings_base_url = apply_managed_embeddings_base_url(
                    provider=embeddings_provider,
                    base_url=getattr(effective_config.embeddings, "base_url", None),
                )
                embeddings_endpoint = getattr(
                    effective_config.embeddings, "endpoint", None
                )
                embeddings_api_version = getattr(
                    effective_config.embeddings, "api_version", None
                )
                logger.info(
                    f"Using user embeddings config: provider={embeddings_provider}, "
                    f"model={embeddings_model}"
                )

        is_plain_text = file_extension.lower() in PLAIN_TEXT_EXTENSIONS
        mps_response: dict = {}

        if is_plain_text:
            logger.info(f"Reading {file_extension} file directly (bypassing MPS)")
            try:
                full_text = _read_plain_text(temp_file_path)
            except UnicodeDecodeError as e:
                error_message = (
                    f"'{filename}' is not valid UTF-8 text and could not be read "
                    f"({e.reason}). Re-save the file as UTF-8 and upload it again."
                )
                logger.warning(f"Document {document_id}: {error_message}")
                await db_client.update_document_status(
                    document_id, "failed", error_message=error_message
                )
                return
            docling_metadata = {"num_pages": None, "document_type": "PlainText"}
        else:
            logger.info(
                f"Delegating document processing to MPS (mode={retrieval_mode})"
            )
            mps_response = await mps_service_key_client.process_document(
                file_path=temp_file_path,
                filename=filename,
                content_type=mime_type or "application/octet-stream",
                retrieval_mode=retrieval_mode,
                max_tokens=max_tokens,
                organization_id=organization_id,
                created_by=created_by_provider_id,
            )
            docling_metadata = mps_response.get("docling_metadata", {})
            full_text = mps_response.get("full_text") or ""

        if retrieval_mode == "full_document":
            await db_client.update_document_full_text(document_id, full_text)
            await db_client.update_document_status(
                document_id,
                "completed",
                total_chunks=0,
                docling_metadata=docling_metadata,
            )
            logger.info(
                f"Successfully processed full_document {document_id}. "
                f"Text length: {len(full_text)} chars"
            )
            return

        if not embeddings_api_key:
            error_message = (
                "API key not configured. Please set your API key in "
                "Model Configurations > Embedding to process documents."
            )
            logger.warning(f"Document {document_id}: {error_message}")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

        # Ingestion runs outside any workflow run, so resolve the MPS correlation
        # id here.
        embedding_service = await build_embedding_service(
            db_client=db_client,
            provider=embeddings_provider,
            api_key=embeddings_api_key,
            model=embeddings_model,
            base_url=embeddings_base_url,
            endpoint=embeddings_endpoint,
            api_version=embeddings_api_version,
            resolve_correlation=True,
        )

        if is_plain_text:
            source_chunks = [
                {
                    "chunk_text": chunk_text,
                    "chunk_index": index,
                    "token_count": _estimate_size(chunk_text),
                }
                for index, chunk_text in enumerate(
                    _split_plain_text(full_text, max_tokens)
                )
            ]
            if not source_chunks:
                logger.warning(f"Document {document_id}: file contains no text")
        else:
            source_chunks = mps_response.get("chunks", [])
            if not source_chunks:
                logger.warning(f"Document {document_id}: MPS returned zero chunks")

        chunk_records = []
        chunk_texts = []
        for chunk in source_chunks:
            contextualized = chunk.get("contextualized_text") or chunk["chunk_text"]
            chunk_records.append(
                KnowledgeBaseChunkModel(
                    document_id=document_id,
                    organization_id=organization_id,
                    chunk_text=chunk["chunk_text"],
                    contextualized_text=contextualized,
                    chunk_index=chunk["chunk_index"],
                    chunk_metadata=chunk.get("chunk_metadata") or {},
                    embedding_model=embedding_service.get_model_id(),
                    embedding_dimension=embedding_service.get_embedding_dimension(),
                    token_count=chunk.get("token_count", 0),
                )
            )
            chunk_texts.append(contextualized)

        logger.info(
            f"Generating embeddings for {len(chunk_texts)} chunks "
            f"using {embedding_service.get_model_id()}"
        )
        embeddings = await _embed_texts_in_batches(embedding_service, chunk_texts)
        if len(embeddings) != len(chunk_records):
            raise ValueError(
                "Embedding count mismatch: "
                f"expected {len(chunk_records)}, got {len(embeddings)}"
            )
        for chunk_record, embedding in zip(chunk_records, embeddings):
            chunk_record.embedding = embedding

        logger.info("Storing chunks in database")
        await db_client.replace_chunks_for_document(
            document_id=document_id,
            organization_id=organization_id,
            chunks=chunk_records,
        )

        await db_client.update_document_status(
            document_id,
            "completed",
            total_chunks=len(chunk_records),
            docling_metadata=docling_metadata,
        )

        logger.info(
            f"Successfully processed knowledge base document {document_id}. "
            f"Total chunks: {len(chunk_records)}"
        )

    except Exception as e:
        logger.exception(
            "Error processing knowledge base document {}: {}", document_id, e
        )
        await db_client.update_document_status(
            document_id, "failed", error_message=str(e)
        )
        raise

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.debug(f"Cleaned up temp file: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {temp_file_path}: {e}")
