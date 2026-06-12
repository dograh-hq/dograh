from __future__ import annotations

from typing import Any

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.registry import ServiceProviders

MPS_CORRELATION_ID_CONTEXT_KEY = "mps_correlation_id"


def uses_managed_model_services_v2(
    ai_model_config: EffectiveAIModelConfiguration | None,
) -> bool:
    if (
        ai_model_config is None
        or getattr(ai_model_config, "managed_service_version", None) != 2
    ):
        return False

    return any(
        _is_dograh_service(getattr(ai_model_config, section_name, None))
        for section_name in ("llm", "tts", "stt", "embeddings")
    )


def get_mps_correlation_id(initial_context: dict[str, Any] | None) -> str | None:
    if not initial_context:
        return None
    correlation_id = initial_context.get(MPS_CORRELATION_ID_CONTEXT_KEY)
    if correlation_id is None:
        return None
    return str(correlation_id)


async def ensure_mps_correlation_id(
    *,
    ai_model_config: EffectiveAIModelConfiguration,
    workflow_run_id: int,
    initial_context: dict[str, Any] | None,
) -> str | None:
    existing = get_mps_correlation_id(initial_context)
    if existing:
        return existing

    if not uses_managed_model_services_v2(ai_model_config):
        return None

    raise ValueError(
        "Managed model services v2 requires workflow run authorization before "
        f"the run starts. Missing correlation id for workflow_run_id={workflow_run_id}."
    )


def _is_dograh_service(service: Any) -> bool:
    provider = getattr(service, "provider", None)
    return (
        provider == ServiceProviders.DOGRAH or provider == ServiceProviders.DOGRAH.value
    )


def get_dograh_service_api_key(
    ai_model_config: EffectiveAIModelConfiguration,
) -> str | None:
    for section_name in ("llm", "tts", "stt", "embeddings"):
        service = getattr(ai_model_config, section_name, None)
        if not _is_dograh_service(service):
            continue

        if hasattr(service, "get_all_api_keys"):
            keys = service.get_all_api_keys()
            if keys:
                return keys[0]

        api_key = getattr(service, "api_key", None)
        if isinstance(api_key, str) and api_key:
            return api_key

    return None
