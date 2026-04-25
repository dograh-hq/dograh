"""OpenAI embedding service.

Embeds text and performs vector similarity search via the local database.
Document conversion and chunking now live in the Model Proxy Service (MPS);
this file no longer pulls docling/transformers.
"""

from typing import Any, Dict, List, Optional

from loguru import logger
from openai import AsyncOpenAI

from api.db.db_client import DBClient

from .base import BaseEmbeddingService

DEFAULT_MODEL_ID = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536  # Dimension for text-embedding-3-small


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
        base_url: Optional[str] = None,
    ):
        """Initialize the OpenAI embedding service.

        Args:
            db_client: Database client for vector similarity search.
            api_key: OpenAI API key. If not provided, the client will not be
                initialized and operations will fail with a clear error.
            model_id: OpenAI embedding model ID (default: text-embedding-3-small).
            base_url: Optional base URL for the API (e.g. for OpenRouter).
        """
        self.db = db_client
        self.model_id = model_id

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

        Raises:
            EmbeddingAPIKeyNotConfiguredError: If API key is not configured.
        """
        self._ensure_api_key_configured()

        try:
            response = await self.client.embeddings.create(
                input=texts,
                model=self.model_id,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error(f"Error generating OpenAI embeddings: {e}")
            raise

    async def embed_query(self, query: str) -> List[float]:
        """Embed a single query text using OpenAI API.

        Raises:
            EmbeddingAPIKeyNotConfiguredError: If API key is not configured.
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

        Raises:
            EmbeddingAPIKeyNotConfiguredError: If API key is not configured.
        """
        self._ensure_api_key_configured()

        query_embedding = await self.embed_query(query)

        return await self.db.search_similar_chunks(
            query_embedding=query_embedding,
            organization_id=organization_id,
            limit=limit,
            document_uuids=document_uuids,
            embedding_model=self.model_id,
        )
