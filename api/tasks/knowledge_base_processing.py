"""ARQ background task for processing knowledge base documents."""

import json
import os
import tempfile

from docling.chunking import HybridChunker
from docling.document_converter import DocumentConverter
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from loguru import logger
from transformers import AutoTokenizer

from api.db import db_client
from api.db.models import KnowledgeBaseChunkModel
from api.services.gen_ai import OpenAIEmbeddingService
from api.services.storage import storage_fs

# For tokenization/chunking
TOKENIZER_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


async def process_knowledge_base_document(
    ctx,
    document_id: int,
    s3_key: str,
    organization_id: int,
    max_tokens: int = 128,
    retrieval_mode: str = "chunked",
):
    """Process a knowledge base document: download, chunk, embed, and store.

    Args:
        ctx: ARQ context
        document_id: Database ID of the document
        s3_key: S3 key where the file is stored
        organization_id: Organization ID
        max_tokens: Maximum number of tokens per chunk (default: 128)
        retrieval_mode: "chunked" for vector search or "full_document" for full text
    """
    logger.info(
        f"Starting knowledge base document processing for document_id={document_id}, "
        f"s3_key={s3_key}, organization_id={organization_id}"
    )

    temp_file_path = None

    try:
        # Update status to processing
        await db_client.update_document_status(document_id, "processing")

        # Extract file extension from S3 key
        filename = s3_key.split("/")[-1]
        file_extension = (
            os.path.splitext(filename)[1] or ".bin"
        )  # Default to .bin if no extension

        # Create temp file for download with correct extension
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
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

        # Validate file size (max 5MB)
        max_file_size = 5 * 1024 * 1024
        if file_size > max_file_size:
            error_message = f"File size ({file_size / (1024 * 1024):.1f}MB) exceeds the maximum allowed size of 5MB."
            logger.warning(f"Document {document_id}: {error_message}")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

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

        # Full document mode: extract text and store it, skip chunking/embedding
        if retrieval_mode == "full_document":
            logger.info(f"Document {document_id}: full_document mode, extracting text")

            plain_text_extensions = {".txt", ".json"}
            if file_extension.lower() in plain_text_extensions:
                with open(temp_file_path, "r", encoding="utf-8") as f:
                    full_text = f.read()
                if file_extension.lower() == ".json":
                    try:
                        parsed = json.loads(full_text)
                        full_text = json.dumps(parsed, indent=2, ensure_ascii=False)
                    except json.JSONDecodeError:
                        pass
                docling_metadata = {"document_type": "PlainText"}
            else:
                converter = DocumentConverter()
                conversion_result = converter.convert(temp_file_path)
                doc = conversion_result.document
                full_text = doc.export_to_text()
                docling_metadata = {
                    "num_pages": len(doc.pages) if hasattr(doc, "pages") else None,
                    "document_type": type(doc).__name__,
                }

            # Store full text on the document record
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

        # Initialize the OpenAI embedding service
        logger.info(
            f"Initializing OpenAI embedding service with max_tokens={max_tokens}"
        )
        # Try to get user's embeddings configuration
        embeddings_api_key = None
        embeddings_model = None
        embeddings_base_url = None
        if document.created_by:
            user_config = await db_client.get_user_configurations(document.created_by)
            if user_config.embeddings:
                embeddings_api_key = user_config.embeddings.api_key
                embeddings_model = user_config.embeddings.model
                embeddings_base_url = getattr(user_config.embeddings, "base_url", None)
                logger.info(f"Using user embeddings config: model={embeddings_model}")

        # Check if API key is configured
        if not embeddings_api_key:
            error_message = (
                "OpenAI API key not configured. Please set your API key in "
                "Model Configurations > Embedding to process documents."
            )
            logger.warning(f"Document {document_id}: {error_message}")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

        service = OpenAIEmbeddingService(
            db_client=db_client,
            max_tokens=max_tokens,
            api_key=embeddings_api_key,
            model_id=embeddings_model or "text-embedding-3-small",
            base_url=embeddings_base_url,
        )

        # Step 1: Initialize tokenizer for chunking
        logger.info(
            f"Loading tokenizer: {TOKENIZER_MODEL} with max_tokens={max_tokens}"
        )
        hf_tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_MODEL)
        tokenizer = HuggingFaceTokenizer(
            tokenizer=hf_tokenizer,
            max_tokens=max_tokens,
        )

        chunk_texts = []
        chunk_records = []
        token_counts = []

        # Check if file is a plain text format that docling doesn't support
        plain_text_extensions = {".txt", ".json"}
        if file_extension.lower() in plain_text_extensions:
            # Read text content directly
            logger.info(f"Reading {file_extension} file directly (bypassing docling)")
            with open(temp_file_path, "r", encoding="utf-8") as f:
                raw_content = f.read()

            # For JSON files, pretty-print for better readability
            if file_extension.lower() == ".json":
                try:
                    parsed = json.loads(raw_content)
                    raw_content = json.dumps(parsed, indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    logger.warning(
                        "JSON file is not valid JSON, treating as plain text"
                    )

            docling_metadata = {
                "num_pages": None,
                "document_type": "PlainText",
            }

            # Token-based chunking for plain text
            tokens = hf_tokenizer.encode(raw_content, add_special_tokens=False)
            total_tokens = len(tokens)
            logger.info(
                f"Total tokens in file: {total_tokens}, chunking with max_tokens={max_tokens}"
            )

            start = 0
            chunk_index = 0
            while start < total_tokens:
                end = min(start + max_tokens, total_tokens)
                chunk_token_ids = tokens[start:end]
                chunk_text = hf_tokenizer.decode(
                    chunk_token_ids, skip_special_tokens=True
                )

                token_count = len(chunk_token_ids)
                token_counts.append(token_count)

                chunk_record = KnowledgeBaseChunkModel(
                    document_id=document_id,
                    organization_id=organization_id,
                    chunk_text=chunk_text,
                    contextualized_text=chunk_text,
                    chunk_index=chunk_index,
                    chunk_metadata={},
                    embedding_model=service.get_model_id(),
                    embedding_dimension=service.get_embedding_dimension(),
                    token_count=token_count,
                )

                chunk_records.append(chunk_record)
                chunk_texts.append(chunk_text)
                chunk_index += 1
                start = end

            total_chunks = len(chunk_records)
            logger.info(f"Generated {total_chunks} chunks from plain text")

        else:
            # Use docling for structured formats (PDF, DOCX, etc.)
            logger.info("Converting document with docling")
            converter = DocumentConverter()
            conversion_result = converter.convert(temp_file_path)
            doc = conversion_result.document

            docling_metadata = {
                "num_pages": len(doc.pages) if hasattr(doc, "pages") else None,
                "document_type": type(doc).__name__,
            }

            # Initialize chunker
            logger.info(f"Initializing HybridChunker with max_tokens={max_tokens}")
            chunker = HybridChunker(tokenizer=tokenizer)

            # Chunk the document
            logger.info(f"Chunking document with max_tokens={max_tokens}")
            chunks = list(chunker.chunk(dl_doc=doc))
            total_chunks = len(chunks)
            logger.info(f"Generated {total_chunks} chunks")

            # Process each chunk
            for i, chunk in enumerate(chunks):
                chunk_text = chunk.text
                contextualized_text = chunker.contextualize(chunk=chunk)

                text_to_tokenize = (
                    contextualized_text if contextualized_text else chunk_text
                )
                token_count = len(
                    tokenizer.tokenizer.encode(
                        text_to_tokenize, add_special_tokens=False
                    )
                )
                token_counts.append(token_count)

                chunk_metadata = {}
                if hasattr(chunk, "meta") and chunk.meta:
                    chunk_metadata = {
                        "doc_items": (
                            [str(item) for item in chunk.meta.doc_items]
                            if hasattr(chunk.meta, "doc_items")
                            else []
                        ),
                        "headings": (
                            chunk.meta.headings
                            if hasattr(chunk.meta, "headings")
                            else []
                        ),
                    }

                chunk_record = KnowledgeBaseChunkModel(
                    document_id=document_id,
                    organization_id=organization_id,
                    chunk_text=chunk_text,
                    contextualized_text=contextualized_text,
                    chunk_index=i,
                    chunk_metadata=chunk_metadata,
                    embedding_model=service.get_model_id(),
                    embedding_dimension=service.get_embedding_dimension(),
                    token_count=token_count,
                )

                chunk_records.append(chunk_record)
                chunk_texts.append(text_to_tokenize)

        # Log chunk statistics
        if token_counts:
            avg_tokens = sum(token_counts) / len(token_counts)
            min_tokens = min(token_counts)
            max_tokens_actual = max(token_counts)
            logger.info("Chunk token statistics:")
            logger.info(f"  - Average: {avg_tokens:.1f} tokens")
            logger.info(f"  - Min: {min_tokens} tokens")
            logger.info(f"  - Max: {max_tokens_actual} tokens")

        # Step 6: Generate embeddings using OpenAI
        logger.info(f"Generating embeddings using {service.get_model_id()}")
        embeddings = await service.embed_texts(chunk_texts)

        # Step 7: Attach embeddings to chunk records
        for chunk_record, embedding in zip(chunk_records, embeddings):
            chunk_record.embedding = embedding

        # Step 8: Save chunks in database
        logger.info("Storing chunks in database")
        await db_client.create_chunks_batch(chunk_records)

        # Step 9: Update document status to completed
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
