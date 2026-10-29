"""Voice discovery using an organization's saved provider connection."""

import asyncio
from random import choice

import httpx
from fastapi import HTTPException

from api.db import db_client
from api.schemas.voices import VoicesResponse
from api.services.configuration.voice_catalog_adapters import (
    VOICE_CATALOGS,
    VoiceQuery,
    create_voice_catalog,
)
from api.utils.url_security import validate_user_configured_service_url


async def get_connection_voices(
    provider: str,
    organization_id: int,
    connection_uuid: str,
    *,
    model: str | None = None,
    language: str | None = None,
    q: str | None = None,
    gender: str | None = None,
    accent: str | None = None,
) -> VoicesResponse:
    catalog_type = VOICE_CATALOGS.get(provider)
    if catalog_type is None:
        raise HTTPException(
            status_code=400, detail="Voice catalog is not supported for this provider"
        )
    name = catalog_type.name
    connection = await db_client.get_provider_connection(
        organization_id, connection_uuid
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    if connection.provider != provider:
        raise HTTPException(
            status_code=400, detail=f"Select a {name} provider connection"
        )
    keys = connection.credentials.get("api_key")
    # Select from the pool once; pagination must keep using the same account.
    api_key = choice(keys) if isinstance(keys, list) and keys else keys
    if catalog_type.auth_header and (
        not isinstance(api_key, str) or not api_key.strip()
    ):
        raise HTTPException(
            status_code=400, detail=f"Add an API key to the {name} connection"
        )
    try:
        catalog = create_voice_catalog(
            provider,
            api_key=api_key,
            base_url=connection.connection_settings.get("base_url"),
        )
        if catalog.url:
            await asyncio.to_thread(
                validate_user_configured_service_url, catalog.url, field_name="base_url"
            )
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid {name} connection base URL"
        ) from None

    try:
        async with (
            asyncio.timeout(30),
            httpx.AsyncClient(timeout=10, follow_redirects=False) as client,
        ):
            voices = await catalog.voices(
                client, VoiceQuery(model, language, q, gender, accent)
            )
    except (TimeoutError, httpx.TimeoutException):
        raise HTTPException(
            status_code=504, detail=f"{name} took too long to load voices. Try again."
        ) from None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            permission = (
                "Voices read permission"
                if provider == "elevenlabs"
                else "voice catalog permissions"
            )
            raise HTTPException(
                status_code=400,
                detail=f"{name} could not list voices. Check the connection's API key and its {permission}.",
            ) from None
        if exc.response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail=f"{name} voice requests are rate limited. Try again shortly.",
            ) from None
        raise HTTPException(
            status_code=502, detail=f"Could not load voices from {name}. Try again."
        ) from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        # Provider bodies/request exceptions can contain credentials or account
        # details. Never return or log them.
        raise HTTPException(
            status_code=502, detail=f"Could not load voices from {name}. Try again."
        ) from None

    # Language/model compatibility is provider-specific and handled by adapters.
    # Preserve provider search semantics, apply remaining filters, and deduplicate.
    filtered = {}
    for voice in voices:
        if (
            gender
            and "gender" not in catalog.server_filters
            and (voice.gender or "").casefold() != gender.casefold()
        ):
            continue
        if (
            accent
            and "accent" not in catalog.server_filters
            and (voice.accent or "").casefold() != accent.casefold()
        ):
            continue
        if (
            q
            and "q" not in catalog.server_filters
            and q.casefold()
            not in " ".join(
                filter(None, (voice.voice_id, voice.name, voice.description))
            ).casefold()
        ):
            continue
        filtered[voice.voice_id] = voice
    return VoicesResponse(
        provider=provider,
        voices=sorted(filtered.values(), key=lambda voice: voice.name.casefold()),
    )
