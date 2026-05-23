import hashlib
import hmac
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from api.services.telephony.providers.vobiz.provider import VobizProvider
from api.services.telephony.providers.vobiz.routes import (
    handle_vobiz_hangup_callback,
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


def _signed_headers(
    provider: VobizProvider, *, form_data: dict[str, str]
) -> dict[str, str]:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    body = urlencode(form_data)
    signature = hmac.new(
        provider.auth_token.encode("utf-8"),
        f"{timestamp}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "x-vobiz-signature": signature,
        "x-vobiz-timestamp": timestamp,
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
    headers = _signed_headers(provider, form_data=form_data)
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
            x_vobiz_signature=headers["x-vobiz-signature"],
            x_vobiz_timestamp=headers["x-vobiz-timestamp"],
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
    headers = _signed_headers(provider, form_data=form_data)
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
            x_vobiz_signature=headers["x-vobiz-signature"],
            x_vobiz_timestamp=headers["x-vobiz-timestamp"],
        )

    assert result == {"status": "success"}
    db_client.update_workflow_run.assert_awaited_once()
