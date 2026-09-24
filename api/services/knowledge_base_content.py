"""Reading and replacing the text of editable knowledge base documents.

The stored file is the source of truth. A save overwrites it and re-runs the
normal processing job, which rebuilds ``full_text`` or the chunks from it, so
any later reprocess (retry, mode switch, re-embed) starts from the edited text.
"""

import hashlib
import os
import tempfile

import aiofiles

from api.db import db_client
from api.db.models import KnowledgeBaseDocumentModel
from api.services.storage import storage_fs
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames
from api.tasks.knowledge_base_processing import MAX_FILE_SIZE_BYTES

EDITABLE_EXTENSIONS = (".txt", ".md")

# Max tokens per chunk passed to document processing.
DEFAULT_CHUNK_MAX_TOKENS = 128

_STALE_MESSAGE = "This document changed since you opened it. Reload it and try again."


class DocumentNotEditableError(Exception):
    """The document's format or stored file can't be edited as text."""


class DocumentContentConflictError(Exception):
    """The document is processing, or changed since the editor loaded it."""


class DocumentContentTooLargeError(Exception):
    """The new content exceeds the document size limit."""


def is_editable_document(filename: str) -> bool:
    return filename.lower().endswith(EDITABLE_EXTENSIONS)


def _editable_storage_key(document: KnowledgeBaseDocumentModel) -> str:
    if not is_editable_document(document.filename):
        raise DocumentNotEditableError("Only .txt and .md documents can be edited.")
    s3_key = (document.custom_metadata or {}).get("s3_key")
    if not s3_key:
        raise DocumentNotEditableError(
            "The original file for this document is missing."
        )
    return s3_key


async def enqueue_document_processing(
    document_id: int,
    s3_key: str,
    organization_id: int,
    created_by_provider_id: str,
    retrieval_mode: str,
) -> None:
    await enqueue_job(
        FunctionNames.PROCESS_KNOWLEDGE_BASE_DOCUMENT,
        document_id,
        s3_key,
        organization_id,
        created_by_provider_id,
        DEFAULT_CHUNK_MAX_TOKENS,
        retrieval_mode,
    )


async def read_document_content(document: KnowledgeBaseDocumentModel) -> str:
    """Return the stored file's text exactly as uploaded or last saved."""
    s3_key = _editable_storage_key(document)

    with tempfile.TemporaryDirectory() as temp_dir:
        local_path = os.path.join(temp_dir, "content")
        if not await storage_fs.adownload_file(s3_key, local_path):
            raise RuntimeError(f"Failed to download {s3_key}")
        if os.path.getsize(local_path) > MAX_FILE_SIZE_BYTES:
            raise DocumentNotEditableError("This file is too large to edit.")
        async with aiofiles.open(local_path, "rb") as f:
            data = await f.read()

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentNotEditableError(
            "This file isn't UTF-8 text, so it can't be edited here."
        ) from exc


async def update_document_content(
    document: KnowledgeBaseDocumentModel,
    content: str,
    expected_file_hash: str,
    created_by_provider_id: str,
) -> KnowledgeBaseDocumentModel:
    """Overwrite a document's stored file and queue it for re-processing.

    Agents keep retrieving the previous version until processing succeeds.
    Returns the document as it now stands: ``pending``, or unchanged when the
    content is identical to a document that already processed successfully.
    """
    s3_key = _editable_storage_key(document)

    if document.processing_status in ("pending", "processing"):
        raise DocumentContentConflictError(
            "This document is still processing. Try again once it finishes."
        )
    if document.file_hash != expected_file_hash:
        raise DocumentContentConflictError(_STALE_MESSAGE)

    data = content.encode("utf-8")
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise DocumentContentTooLargeError(
            f"Documents can be at most {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB."
        )
    file_hash = hashlib.sha256(data).hexdigest()

    # A failed document still re-runs, so saving unchanged text doubles as retry.
    if file_hash == document.file_hash and document.processing_status == "completed":
        return document

    # Snapshot plain values; the claim may refresh `document` in place.
    previous_state = {
        "processing_status": document.processing_status,
        "processing_error": document.processing_error,
        "file_hash": document.file_hash,
        "file_size_bytes": document.file_size_bytes,
    }

    # Claim before writing: the losing side of two concurrent saves must not
    # touch the file the winner's job is about to read.
    claimed = await db_client.claim_document_for_content_update(
        document_uuid=document.document_uuid,
        organization_id=document.organization_id,
        expected_file_hash=expected_file_hash,
        file_hash=file_hash,
        file_size_bytes=len(data),
    )
    if claimed is None:
        raise DocumentContentConflictError(_STALE_MESSAGE)

    # Backends return False for errors they recognise but let others (network,
    # credentials) raise. Either way no job will run, so release the claim;
    # otherwise the document stays "pending" and every later save is rejected.
    try:
        if not await storage_fs.acreate_file_from_bytes(s3_key, data):
            raise RuntimeError(f"Failed to write {s3_key}")
    except Exception:
        await db_client.restore_document_after_failed_content_update(
            document_id=claimed.id,
            organization_id=claimed.organization_id,
            **previous_state,
        )
        raise

    try:
        await enqueue_document_processing(
            document_id=claimed.id,
            s3_key=s3_key,
            organization_id=claimed.organization_id,
            created_by_provider_id=created_by_provider_id,
            retrieval_mode=claimed.retrieval_mode,
        )
    except Exception:
        # The new file is already stored and hashed, so a later save of the
        # same text re-queues it.
        await db_client.update_document_status(
            claimed.id,
            "failed",
            error_message="Saved, but re-indexing couldn't start. Save again to retry.",
        )
        raise

    return claimed
