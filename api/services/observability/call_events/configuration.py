"""Org settings and sink discovery. Only configuration goes to the DB."""

import asyncio

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.schemas.call_events import CallEventsSettings
from api.services.integrations.registry import get_package

MASKED_SECRET = "********"
CONFIG_KEY = OrganizationConfigurationKey.CALL_EVENTS.value


def registration(sink_type: str):
    package = get_package(sink_type)
    if package is None or package.call_event_sink is None:
        raise ValueError("Unsupported call-event destination")
    return package.call_event_sink


async def load_settings(organization_id: int) -> CallEventsSettings:
    row = await db_client.get_configuration(organization_id, CONFIG_KEY)
    return (
        CallEventsSettings.model_validate(row.value)
        if row and row.value
        else CallEventsSettings()
    )


def masked_settings(settings: CallEventsSettings) -> CallEventsSettings:
    result = settings.model_copy(deep=True)
    if result.sink_type:
        for key in registration(result.sink_type).sensitive_fields:
            if result.config.get(key):
                result.config[key] = MASKED_SECRET
    return result


async def resolve_settings(
    organization_id: int, request: CallEventsSettings
) -> CallEventsSettings:
    if request.sink_type is None:
        if request.enabled:
            raise ValueError("Select a destination before enabling call events")
        return CallEventsSettings()
    spec = registration(request.sink_type)
    config = dict(request.config)
    previous = await load_settings(organization_id)
    for field in spec.sensitive_fields:
        if config.get(field) == MASKED_SECRET:
            value = (
                previous.config.get(field)
                if previous.sink_type == request.sink_type
                else None
            )
            if not value:
                raise ValueError("Enter credentials for this destination")
            config[field] = value
    validated = spec.config_model.model_validate(config)
    return request.model_copy(update={"config": validated.model_dump(mode="json")})


def same_destination(before: CallEventsSettings, after: CallEventsSettings) -> bool:
    if not after.enabled or before.sink_type != after.sink_type or not before.sink_type:
        return False
    spec = registration(before.sink_type)
    return all(
        before.config.get(k) == after.config.get(k) for k in spec.destination_fields
    )


async def check_connection(settings: CallEventsSettings) -> None:
    if not settings.sink_type:
        raise ValueError("Select a destination")
    spec = registration(settings.sink_type)
    sink = spec.create(spec.config_model.model_validate(settings.config))
    try:
        async with asyncio.timeout(20):
            await sink.validate_connection()
    finally:
        await sink.close()
