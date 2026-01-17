"""Base class for embedding services."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseEmbeddingService(ABC):
    """Abstract base class for embedding services.

    All embedding services (SentenceTransformer, OpenAI, etc.) should inherit from this class
    and implement the required methods.
    """

    @abstractmethod
    def get_model_id(self) -> str:
        """Return the model identifier.

        Returns:
            String identifier for the model (e.g., 'sentence-transformers/all-MiniLM-L6-v2')
        """
        pass

    @abstractmethod
    def get_embedding_dimension(self) -> int:
        """Return the embedding dimension.

        Returns:
            Integer dimension of the embedding vectors
        """
        pass

    @abstractmethod
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors (each vector is a list of floats)
        """
        pass

    @abstractmethod
    async def embed_query(self, query: str) -> List[float]:
        """Embed a single query text.

        Args:
            query: Query text to embed

        Returns:
            Embedding vector as list of floats
        """
        pass

    @abstractmethod
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
            List of dictionaries containing chunk data and similarity scores
        """
        pass
