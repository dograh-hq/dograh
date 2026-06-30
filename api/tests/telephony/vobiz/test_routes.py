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


def _provider() -> VobizProvider:
    return VobizProvider(
        {
            "auth_id": "MA123",
            "auth_token": "vobiz-auth-token",
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
