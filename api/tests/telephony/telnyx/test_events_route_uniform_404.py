"""Uniform-404 behavior of the Telnyx events route (dograh-hq/dograh#780).

Invalid-signature requests must be indistinguishable from requests for a
nonexistent run id: run ids are sequential integers, so a distinct 401 on
existing runs would let unsigned callers enumerate which runs exist. The
route answers both cases with HTTPException 404 "Workflow run not found";
the logger.warning in the route keeps the real reason in server logs.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.services.telephony.providers.telnyx.provider import TelnyxProvider
from api.services.telephony.providers.telnyx.routes import handle_telnyx_events


def _body() -> str:
    return json.dumps(
        {
            "data": {
                "record_type": "event",
                "event_type": "call.initiated",
                "payload": {
                    "call_control_id": "call-control-id",
                    "connection_id": "connection-id",
                    "direction": "incoming",
                    "from": "+15551230001",
                    "to": "+15551230002",
                },
            }
        },
        separators=(",", ":"),
    )


def _request(body: str, headers: dict[str, str], query_string: bytes = b"") -> Request:
    async def receive():
        return {
            "type": "http.request",
            "body": body.encode("utf-8"),
            "more_body": False,
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/telephony/telnyx/events/123",
            # Required: the route reads request.query_params for the capability
            # token, and starlette indexes scope["query_string"] directly.
            "query_string": query_string,
            "headers": [
                (name.lower().encode("ascii"), value.encode("ascii"))
                for name, value in headers.items()
            ],
        },
        receive,
    )


def _provider() -> TelnyxProvider:
    return TelnyxProvider(
        {
            "api_key": "placeholder-api-key",
            "connection_id": "connection-id",
            "webhook_public_key": "placeholder-public-key",
            "from_numbers": ["+15551230002"],
        }
    )


async def _post_existing_run_invalid_signature(
    query_string: bytes = b"",
) -> HTTPException:
    """Send an unsigned request for a run that exists (bad signature)."""
    provider = _provider()
    request = _request(_body(), {}, query_string)

    with (
        patch("api.services.telephony.providers.telnyx.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.telnyx.routes."
            "get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.telnyx.routes._process_status_update",
            new_callable=AsyncMock,
        ),
        patch.object(
            provider,
            "verify_inbound_signature",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        with pytest.raises(HTTPException) as exc_info:
            await handle_telnyx_events(request, workflow_run_id=123)

    return exc_info.value


async def _post_unknown_run(query_string: bytes = b"") -> HTTPException:
    """Send a request for a run id that does not exist."""
    provider = _provider()
    request = _request(_body(), {}, query_string)

    with (
        patch("api.services.telephony.providers.telnyx.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.telnyx.routes."
            "get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.telnyx.routes._process_status_update",
            new_callable=AsyncMock,
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await handle_telnyx_events(request, workflow_run_id=999999)

    return exc_info.value


@pytest.mark.asyncio
async def test_events_route_invalid_signature_on_existing_run_returns_404():
    exc = await _post_existing_run_invalid_signature()

    assert exc.status_code == 404
    assert exc.detail == "Workflow run not found"


@pytest.mark.asyncio
async def test_events_route_unknown_run_returns_404():
    exc = await _post_unknown_run()

    assert exc.status_code == 404
    assert exc.detail == "Workflow run not found"


@pytest.mark.asyncio
async def test_events_route_invalid_signature_and_unknown_run_404s_are_identical():
    invalid_signature = await _post_existing_run_invalid_signature()
    unknown_run = await _post_unknown_run()

    assert invalid_signature.status_code == unknown_run.status_code
    assert invalid_signature.detail == unknown_run.detail


@pytest.mark.asyncio
async def test_uniform_404s_hold_in_the_production_configuration(monkeypatch):
    """The signature-path 404 must still be reachable when a secret is set.

    Production sets TELEPHONY_WS_TOKEN_SECRET, so without this the uniform-404
    behavior above would only ever be exercised in the no-secret configuration.
    Both requests carry a *valid* capability token for their own run id, so the
    token gate passes and they reach the checks this file is about.
    """
    from api import constants
    from api.services.telephony import ws_auth

    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_SECRET", "unit-test-secret")

    invalid_signature = await _post_existing_run_invalid_signature(
        query_string=f"token={ws_auth.mint_events_token(123)}".encode()
    )
    unknown_run = await _post_unknown_run(
        query_string=f"token={ws_auth.mint_events_token(999999)}".encode()
    )

    assert invalid_signature.status_code == unknown_run.status_code == 404
    assert invalid_signature.detail == unknown_run.detail == "Workflow run not found"
