"""OpenAI embedding service.

This module provides document processing capabilities using:
- OpenAI's text-embedding-3-small for embeddings (1536 dimensions)
- Docling for document conversion and chunking
- pgvector for vector similarity search
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from docling.chunking import HybridChunker
from docling.document_converter import DocumentConverter
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from loguru import logger
from openai import AsyncOpenAI
from transformers import AutoTokenizer

from api.db.db_client import DBClient
from api.db.models import KnowledgeBaseChunkModel

from .base import BaseEmbeddingService

# Model configuration
DEFAULT_MODEL_ID = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536  # Dimension for text-embedding-3-small

# For chunking, we'll use the same tokenizer as SentenceTransformer
# since OpenAI uses similar tokenization
TOKENIZER_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class EmbeddingAPIKeyNotConfiguredError(Exception):
    """Raised when OpenAI API key is not configured for embeddings."""

    def __init__(self):
        super().__init__(
            "OpenAI API key not configured. Please set your API key in "
            "Model Configurations > Embedding to use document processing."
        )


class OpenAIEmbeddingService(BaseEmbeddingService):
    """Embedding service using OpenAI's text-embedding-3-small."""

    def __init__(
        self,
        db_client: DBClient,
        api_key: Optional[str] = None,
        model_id: str = DEFAULT_MODEL_ID,
        max_tokens: int = 512,
        base_url: Optional[str] = None,
    ):
        """Initialize the OpenAI embedding service.

        Args:
            db_client: Database client for storing documents and chunks
            api_key: OpenAI API key. If not provided, the client will not be
                    initialized and operations will fail with a clear error.
            model_id: OpenAI embedding model ID (default: text-embedding-3-small)
            max_tokens: Maximum number of tokens per chunk (default: 512)
            base_url: Optional base URL for the API (e.g. for OpenRouter)
        """
        self.db = db_client
        self.model_id = model_id
        self.max_tokens = max_tokens

        # Only initialize OpenAI client if API key is provided
        self._api_key_configured = bool(api_key)
        if self._api_key_configured:
            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            self.client = AsyncOpenAI(**client_kwargs)
            logger.info(f"OpenAI embedding service initialized with model: {model_id}")
        else:
            self.client = None
            logger.warning(
                "OpenAI embedding service initialized without API key. "
                "Operations will fail until API key is configured in Model Configurations."
            )

        # Initialize tokenizer for chunking
        # We use a HuggingFace tokenizer for consistent chunking
        logger.info(
            f"Loading tokenizer for chunking: {TOKENIZER_MODEL} with max_tokens={max_tokens}"
        )
        try:
            self.tokenizer = HuggingFaceTokenizer(
                tokenizer=AutoTokenizer.from_pretrained(
                    TOKENIZER_MODEL,
                    local_files_only=True,
                ),
                max_tokens=max_tokens,
            )
            logger.info("Loaded tokenizer from cache")
        except Exception as e:
            logger.warning(f"Tokenizer not in cache, downloading: {e}")
            self.tokenizer = HuggingFaceTokenizer(
                tokenizer=AutoTokenizer.from_pretrained(TOKENIZER_MODEL),
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

    def _ensure_api_key_configured(self):
        """Check if API key is configured and raise error if not."""
        if not self._api_key_configured or self.client is None:
            raise EmbeddingAPIKeyNotConfiguredError()

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts using OpenAI API.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors (each vector is a list of floats)

        Raises:
            EmbeddingAPIKeyNotConfiguredError: If API key is not configured
        """
        self._ensure_api_key_configured()

        try:
            # OpenAI API call
            response = await self.client.embeddings.create(
                input=texts,
                model=self.model_id,
            )

            # Extract embeddings from response
            embeddings = [item.embedding for item in response.data]
            return embeddings

        except Exception as e:
            logger.error(f"Error generating OpenAI embeddings: {e}")
            raise

    async def embed_query(self, query: str) -> List[float]:
        """Embed a single query text using OpenAI API.

        Args:
            query: Query text to embed

        Returns:
            Embedding vector as list of floats

        Raises:
            EmbeddingAPIKeyNotConfiguredError: If API key is not configured
        """
        self._ensure_api_key_configured()
        embeddings = await self.embed_texts([query])
        return embeddings[0]

    async def search_similar_chunks(
        self,
        query: str,
        organization_id: int,
        limit: int = 5,
        document_uuids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar chunks using vector similarity.

        Args:
            query: Search query text
            organization_id: Organization ID for scoping
            limit: Maximum number of results to return
            document_uuids: Optional list of document UUIDs to filter by

        Returns:
            List of dictionaries with chunk data and similarity scores

        Raises:
            EmbeddingAPIKeyNotConfiguredError: If API key is not configured
        """
        self._ensure_api_key_configured()

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

            logger.info(f"Processing document with OpenAI embeddings: {filename}")

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

                # Get contextualized text
                contextualized_text = self.chunker.contextualize(chunk=chunk)

                # Calculate token count
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

            # Step 4: Generate embeddings using OpenAI API
            logger.info(f"Generating embeddings using OpenAI ({self.model_id})...")
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
            logger.info(f"  - Embedding model: {self.model_id}")
            logger.info(f"  - Document ID: {doc_record.id}")
            logger.info(f"  - Document UUID: {doc_record.document_uuid}")

            return doc_record

        except Exception as e:
            logger.error(f"Error processing document with OpenAI: {e}")

            # Update document status to failed if it exists
            if "doc_record" in locals():
                await self.db.update_document_status(
                    doc_record.id, "failed", error_message=str(e)
                )

            raise
