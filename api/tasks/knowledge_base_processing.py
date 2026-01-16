"""ARQ background task for processing knowledge base documents."""

import os
import tempfile

from docling.chunking import HybridChunker
from docling.document_converter import DocumentConverter
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from loguru import logger
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer

from api.db import db_client
from api.db.models import KnowledgeBaseChunkModel, KnowledgeBaseDocumentModel
from api.services.storage import storage_fs

# Constants
EMBED_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384


async def process_knowledge_base_document(
    ctx, document_id: int, s3_key: str, organization_id: int, max_tokens: int = 128
):
    """Process a knowledge base document: download, chunk, embed, and store.

    Args:
        ctx: ARQ context
        document_id: Database ID of the document
        s3_key: S3 key where the file is stored
        organization_id: Organization ID
        max_tokens: Maximum number of tokens per chunk (default: 128)
    """
    logger.info(
        f"Starting knowledge base document processing for document_id={document_id}, "
        f"s3_key={s3_key}, organization_id={organization_id}"
    )

    temp_file_path = None

    try:
        # Update status to processing
        await db_client.update_document_status(document_id, "processing")

        # Create temp file for download
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        temp_file_path = temp_file.name
        temp_file.close()

        # Download file from S3
        logger.info(f"Downloading file from S3: {s3_key}")
        download_success = await storage_fs.adownload_file(s3_key, temp_file_path)

        if not download_success:
            raise Exception(f"Failed to download file from S3: {s3_key}")

        if not os.path.exists(temp_file_path):
            raise FileNotFoundError(f"Downloaded file not found: {temp_file_path}")

        file_size = os.path.getsize(temp_file_path)
        logger.info(f"Downloaded file size: {file_size} bytes")

        # Compute file hash and get mime type
        file_hash = db_client.compute_file_hash(temp_file_path)
        mime_type = db_client.get_mime_type(temp_file_path)
        filename = s3_key.split("/")[-1]

        # Get document record
        document = await db_client.get_document_by_id(document_id)
        if not document:
            raise Exception(f"Document {document_id} not found")

        # Check if a document with this hash already exists (reject duplicates)
        existing_doc = await db_client.get_document_by_hash(file_hash, organization_id)
        if existing_doc and existing_doc.id != document_id:
            error_message = (
                f"This file is a duplicate of '{existing_doc.filename}'. "
                f"Please delete the duplicate files and consolidate them into a single unique file before uploading."
            )
            logger.warning(
                f"Duplicate document detected: {document_id} is duplicate of {existing_doc.id} "
                f"({existing_doc.filename})"
            )
            # Update file metadata
            await db_client.update_document_metadata(
                document_id,
                file_size_bytes=file_size,
                file_hash=file_hash,
                mime_type=mime_type,
            )
            # Mark as failed with duplicate error message
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

        # Update document with file metadata
        await db_client.update_document_metadata(
            document_id,
            file_size_bytes=file_size,
            file_hash=file_hash,
            mime_type=mime_type,
        )

        # Initialize models for processing
        cache_dir = os.path.expanduser("~/.cache/hf_models")
        logger.info(f"Loading embedding model: {EMBED_MODEL_ID} (cache: {cache_dir})")
        embedding_model = SentenceTransformer(
            EMBED_MODEL_ID,
            cache_folder=cache_dir,
        )

        logger.info(f"Loading tokenizer: {EMBED_MODEL_ID} (cache: {cache_dir})")
        tokenizer = HuggingFaceTokenizer(
            tokenizer=AutoTokenizer.from_pretrained(
                EMBED_MODEL_ID,
                cache_dir=cache_dir,
            ),
            max_tokens=max_tokens,
        )

        logger.info(f"Initializing HybridChunker with max_tokens={max_tokens}")
        chunker = HybridChunker(tokenizer=tokenizer)

        # Convert document with docling
        logger.info("Converting document with docling")
        converter = DocumentConverter()
        conversion_result = converter.convert(temp_file_path)
        doc = conversion_result.document

        # Store docling metadata
        docling_metadata = {
            "num_pages": len(doc.pages) if hasattr(doc, "pages") else None,
            "document_type": type(doc).__name__,
        }

        # Chunk the document
        logger.info(f"Chunking document with max_tokens={max_tokens}")
        chunks = list(chunker.chunk(dl_doc=doc))
        total_chunks = len(chunks)
        logger.info(f"Generated {total_chunks} chunks")

        # Process each chunk
        chunk_texts = []
        chunk_records = []
        token_counts = []

        for i, chunk in enumerate(chunks):
            chunk_text = chunk.text
            contextualized_text = chunker.contextualize(chunk=chunk)

            # Calculate token count
            text_to_tokenize = contextualized_text if contextualized_text else chunk_text
            token_count = len(
                tokenizer.tokenizer.encode(text_to_tokenize, add_special_tokens=False)
            )
            token_counts.append(token_count)

            # Prepare chunk metadata
            chunk_metadata = {}
            if hasattr(chunk, "meta") and chunk.meta:
                chunk_metadata = {
                    "doc_items": (
                        [str(item) for item in chunk.meta.doc_items]
                        if hasattr(chunk.meta, "doc_items")
                        else []
                    ),
                    "headings": (
                        chunk.meta.headings if hasattr(chunk.meta, "headings") else []
                    ),
                }

            # Create chunk record
            chunk_record = KnowledgeBaseChunkModel(
                document_id=document_id,
                organization_id=organization_id,
                chunk_text=chunk_text,
                contextualized_text=contextualized_text,
                chunk_index=i,
                chunk_metadata=chunk_metadata,
                embedding_model=EMBED_MODEL_ID,
                embedding_dimension=EMBEDDING_DIMENSION,
                token_count=token_count,
            )

            chunk_records.append(chunk_record)
            chunk_texts.append(text_to_tokenize)

        # Log chunk statistics
        if token_counts:
            avg_tokens = sum(token_counts) / len(token_counts)
            min_tokens = min(token_counts)
            max_tokens = max(token_counts)
            logger.info(f"Chunk token statistics:")
            logger.info(f"  - Average: {avg_tokens:.1f} tokens")
            logger.info(f"  - Min: {min_tokens} tokens")
            logger.info(f"  - Max: {max_tokens} tokens")

        # Generate embeddings in batch
        logger.info("Generating embeddings")
        embeddings = embedding_model.encode(
            chunk_texts,
            show_progress_bar=False,
            convert_to_numpy=True,
        )

        # Attach embeddings to chunk records
        for chunk_record, embedding in zip(chunk_records, embeddings):
            chunk_record.embedding = embedding.tolist()

        # Save chunks in database
        logger.info("Storing chunks in database")
        await db_client.create_chunks_batch(chunk_records)

        # Update document status to completed
        await db_client.update_document_status(
            document_id,
            "completed",
            total_chunks=total_chunks,
            docling_metadata=docling_metadata,
        )

        logger.info(
            f"Successfully processed knowledge base document {document_id}. "
            f"Total chunks: {total_chunks}"
        )

    except Exception as e:
        logger.error(
            f"Error processing knowledge base document {document_id}: {e}",
            exc_info=True,
        )
        # Update document status to failed
        await db_client.update_document_status(
            document_id, "failed", error_message=str(e)
        )
        raise

    finally:
        # Clean up temp file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.debug(f"Cleaned up temp file: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {temp_file_path}: {e}")
