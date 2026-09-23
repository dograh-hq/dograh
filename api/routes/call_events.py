"""Organization settings for call-event destinations."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from api.constants import AUTH_PROVIDER
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.observability.call_events.configuration import (
    CONFIG_KEY,
    CallEventsSettings,
    load_settings,
    masked_settings,
    registration,
    resolve_settings,
    sink_types,
)

router = APIRouter(prefix="/organizations/call-events", tags=["organizations"])


class CallEventsSettingsResponse(CallEventsSettings):
    available_sinks: list[str]
    deployment_identity_available: bool


class CallEventsConnectionResult(BaseModel):
    message: str


def _org(user):
    if not user.selected_organization_id:
        raise HTTPException(400, "No organization selected")
    return user.selected_organization_id


async def _resolve(org_id, request):
    try:
        return await resolve_settings(org_id, request)
    except ValidationError as exc:
        # Validation inputs may contain private keys; never echo them in HTTP errors.
        messages = [
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in exc.errors(include_input=False)
        ]
        raise HTTPException(422, "; ".join(messages)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("", response_model=CallEventsSettingsResponse)
async def get_call_events_settings(user: UserModel = Depends(get_user)):
    settings = masked_settings(await load_settings(_org(user)))
    return CallEventsSettingsResponse(
        **settings.model_dump(),
        available_sinks=sink_types(),
        deployment_identity_available=AUTH_PROVIDER == "local",
    )


@router.put("", response_model=CallEventsSettings)
async def save_call_events_settings(
    request: CallEventsSettings, user: UserModel = Depends(get_user)
):
    org_id = _org(user)
    settings = await _resolve(org_id, request)
    await db_client.upsert_configuration(
        org_id, CONFIG_KEY, settings.model_dump(mode="json")
    )
    return masked_settings(settings)


@router.post("/test", response_model=CallEventsConnectionResult)
async def test_call_events_connection(
    request: CallEventsSettings, user: UserModel = Depends(get_user)
):
    settings = await _resolve(_org(user), request)
    if not settings.sink_type:
        raise HTTPException(422, "Select a destination")
    spec = registration(settings.sink_type)
    sink = spec.create(spec.config_model.model_validate(settings.config))
    try:
        async with asyncio.timeout(20):
            await sink.validate_connection()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(
            400, "Connection failed. Check the destination and credentials."
        ) from None
    finally:
        await sink.close()
    return CallEventsConnectionResult(
        message="Connection and table schema verified. No events were written; write permission is checked on export."
    )


@router.delete("", response_model=CallEventsSettings)
async def delete_call_events_settings(user: UserModel = Depends(get_user)):
    await db_client.delete_configuration(_org(user), CONFIG_KEY)
    return CallEventsSettings()
