import base64
import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from api.services.telephony.providers.plivo.provider import PlivoProvider
from api.services.telephony.providers.plivo.routes import (
    handle_plivo_hangup_callback,
    handle_plivo_xml_webhook,
)


def _provider() -> PlivoProvider:
    return PlivoProvider(
        {
            "auth_id": "MA123",
            "auth_token": "plivo-auth-token",
            "from_numbers": ["+15551230002"],
        }
    )


def _request(
    *,
    path: str,
    query: dict[str, str | int],
    form_data: dict[str, str],
    headers: dict[str, str] | None = None,
) -> Request:
    body = urlencode(form_data).encode("utf-8")
    query_string = urlencode(query).encode("utf-8")
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
            "query_string": query_string,
            "headers": request_headers,
        },
        receive,
    )


def _signature(
    provider: PlivoProvider,
    *,
    path: str,
    query: dict[str, str | int],
    form_data: dict[str, str],
    nonce: str,
) -> str:
    url = f"https://example.test{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    payload = f"{provider._construct_post_url(url, form_data)}.{nonce}"
    return base64.b64encode(
        hmac.new(
            provider.auth_token.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")


@pytest.mark.asyncio
async def test_plivo_xml_route_accepts_valid_signature_with_extra_query_param():
    provider = _provider()
    query = {
        "workflow_id": 7,
        "user_id": 8,
        "workflow_run_id": 123,
        "campaign_id": 42,
        "organization_id": 11,
    }
    form_data = {
        "CallUUID": "call-123",
        "Direction": "outbound",
        "From": "15551230001",
        "To": "15551230002",
    }
    nonce = "nonce-123"
    request = _request(
        path="/api/v1/telephony/plivo-xml",
        query=query,
        form_data=form_data,
        headers={
            "x-plivo-signature-v3": _signature(
                provider,
                path="/api/v1/telephony/plivo-xml",
                query=query,
                form_data=form_data,
                nonce=nonce,
            ),
            "x-plivo-signature-v3-nonce": nonce,
        },
    )

    with (
        patch("api.services.telephony.providers.plivo.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.plivo.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch.object(
            provider,
            "get_webhook_response",
            new_callable=AsyncMock,
            return_value="<Response/>",
        ) as get_webhook_response,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(gathered_context={}, workflow_id=7)
        )
        db_client.update_workflow_run = AsyncMock()

        response = await handle_plivo_xml_webhook(
            workflow_id=7,
            user_id=8,
            workflow_run_id=123,
            organization_id=11,
            request=request,
        )

    assert response.body == b"<Response/>"
    get_webhook_response.assert_awaited_once_with(7, 8, 123)
    db_client.update_workflow_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_plivo_status_callback_rejects_missing_signature():
    provider = _provider()
    request = _request(
        path="/api/v1/telephony/plivo/hangup-callback/123",
        query={},
        form_data={"CallUUID": "call-123", "Event": "hangup"},
    )

    with (
        patch("api.services.telephony.providers.plivo.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.plivo.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.plivo.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_plivo_hangup_callback(
            workflow_run_id=123, request=request
        )

    assert result == {"status": "error", "reason": "invalid_signature"}
    process_status.assert_not_awaited()
