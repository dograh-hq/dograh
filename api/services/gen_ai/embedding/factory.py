"""Factory for embedding services, including the Dograh-managed (MPS) path.

Centralizes the provider branching (Azure BYOK / Dograh-managed / OpenAI-compatible
BYOK) that was previously duplicated across document ingestion, the search route,
and the RAG tool, and resolves the MPS correlation id the same way the voice
path does.
"""

from typing import Optional

from loguru import logger

from api.db.db_client import DBClient

from .azure_openai_service import AzureOpenAIEmbeddingService
from .base import BaseEmbeddingService
from .dograh_service import DograhEmbeddingService
from .openai_service import OpenAIEmbeddingService

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_AZURE_API_VERSION = "2024-02-15-preview"


async def resolve_embedding_correlation_id(
    *,
    service_key: Optional[str],
) -> Optional[str]:
    """Mint an MPS correlation id for a managed embedding call made outside a run.

    Matches the voice path's ``_authorize_oss_managed_v2_correlation``: the
    correlation is minted via the bearer service-key endpoint, so it works for
    hosted orgs and OSS keys alike. Returns ``None`` when minting fails; MPS
    accepts un-correlated embedding calls.
    """
    if not service_key:
        return None

    # Imported lazily to avoid import-time cycles between the gen_ai and service
    # layers (matches the inline-import convention used elsewhere in the app).
    from api.services.mps_service_key_client import mps_service_key_client

    try:
        minted = await mps_service_key_client.create_correlation_id(
            service_key=service_key
        )
        return minted.get("correlation_id")
    except Exception as e:
        logger.warning(
            "Could not resolve MPS correlation id for managed embeddings; "
            "sending without it: {}",
            e,
        )
        return None


async def build_embedding_service(
    *,
    db_client: DBClient,
    provider: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    base_url: Optional[str] = None,
    endpoint: Optional[str] = None,
    api_version: Optional[str] = None,
    correlation_id: Optional[str] = None,
    resolve_correlation: bool = False,
) -> BaseEmbeddingService:
    """Construct the right embedding service for a provider/config.

    Args:
        correlation_id: A correlation id already available in context (e.g. the
            running workflow's MPS correlation id). Used for the Dograh provider.
        resolve_correlation: When True and no ``correlation_id`` is supplied, resolve
            one for the Dograh provider via ``resolve_embedding_correlation_id``
            (for calls made outside a workflow run: ingestion, manual search).
    """
    from api.services.configuration.registry import ServiceProviders

    model_id = model or DEFAULT_EMBEDDING_MODEL

    if provider == ServiceProviders.AZURE.value and endpoint:
        return AzureOpenAIEmbeddingService(
            db_client=db_client,
            api_key=api_key,
            endpoint=endpoint,
            model_id=model_id,
            api_version=api_version or DEFAULT_AZURE_API_VERSION,
        )

    if provider == ServiceProviders.DOGRAH.value:
        cid = correlation_id
        if cid is None and resolve_correlation:
            cid = await resolve_embedding_correlation_id(service_key=api_key)
        return DograhEmbeddingService(
            db_client=db_client,
            api_key=api_key,
            model_id=model_id,
            base_url=base_url,
            correlation_id=cid,
        )

    return OpenAIEmbeddingService(
        db_client=db_client,
        api_key=api_key,
        model_id=model_id,
        base_url=base_url,
    )
