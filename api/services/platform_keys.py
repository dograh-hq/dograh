"""Platform master keys cache and resolution service.

Maintains an in-memory cache of master provider keys configured by Superadmins in
the database, allowing synchronous resolution during Pipecat service construction
without async DB query overhead or extra proxy network latency.
"""

from typing import Any, Dict, Optional, Tuple
from loguru import logger

# Structure: {service_type: {provider: {"api_key": str, "is_default": bool, "pricing": dict}}}
_MASTER_KEYS_CACHE: Dict[str, Dict[str, Dict[str, Any]]] = {
    "llm": {},
    "stt": {},
    "tts": {},
}


async def refresh_master_keys_cache() -> None:
    """Load or reload all active platform master keys from the database into memory."""
    from api.db import db_client

    try:
        keys = await db_client.list_master_keys()
        new_cache: Dict[str, Dict[str, Dict[str, Any]]] = {
            "llm": {},
            "stt": {},
            "tts": {},
        }
        for k in keys:
            if not k.is_active:
                continue
            st = k.service_type.lower()
            prov = k.provider.lower()
            if st not in new_cache:
                new_cache[st] = {}
            new_cache[st][prov] = {
                "api_key": k.api_key,
                "is_default": k.is_default,
                "default_model": getattr(k, "default_model", None),
                "default_voice": getattr(k, "default_voice", None),
                "models_pricing": k.models_pricing or {},
            }

        global _MASTER_KEYS_CACHE
        _MASTER_KEYS_CACHE = new_cache
        logger.info("Refreshed platform master keys cache: {} keys loaded", len(keys))
    except Exception as e:
        logger.warning("Failed to refresh platform master keys cache: {}", e)


def get_platform_master_key(service_type: str, provider: str) -> Optional[str]:
    """Resolve an API key for a specific provider from the platform master keys cache."""
    st = service_type.lower()
    prov = provider.lower()
    entry = _MASTER_KEYS_CACHE.get(st, {}).get(prov)
    if entry and entry.get("api_key"):
        return entry["api_key"]
    return None


def get_platform_default_model(service_type: str, provider: str) -> Optional[str]:
    """Get the default model configured for a specific platform provider."""
    st = service_type.lower()
    prov = provider.lower()
    entry = _MASTER_KEYS_CACHE.get(st, {}).get(prov)
    if entry:
        return entry.get("default_model")
    return None


def get_platform_default_voice(service_type: str, provider: str) -> Optional[str]:
    """Get the default voice configured for a specific platform provider."""
    st = service_type.lower()
    prov = provider.lower()
    entry = _MASTER_KEYS_CACHE.get(st, {}).get(prov)
    if entry:
        return entry.get("default_voice")
    return None


def get_default_platform_provider(service_type: str) -> Optional[Tuple[str, str]]:
    """Return (provider, api_key) for the marked default master provider of a service type."""
    st = service_type.lower()
    providers_map = _MASTER_KEYS_CACHE.get(st, {})
    for prov, data in providers_map.items():
        if data.get("is_default") and data.get("api_key"):
            return prov, data["api_key"]
    # If no default explicitly marked, return the first available active provider
    if providers_map:
        first_prov = next(iter(providers_map))
        return first_prov, providers_map[first_prov]["api_key"]
    return None


def get_default_platform_provider_and_model(
    service_type: str,
) -> Optional[Tuple[str, str, Optional[str]]]:
    """Return (provider, api_key, default_model) for the marked default master provider of a service type."""
    st = service_type.lower()
    providers_map = _MASTER_KEYS_CACHE.get(st, {})
    for prov, data in providers_map.items():
        if data.get("is_default") and data.get("api_key"):
            return prov, data["api_key"], data.get("default_model")
    if providers_map:
        first_prov = next(iter(providers_map))
        return (
            first_prov,
            providers_map[first_prov]["api_key"],
            providers_map[first_prov].get("default_model"),
        )
    return None


def get_model_rate_per_minute(service_type: str, provider: str, model: str) -> float:
    """Get the configured rate per minute for a model, or return a sensible default."""
    defaults = {
        "llm": 0.01,
        "stt": 0.01,
        "tts": 0.02,
    }
    st = service_type.lower()
    prov = provider.lower()
    entry = _MASTER_KEYS_CACHE.get(st, {}).get(prov, {})
    pricing = entry.get("models_pricing", {})

    # Check model-specific rate
    if model in pricing:
        val = pricing[model]
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, dict) and "price_per_minute_usd" in val:
            return float(val["price_per_minute_usd"])

    # Fallback to provider default if defined
    if "default" in pricing:
        val = pricing["default"]
        if isinstance(val, (int, float)):
            return float(val)

    return defaults.get(st, 0.01)


def calculate_run_composite_rate(
    llm_provider: str = "",
    llm_model: str = "",
    stt_provider: str = "",
    stt_model: str = "",
    tts_provider: str = "",
    tts_model: str = "",
    telephony_rate: float = 0.02,
    is_platform_telephony: bool = True,
    is_byok_llm: bool = False,
    is_byok_stt: bool = False,
    is_byok_tts: bool = False,
    byok_platform_fee_per_minute: float = 0.04,
) -> dict:
    """Calculate the composite rate per minute from model-specific pricing, BYOK platform fees, and telephony.

    Rules:
    - If is_platform_telephony is False (BYOT): telephony usage rate is $0.00/min.
    - If is_byok is True for a model service: that model's platform rate is $0.00/min (user pays provider directly).
    - If any BYOK service is used: a platform orchestration fee (byok_platform_fee_per_minute) is charged.
    """
    raw_llm = get_model_rate_per_minute("llm", llm_provider, llm_model) if llm_provider else 0.015
    llm_rate = 0.0 if is_byok_llm else raw_llm

    raw_stt = get_model_rate_per_minute("stt", stt_provider, stt_model) if stt_provider else 0.005
    stt_rate = 0.0 if is_byok_stt else raw_stt

    raw_tts = get_model_rate_per_minute("tts", tts_provider, tts_model) if tts_provider else 0.020
    tts_rate = 0.0 if is_byok_tts else raw_tts

    has_any_byok = is_byok_llm or is_byok_stt or is_byok_tts
    byok_fee = byok_platform_fee_per_minute if has_any_byok else 0.0

    effective_telephony_rate = telephony_rate if is_platform_telephony else 0.0

    composite_rate = round(llm_rate + stt_rate + tts_rate + effective_telephony_rate + byok_fee, 4)
    return {
        "rate_per_minute": composite_rate,
        "llm_rate": llm_rate,
        "stt_rate": stt_rate,
        "tts_rate": tts_rate,
        "telephony_rate": effective_telephony_rate,
        "byok_fee": byok_fee,
        "is_platform_telephony": is_platform_telephony,
        "is_byok": has_any_byok,
    }
