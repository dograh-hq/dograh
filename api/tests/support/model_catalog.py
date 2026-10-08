"""Seed an organization's model catalog for database-backed tests."""

import json

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration

ROLES = ("llm", "stt", "tts", "realtime", "embeddings")


async def seed_default_model_configuration(
    db, organization_id: int, effective: EffectiveAIModelConfiguration
) -> str:
    """Store ``effective`` as the organization's default; returns its uuid.

    Services sharing credentials share one provider connection, as the
    application's own catalog does.
    """
    from api.services.configuration.model_connections import (
        split_service_configuration,
    )

    spec = {"version": 3, "mode": "realtime" if effective.is_realtime else "pipeline"}
    connections: dict[tuple, str] = {}
    for role in ROLES:
        service = getattr(effective, role, None)
        if service is None:
            continue
        provider, credentials, connection_settings, settings = (
            split_service_configuration(service)
        )
        key = (
            provider,
            json.dumps(credentials, sort_keys=True),
            json.dumps(connection_settings, sort_keys=True),
        )
        if key not in connections:
            row = await db.create_provider_connection(
                organization_id,
                name=provider,
                provider=provider,
                credentials=credentials,
                connection_settings=connection_settings,
            )
            connections[key] = row.uuid
        spec[role] = {
            "provider_connection_uuid": connections[key],
            "settings": settings,
        }
    configuration = await db.create_named_model_configuration(
        organization_id, name="Organization default", configuration=spec
    )
    await db.set_default_named_model_configuration(organization_id, configuration.uuid)
    return configuration.uuid
