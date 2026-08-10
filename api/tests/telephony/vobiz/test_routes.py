import base64
import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.services.telephony.providers.vobiz.provider import VobizProvider
from api.services.telephony.providers.vobiz.routes import (
    handle_vobiz_hangup_callback,
    handle_vobiz_hangup_callback_by_workflow,
    handle_vobiz_ring_callback,
)


def _provider(application_id: str | None = None) -> VobizProvider:
    return VobizProvider(
        {
            "auth_id": "MA123",
            "auth_token": "vobiz-auth-token",
            "application_id": application_id,
            "from_numbers": ["+15551230002"],
        }
    )


def _request(
    *,
    path: str,
    form_data: dict[str, str],
    headers: dict[str, str] | None = None,
) -> Request:
    body = urlencode(form_data).encode("utf-8")
    request_headers = [
        (b"content-type", b"application/x-www-form-urlencoded"),
        *[
            (name.lower().encode("ascii"), value.encode("ascii"))
            for name, value in (headers or {}).items()
        ],
    ]

    async def receive():
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("example.test", 443),
            "path": path,
            "query_string": b"",
            "headers": request_headers,
        },
        receive,
    )


def _signed_headers(provider: VobizProvider, *, url: str) -> dict[str, str]:
    nonce = "12345678901234567890"
    signature = base64.b64encode(
        hmac.new(
            provider.auth_token.encode("utf-8"),
            f"{url}.{nonce}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")
    return {
        "x-vobiz-signature-v3": signature,
        "x-vobiz-signature-v3-nonce": nonce,
    }


class _StubResponse:
    def __init__(self, status: int, body: str = ""):
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _StubSession:
    def __init__(self, responses: list[_StubResponse]):
        self.responses = responses
        self.requests: list[tuple[str, str, dict | None]] = []

    def post(self, url: str, *, json: dict, headers: dict):
        self.requests.append(("POST", url, json))
        return self.responses.pop(0)

    def delete(self, url: str, *, headers: dict):
        self.requests.append(("DELETE", url, None))
        return self.responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_vobiz_hangup_callback_accepts_signed_form_body():
    provider = _provider()
    form_data = {
        "CallUUID": "call-123",
        "CallStatus": "completed",
        "From": "15551230001",
        "To": "15551230002",
        "Direction": "outbound",
        "Duration": "12",
    }
    headers = _signed_headers(
        provider, url="https://example.test/api/v1/telephony/vobiz/hangup-callback/123"
    )
    request = _request(
        path="/api/v1/telephony/vobiz/hangup-callback/123",
        form_data=form_data,
        headers=headers,
    )

    with (
        patch("api.services.telephony.providers.vobiz.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.vobiz.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.vobiz.routes.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://example.test", "wss://example.test"),
        ),
        patch(
            "api.services.telephony.providers.vobiz.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_vobiz_hangup_callback(
            workflow_run_id=123,
            request=request,
        )

    assert result == {"status": "success"}
    process_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_vobiz_ring_callback_accepts_signed_form_body():
    provider = _provider()
    form_data = {
        "CallUUID": "call-123",
        "CallStatus": "ringing",
        "From": "15551230001",
        "To": "15551230002",
    }
    headers = _signed_headers(
        provider, url="https://example.test/api/v1/telephony/vobiz/ring-callback/123"
    )
    request = _request(
        path="/api/v1/telephony/vobiz/ring-callback/123",
        form_data=form_data,
        headers=headers,
    )

    workflow_run = SimpleNamespace(workflow_id=7, logs={})

    with (
        patch("api.services.telephony.providers.vobiz.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.vobiz.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.vobiz.routes.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://example.test", "wss://example.test"),
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )
        db_client.update_workflow_run = AsyncMock()

        result = await handle_vobiz_ring_callback(
            workflow_run_id=123,
            request=request,
        )

    assert result == {"status": "success"}
    db_client.update_workflow_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_vobiz_verify_webhook_signature_accepts_v3_and_strips_query():
    provider = _provider()
    headers = _signed_headers(
        provider, url="https://example.test/api/v1/telephony/vobiz/hangup-callback/123"
    )

    assert await provider.verify_webhook_signature(
        "https://example.test/api/v1/telephony/vobiz/hangup-callback/123?foo=bar",
        {},
        headers["x-vobiz-signature-v3"],
        headers["x-vobiz-signature-v3-nonce"],
        signature_version="v3",
    )


@pytest.mark.asyncio
async def test_vobiz_verify_inbound_signature_accepts_v2():
    provider = _provider()
    url = "https://example.test/api/v1/telephony/vobiz/hangup-callback/123"
    nonce = "12345678901234567890"
    signature = base64.b64encode(
        hmac.new(
            provider.auth_token.encode("utf-8"),
            f"{url}{nonce}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")

    assert await provider.verify_inbound_signature(
        url,
        {},
        {
            "X-Vobiz-Signature-V2": signature,
            "X-Vobiz-Signature-V2-Nonce": nonce,
        },
    )


@pytest.mark.asyncio
async def test_vobiz_configure_inbound_updates_application_and_attaches_number():
    provider = _provider(application_id="12345678901234567")
    session = _StubSession([_StubResponse(204), _StubResponse(202)])

    with patch(
        "api.services.telephony.providers.vobiz.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.configure_inbound(
            "+15551230002",
            "https://voice.example.test/api/v1/telephony/inbound/run",
        )

    assert result.ok
    assert session.requests == [
        (
            "POST",
            "https://api.vobiz.ai/api/v1/Account/MA123/numbers/"
            "%2B15551230002/application",
            {"application_id": "12345678901234567"},
        ),
        (
            "POST",
            "https://api.vobiz.ai/api/v1/Account/MA123/Application/12345678901234567/",
            {
                "answer_url": (
                    "https://voice.example.test/api/v1/telephony/inbound/run"
                ),
                "answer_method": "POST",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_vobiz_configure_inbound_does_not_update_application_when_attach_fails():
    provider = _provider(application_id="12345678901234567")
    session = _StubSession([_StubResponse(409, "number already assigned")])

    with patch(
        "api.services.telephony.providers.vobiz.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.configure_inbound(
            "+15551230002",
            "https://voice.example.test/api/v1/telephony/inbound/run",
        )

    assert not result.ok
    assert result.message == (
        "Vobiz indicates that this phone number is already attached to an "
        "application. To enable inbound calls in Dograh, review the phone number "
        "configuration in Vobiz and ensure that the number is attached to the "
        "Application ID configured in Dograh (12345678901234567)."
    )
    assert session.requests == [
        (
            "POST",
            "https://api.vobiz.ai/api/v1/Account/MA123/numbers/"
            "%2B15551230002/application",
            {"application_id": "12345678901234567"},
        )
    ]


@pytest.mark.asyncio
async def test_vobiz_configure_inbound_explains_missing_number_or_application():
    provider = _provider(application_id="12345678901234567")
    session = _StubSession([_StubResponse(404, "not found")])

    with patch(
        "api.services.telephony.providers.vobiz.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.configure_inbound(
            "+15551230002",
            "https://voice.example.test/api/v1/telephony/inbound/run",
        )

    assert not result.ok
    assert result.message == (
        "Vobiz could not find this phone number or the configured Application ID. "
        "Confirm that the number belongs to this Vobiz account and that Application "
        "ID 12345678901234567 exists."
    )


@pytest.mark.asyncio
async def test_vobiz_configure_inbound_detaches_number_without_clearing_application():
    provider = _provider(application_id="12345678901234567")
    session = _StubSession([_StubResponse(204)])

    with patch(
        "api.services.telephony.providers.vobiz.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.configure_inbound("+15551230002", None)

    assert result.ok
    assert session.requests == [
        (
            "DELETE",
            "https://api.vobiz.ai/api/v1/Account/MA123/numbers/"
            "%2B15551230002/application",
            None,
        )
    ]


@pytest.mark.asyncio
async def test_vobiz_configure_inbound_treats_missing_detach_as_success():
    provider = _provider(application_id="12345678901234567")
    session = _StubSession([_StubResponse(404, "number has no application")])

    with patch(
        "api.services.telephony.providers.vobiz.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.configure_inbound("+15551230002", None)

    assert result.ok
    assert session.requests == [
        (
            "DELETE",
            "https://api.vobiz.ai/api/v1/Account/MA123/numbers/"
            "%2B15551230002/application",
            None,
        )
    ]


@pytest.mark.asyncio
async def test_vobiz_verify_inbound_signature_rejects_missing_signature():
    provider = _provider()

    assert not await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/vobiz/hangup-callback/123",
        {},
        {},
    )


@pytest.mark.asyncio
async def test_vobiz_hangup_callback_rejects_missing_signature():
    """An unsigned hangup callback must be rejected before status processing."""
    provider = _provider()
    form_data = {
        "CallUUID": "call-123",
        "CallStatus": "completed",
        "From": "15551230001",
        "To": "15551230002",
        "Direction": "outbound",
        "Duration": "12",
    }
    # No x-vobiz-signature-* headers — the callback is unsigned.
    request = _request(
        path="/api/v1/telephony/vobiz/hangup-callback/123",
        form_data=form_data,
    )

    with (
        patch("api.services.telephony.providers.vobiz.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.vobiz.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.vobiz.routes.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://example.test", "wss://example.test"),
        ),
        patch(
            "api.services.telephony.providers.vobiz.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        with pytest.raises(HTTPException) as exc_info:
            await handle_vobiz_hangup_callback(
                workflow_run_id=123,
                request=request,
            )

    assert exc_info.value.status_code == 403
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_vobiz_ring_callback_rejects_missing_signature():
    """An unsigned ring callback must be rejected before it is logged."""
    provider = _provider()
    form_data = {
        "CallUUID": "call-123",
        "CallStatus": "ringing",
        "From": "15551230001",
        "To": "15551230002",
    }
    # No x-vobiz-signature-* headers — the callback is unsigned.
    request = _request(
        path="/api/v1/telephony/vobiz/ring-callback/123",
        form_data=form_data,
    )

    workflow_run = SimpleNamespace(workflow_id=7, logs={})

    with (
        patch("api.services.telephony.providers.vobiz.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.vobiz.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.vobiz.routes.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://example.test", "wss://example.test"),
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )
        db_client.update_workflow_run = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await handle_vobiz_ring_callback(
                workflow_run_id=123,
                request=request,
            )

    assert exc_info.value.status_code == 403
    db_client.update_workflow_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_vobiz_hangup_callback_by_workflow_rejects_missing_signature():
    """An unsigned by-workflow hangup callback must be rejected before processing."""
    provider = _provider()
    form_data = {
        "CallUUID": "call-123",
        "CallStatus": "completed",
        "From": "15551230001",
        "To": "15551230002",
        "Direction": "outbound",
        "Duration": "12",
    }
    # No x-vobiz-signature-* headers — the callback is unsigned.
    request = _request(
        path="/api/v1/telephony/vobiz/hangup-callback/workflow/7",
        form_data=form_data,
    )

    with (
        patch("api.services.telephony.providers.vobiz.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.vobiz.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.vobiz.routes.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://example.test", "wss://example.test"),
        ),
        patch(
            "api.services.telephony.providers.vobiz.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )
        db_client.get_workflow_run_by_call_id = AsyncMock(
            return_value=SimpleNamespace(id=123, workflow_id=7)
        )

        with pytest.raises(HTTPException) as exc_info:
            await handle_vobiz_hangup_callback_by_workflow(
                workflow_id=7,
                request=request,
            )

    assert exc_info.value.status_code == 403
    process_status.assert_not_awaited()
