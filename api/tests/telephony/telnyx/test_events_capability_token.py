"""Pre-auth capability-token gate for the Telnyx events route (#780 review).

When ``TELEPHONY_WS_TOKEN_SECRET`` is set, the events webhook URL minted by
the provider carries an HMAC token in its query string (Telnyx preserves
query strings on webhook POSTs, verified live). The route verifies the token
before any database lookup, so the reject path does constant work and
unauthenticated callers learn nothing about run existence through status
code or timing. With no secret configured the flow degrades to the
lookup-then-verify order documented in the uniform-404 PR.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api import constants
from api.services.telephony import ws_auth
from api.services.telephony.providers.telnyx.provider import TelnyxProvider
from api.services.telephony.providers.telnyx.routes import handle_telnyx_events


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_SECRET", "unit-test-secret")


@pytest.fixture
def no_secret(monkeypatch):
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_SECRET", None)


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


def _request(body: str, query_string: str = b"") -> Request:
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
            "query_string": query_string,
            "headers": [],
        },
        receive,
    )


# ---------------------------------------------------------------------------
# Provider: the minted webhook URL carries the token when the secret is set.
# ---------------------------------------------------------------------------


def _dial_session() -> MagicMock:
    session = MagicMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False

    dial_response = MagicMock()
    dial_response.status = 200
    dial_response.json = AsyncMock(
        return_value={
            "data": {
                "call_control_id": "cc-123",
                "call_leg_id": "leg-123",
                "call_session_id": "session-123",
            }
        }
    )
    session.post.return_value.__aenter__.return_value = dial_response
    return session


def _dial_provider() -> TelnyxProvider:
    return TelnyxProvider(
        {
            "api_key": "placeholder-api-key",
            "connection_id": "connection-id",
            "webhook_public_key": "placeholder-public-key",
            "from_numbers": ["+15551230002"],
        }
    )


@pytest.mark.asyncio
async def test_dial_webhook_url_carries_events_token_when_secret_set(secret):
    session = _dial_session()
    with (
        patch(
            "api.services.telephony.providers.telnyx.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.backend.test", "wss://api.backend.test"),
        ),
        patch(
            "api.services.telephony.providers.telnyx.provider.aiohttp.ClientSession",
            MagicMock(return_value=session),
        ),
    ):
        await _dial_provider().initiate_call(
            "+15551230001",
            "https://ignored-webhook-url",
            workflow_run_id=123,
            workflow_id=7,
            organization_id=11,
        )

    body = session.post.call_args.kwargs["json"]
    token = ws_auth.mint_events_token(123)
    assert body["webhook_url"] == (
        f"https://api.backend.test/api/v1/telephony/telnyx/events/123?token={token}"
    )


@pytest.mark.asyncio
async def test_dial_webhook_url_has_no_query_when_no_secret(no_secret):
    session = _dial_session()
    with (
        patch(
            "api.services.telephony.providers.telnyx.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.backend.test", "wss://api.backend.test"),
        ),
        patch(
            "api.services.telephony.providers.telnyx.provider.aiohttp.ClientSession",
            MagicMock(return_value=session),
        ),
    ):
        await _dial_provider().initiate_call(
            "+15551230001",
            "https://ignored-webhook-url",
            workflow_run_id=123,
            workflow_id=7,
            organization_id=11,
        )

    body = session.post.call_args.kwargs["json"]
    assert body["webhook_url"] == (
        "https://api.backend.test/api/v1/telephony/telnyx/events/123"
    )


# ---------------------------------------------------------------------------
# Route: the token gate runs before any database lookup.
# ---------------------------------------------------------------------------


async def _post_with_token(query_string: bytes):
    """POST with a capability token against a run that DOES exist.

    Returns ``(exc, db_client, process_status)`` so a caller can tell the three
    outcomes apart: rejected at the gate (404 + the run lookup never awaited),
    passed the gate and stopped at the signature check (404 + lookup awaited),
    or processed. A stub returning ``None`` for the run would make the gate's
    404 and the missing-run 404 indistinguishable and the assertions vacuous.
    """
    provider = _dial_provider()
    request = _request(_body(), query_string)

    db_client = MagicMock()
    db_client.get_workflow_run_by_id = AsyncMock(
        return_value=SimpleNamespace(workflow_id=7)
    )
    db_client.get_workflow_by_id = AsyncMock(
        return_value=SimpleNamespace(organization_id=11)
    )
    with (
        patch("api.services.telephony.providers.telnyx.routes.db_client", db_client),
        patch(
            "api.services.telephony.providers.telnyx.routes."
            "get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch.object(
            provider,
            "verify_inbound_signature",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "api.services.telephony.providers.telnyx.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        exc = None
        try:
            await handle_telnyx_events(request, workflow_run_id=123)
        except HTTPException as raised:
            exc = raised
    return exc, db_client, process_status


@pytest.mark.asyncio
async def test_missing_token_rejected_before_any_lookup(secret):
    exc, db_client, process_status = await _post_with_token(b"")
    assert exc is not None
    assert exc.status_code == 404
    assert exc.detail == "Workflow run not found"
    # The point of the gate: constant work on the reject path.
    db_client.get_workflow_run_by_id.assert_not_awaited()
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_token_rejected_before_any_lookup(secret):
    exc, db_client, process_status = await _post_with_token(b"token=not-the-right-hmac")
    assert exc is not None
    assert exc.status_code == 404
    assert exc.detail == "Workflow run not found"
    db_client.get_workflow_run_by_id.assert_not_awaited()
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_token_passes_the_gate(secret):
    token = ws_auth.mint_events_token(123)
    exc, db_client, process_status = await _post_with_token(f"token={token}".encode())
    # Past the gate: the lookup ran. The request still dies at the signature
    # check (stubbed False), with the same uniform 404 -- which is why the
    # assert_awaited below, not the status code, is what proves the gate passed.
    db_client.get_workflow_run_by_id.assert_awaited_once()
    assert exc is not None
    assert exc.status_code == 404
    assert exc.detail == "Workflow run not found"
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_secret_keeps_legacy_uniform_flow(no_secret):
    exc, db_client, _ = await _post_with_token(b"")
    # No secret: no gate at all, so the lookup runs and the signature check
    # produces the uniform 404.
    db_client.get_workflow_run_by_id.assert_awaited_once()
    assert exc is not None
    assert exc.status_code == 404
    assert exc.detail == "Workflow run not found"
