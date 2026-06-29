"""Dograh-managed embedding service.

Routes embeddings through Dograh's managed proxy (MPS). This mirrors the managed
voice services (``DograhLLMService`` / ``DograhTTSService``): when a server-minted
MPS correlation id is present, it forwards the MPS billing v2 protocol
(``correlation_id`` + ``mps_billing_version``) in the request body so MPS can
authorize and attribute the call. With no correlation id (e.g. a v1 org) it
behaves like a plain OpenAI-compatible call, which MPS accepts.

Keeping this in a subclass keeps ``OpenAIEmbeddingService`` a generic
OpenAI-compatible client; only the managed path carries MPS-specific metadata,
so BYOK OpenAI/Azure requests never ship MPS fields to the real provider.
"""

from typing import Any, Dict, Optional

from api.db.db_client import DBClient

from .openai_service import DEFAULT_MODEL_ID, OpenAIEmbeddingService

# Protocol contract with MPS (see model_services
# api/services/model_service_correlations.py). Kept local to avoid coupling the
# app layer to the pipecat package, which defines its own copy for voice.
MPS_BILLING_VERSION_KEY = "mps_billing_version"
MPS_BILLING_VERSION_V2 = "2"


class DograhEmbeddingService(OpenAIEmbeddingService):
    """OpenAI-compatible embedding client pointed at Dograh's managed proxy."""

    def __init__(
        self,
        db_client: DBClient,
        api_key: Optional[str] = None,
        model_id: str = DEFAULT_MODEL_ID,
        base_url: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ):
        """Initialize the managed embedding service.

        Args:
            db_client: Database client for vector similarity search.
            api_key: Dograh-managed MPS service key.
            model_id: Embedding model/tier id (default: text-embedding-3-small).
            base_url: MPS embeddings base URL.
            correlation_id: Server-minted MPS correlation id. When set, the MPS
                billing v2 protocol is forwarded with each request. When None,
                requests are sent without the protocol (valid for v1 orgs).
        """
        super().__init__(
            db_client=db_client,
            api_key=api_key,
            model_id=model_id,
            base_url=base_url,
        )
        self._correlation_id = correlation_id

    def _request_kwargs(self) -> Dict[str, Any]:
        """Forward the MPS billing v2 protocol when a correlation id is present."""
        if not self._correlation_id:
            return {}
        return {
            "extra_body": {
                "metadata": {
                    "correlation_id": self._correlation_id,
                    MPS_BILLING_VERSION_KEY: MPS_BILLING_VERSION_V2,
                }
            }
        }
