"""Sentence Transformer embedding service.

This module provides document processing capabilities using:
- Sentence-transformers for embeddings (all-MiniLM-L6-v2)
- Docling for document conversion and chunking
- pgvector for vector similarity search

Setup for offline usage:
1. First run: Downloads and caches models to ~/.cache/sentence_transformers
2. Subsequent runs: Uses cached models (no internet needed)
3. For fully offline mode: Set TRANSFORMERS_OFFLINE=1 and HF_HUB_OFFLINE=1
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from docling.chunking import HybridChunker
from docling.document_converter import DocumentConverter
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from loguru import logger
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer

from api.db.db_client import DBClient
from api.db.models import KnowledgeBaseChunkModel

from .base import BaseEmbeddingService

# Set environment variables for model caching
os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")
os.environ.setdefault("HF_HUB_OFFLINE", "0")
os.environ.setdefault(
    "SENTENCE_TRANSFORMERS_HOME", os.path.expanduser("~/.cache/sentence_transformers")
)

# Model configuration
DEFAULT_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384  # Dimension for all-MiniLM-L6-v2


class SentenceTransformerEmbeddingService(BaseEmbeddingService):
    """Embedding service using Sentence Transformers."""

    def __init__(
        self,
        db_client: DBClient,
        model_id: str = DEFAULT_MODEL_ID,
        max_tokens: int = 512,
    ):
        """Initialize the Sentence Transformer embedding service.

        Args:
            db_client: Database client for storing documents and chunks
            model_id: Sentence-transformers model ID (default: all-MiniLM-L6-v2)
            max_tokens: Maximum number of tokens per chunk (default: 512)
                Note: This applies to the contextualized text (with headings/captions)
        """
        self.db = db_client
        self.model_id = model_id
        self.max_tokens = max_tokens

        # Initialize embedding model
        logger.info(f"Loading embedding model: {model_id}")
        try:
            # Try to load from cache first (local_files_only=True)
            self.embedding_model = SentenceTransformer(
                model_id,
                cache_folder=os.environ.get("SENTENCE_TRANSFORMERS_HOME"),
                local_files_only=True,
            )
            logger.info("Loaded model from cache")
        except Exception as e:
            logger.warning(f"Model not in cache, downloading: {e}")
            # If not in cache, download it (this will cache it for next time)
            self.embedding_model = SentenceTransformer(
                model_id,
                cache_folder=os.environ.get("SENTENCE_TRANSFORMERS_HOME"),
            )
            logger.info("Model downloaded and cached")

        # Initialize tokenizer for chunking with max_tokens
        logger.info(f"Loading tokenizer: {model_id} with max_tokens={max_tokens}")
        try:
            # Try to load from cache first
            self.tokenizer = HuggingFaceTokenizer(
                tokenizer=AutoTokenizer.from_pretrained(
                    model_id,
                    local_files_only=True,
                ),
                max_tokens=max_tokens,
            )
            logger.info("Loaded tokenizer from cache")
        except Exception as e:
            logger.warning(f"Tokenizer not in cache, downloading: {e}")
            # If not in cache, download it
            self.tokenizer = HuggingFaceTokenizer(
                tokenizer=AutoTokenizer.from_pretrained(model_id),
                max_tokens=max_tokens,
            )
            logger.info("Tokenizer downloaded and cached")

        # Initialize chunker
        logger.info(f"Initializing HybridChunker with max_tokens={max_tokens}")
        self.chunker = HybridChunker(tokenizer=self.tokenizer)

        # Initialize document converter
        self.converter = DocumentConverter()

    def get_model_id(self) -> str:
        """Return the model identifier."""
        return self.model_id

    def get_embedding_dimension(self) -> int:
        """Return the embedding dimension."""
        return EMBEDDING_DIMENSION

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors (each vector is a list of floats)
        """
        embeddings = self.embedding_model.encode(
            texts,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [embedding.tolist() for embedding in embeddings]

    async def embed_query(self, query: str) -> List[float]:
        """Embed a single query text.

        Args:
            query: Query text to embed

        Returns:
            Embedding vector as list of floats
        """
        embedding = self.embedding_model.encode([query])[0]
        return embedding.tolist()

    async def search_similar_chunks(
        self,
        query: str,
        organization_id: int,
        limit: int = 5,
        document_uuids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar chunks using vector similarity.

        Returns top-k most similar chunks without any threshold filtering.
        Apply similarity thresholds and reranking at the application layer.

        Args:
            query: Search query text
            organization_id: Organization ID for scoping
            limit: Maximum number of results to return
            document_uuids: Optional list of document UUIDs to filter by

        Returns:
            List of dictionaries with chunk data and similarity scores
        """
        # Generate query embedding
        query_embedding = await self.embed_query(query)

        # Perform vector similarity search
        results = await self.db.search_similar_chunks(
            query_embedding=query_embedding,
            organization_id=organization_id,
            limit=limit,
            document_uuids=document_uuids,
            embedding_model=self.model_id,
        )

        return results

    async def process_document(
        self,
        file_path: str,
        organization_id: int,
        created_by: int,
        custom_metadata: dict = None,
    ):
        """Process a document: convert, chunk, embed, and store in database.

        Args:
            file_path: Path to the document file
            organization_id: Organization ID for scoping
            created_by: User ID who uploaded the document
            custom_metadata: Optional custom metadata dictionary

        Returns:
            The created document record
        """
        try:
            # Extract file metadata
            filename = Path(file_path).name
            file_hash = self.db.compute_file_hash(file_path)
            file_size = os.path.getsize(file_path)
            mime_type = self.db.get_mime_type(file_path)

            # Check if document already exists
            existing_doc = await self.db.get_document_by_hash(
                file_hash, organization_id
            )
            if existing_doc:
                logger.info(f"Document already exists: {filename} (hash: {file_hash})")
                return existing_doc

            # Create document record
            doc_record = await self.db.create_document(
                organization_id=organization_id,
                created_by=created_by,
                filename=filename,
                file_size_bytes=file_size,
                file_hash=file_hash,
                mime_type=mime_type,
                custom_metadata=custom_metadata or {},
            )

            logger.info(f"Processing document: {filename}")

            # Update status to processing
            await self.db.update_document_status(doc_record.id, "processing")

            # Step 1: Convert document using docling
            logger.info("Converting document with docling...")
            conversion_result = self.converter.convert(file_path)
            doc = conversion_result.document

            # Store docling metadata
            docling_metadata = {
                "num_pages": len(doc.pages) if hasattr(doc, "pages") else None,
                "document_type": type(doc).__name__,
            }

            # Step 2: Chunk the document
            logger.info(f"Chunking document with max_tokens={self.max_tokens}...")
            chunks = list(self.chunker.chunk(dl_doc=doc))
            total_chunks = len(chunks)

            logger.info(f"Generated {total_chunks} chunks")

            # Step 3: Process each chunk
            chunk_texts = []
            chunk_records = []
            token_counts = []

            for i, chunk in enumerate(chunks):
                # Get chunk text
                chunk_text = chunk.text

                # Get contextualized text (enriched with surrounding context)
                contextualized_text = self.chunker.contextualize(chunk=chunk)

                # Calculate actual token count using the tokenizer
                text_to_tokenize = (
                    contextualized_text if contextualized_text else chunk_text
                )
                token_count = len(
                    self.tokenizer.tokenizer.encode(
                        text_to_tokenize, add_special_tokens=False
                    )
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
                            chunk.meta.headings
                            if hasattr(chunk.meta, "headings")
                            else []
                        ),
                    }

                # Create chunk record (without embedding yet)
                chunk_record = KnowledgeBaseChunkModel(
                    document_id=doc_record.id,
                    organization_id=organization_id,
                    chunk_text=chunk_text,
                    contextualized_text=contextualized_text,
                    chunk_index=i,
                    chunk_metadata=chunk_metadata,
                    embedding_model=self.model_id,
                    embedding_dimension=EMBEDDING_DIMENSION,
                    token_count=token_count,
                )

                chunk_records.append(chunk_record)
                # Use contextualized text for embedding if available
                chunk_texts.append(text_to_tokenize)

            # Log chunk statistics
            if token_counts:
                avg_tokens = sum(token_counts) / len(token_counts)
                min_tokens = min(token_counts)
                max_tokens = max(token_counts)
                logger.info("Chunk token statistics:")
                logger.info(f"  - Average: {avg_tokens:.1f} tokens")
                logger.info(f"  - Min: {min_tokens} tokens")
                logger.info(f"  - Max: {max_tokens} tokens")

            # Step 4: Generate embeddings in batch
            logger.info("Generating embeddings...")
            embeddings = await self.embed_texts(chunk_texts)

            # Step 5: Attach embeddings to chunk records
            for chunk_record, embedding in zip(chunk_records, embeddings):
                chunk_record.embedding = embedding

            # Step 6: Save all chunks in batch
            logger.info("Storing chunks in database...")
            await self.db.create_chunks_batch(chunk_records)

            # Update document status to completed
            await self.db.update_document_status(
                doc_record.id,
                "completed",
                total_chunks=total_chunks,
                docling_metadata=docling_metadata,
            )

            logger.info(f"Successfully processed document: {filename}")
            logger.info(f"  - Total chunks: {total_chunks}")
            logger.info(f"  - Document ID: {doc_record.id}")
            logger.info(f"  - Document UUID: {doc_record.document_uuid}")

            return doc_record

        except Exception as e:
            logger.error(f"Error processing document: {e}")

            # Update document status to failed if it exists
            if "doc_record" in locals():
                await self.db.update_document_status(
                    doc_record.id, "failed", error_message=str(e)
                )

            raise
