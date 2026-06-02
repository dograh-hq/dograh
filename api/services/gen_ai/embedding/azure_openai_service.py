"""Azure OpenAI embedding service.

Uses the Azure OpenAI REST API for text embeddings, compatible with
1536-dimensional embedding deployments such as text-embedding-3-small and
text-embedding-ada-002.
"""

from typing import Any, Dict, List, Optional

from loguru import logger
from openai import AsyncAzureOpenAI

from api.db.db_client import DBClient
from api.utils.url_security import validate_user_configured_service_url

from .base import BaseEmbeddingService

DEFAULT_MODEL_ID = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536


class AzureEmbeddingAPIKeyNotConfiguredError(Exception):
    """Raised when Azure OpenAI credentials are not configured for embeddings."""

    def __init__(self):
        super().__init__(
            "Azure OpenAI endpoint or API key not configured. Please set your "
            "endpoint and API key in Model Configurations > Embedding to use "
            "document processing."
        )


class AzureOpenAIEmbeddingService(BaseEmbeddingService):
    """Embedding service using Azure OpenAI text-embedding deployments."""

    def __init__(
        self,
        db_client: DBClient,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model_id: str = DEFAULT_MODEL_ID,
        api_version: str = "2024-02-15-preview",
    ):
        """Initialize the Azure OpenAI embedding service.

        Args:
            db_client: Database client for vector similarity search.
            api_key: Azure OpenAI API key.
            endpoint: Azure OpenAI resource endpoint (e.g. https://<resource>.openai.azure.com).
            model_id: Deployment name, used as both the deployment and model identifier.
            api_version: Azure OpenAI API version.
        """
        self.db = db_client
        self.model_id = model_id

        self._configured = bool(api_key and endpoint)
        if self._configured:
            validate_user_configured_service_url(endpoint, field_name="endpoint")
            self.client = AsyncAzureOpenAI(
                api_key=api_key,
                azure_endpoint=endpoint,
                api_version=api_version,
            )
            logger.info(
                f"Azure OpenAI embedding service initialized with deployment: {model_id}"
            )
        else:
            self.client = None
            logger.warning(
                "Azure OpenAI embedding service initialized without credentials. "
                "Operations will fail until endpoint and API key are configured."
            )

    def get_model_id(self) -> str:
        return self.model_id

    def get_embedding_dimension(self) -> int:
        return EMBEDDING_DIMENSION

    def _ensure_configured(self):
        if not self._configured or self.client is None:
            raise AzureEmbeddingAPIKeyNotConfiguredError()

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts using Azure OpenAI API."""
        self._ensure_configured()
        try:
            response = await self.client.embeddings.create(
                input=texts,
                model=self.model_id,
            )
            embeddings = [item.embedding for item in response.data]
            self._validate_embedding_dimensions(embeddings)
            return embeddings
        except Exception as e:
            logger.error(f"Error generating Azure OpenAI embeddings: {e}")
            raise

    def _validate_embedding_dimensions(self, embeddings: List[List[float]]) -> None:
        for embedding in embeddings:
            if len(embedding) != EMBEDDING_DIMENSION:
                raise ValueError(
                    "Azure OpenAI embedding deployment "
                    f"{self.model_id!r} returned {len(embedding)} dimensions; "
                    "Dograh knowledge base storage currently supports "
                    f"{EMBEDDING_DIMENSION}-dimensional embeddings."
                )

    async def embed_query(self, query: str) -> List[float]:
        """Embed a single query text using Azure OpenAI API."""
        self._ensure_configured()
        embeddings = await self.embed_texts([query])
        return embeddings[0]

    async def search_similar_chunks(
        self,
        query: str,
        organization_id: int,
        limit: int = 5,
        document_uuids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar chunks using vector similarity."""
        self._ensure_configured()
        query_embedding = await self.embed_query(query)
        return await self.db.search_similar_chunks(
            query_embedding=query_embedding,
            organization_id=organization_id,
            limit=limit,
            document_uuids=document_uuids,
            embedding_model=self.model_id,
        )
