from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from twilio.request_validator import RequestValidator

from api.services.telephony.providers.twilio.provider import TwilioProvider
from api.services.telephony.providers.twilio.routes import (
    handle_twilio_status_callback,
    handle_twiml_webhook,
)


def _provider() -> TwilioProvider:
    return TwilioProvider(
        {
            "account_sid": "AC123",
            "auth_token": "twilio-auth-token",
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
    provider: TwilioProvider,
    *,
    path: str,
    query: dict[str, str | int],
    form_data: dict[str, str],
) -> str:
    url = f"https://example.test{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    validator = RequestValidator(provider.auth_token)
    return validator.compute_signature(url, form_data)


@pytest.mark.asyncio
async def test_twiml_route_accepts_valid_signature_with_extra_query_param():
    provider = _provider()
    query = {
        "workflow_id": 7,
        "user_id": 8,
        "workflow_run_id": 123,
        "campaign_id": 42,
        "organization_id": 11,
    }
    form_data = {"CallSid": "CA123", "CallStatus": "in-progress"}
    request = _request(
        path="/api/v1/telephony/twiml",
        query=query,
        form_data=form_data,
        headers={
            "x-twilio-signature": _signature(
                provider,
                path="/api/v1/telephony/twiml",
                query=query,
                form_data=form_data,
            )
        },
    )

    with (
        patch("api.services.telephony.providers.twilio.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.twilio.routes.get_telephony_provider_for_run",
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
            return_value=SimpleNamespace(id=123)
        )

        response = await handle_twiml_webhook(
            workflow_id=7,
            user_id=8,
            workflow_run_id=123,
            organization_id=11,
            request=request,
        )

    assert response.body == b"<Response/>"
    get_webhook_response.assert_awaited_once_with(7, 8, 123)


@pytest.mark.asyncio
async def test_twiml_route_rejects_missing_signature():
    provider = _provider()
    request = _request(
        path="/api/v1/telephony/twiml",
        query={
            "workflow_id": 7,
            "user_id": 8,
            "workflow_run_id": 123,
            "organization_id": 11,
        },
        form_data={"CallSid": "CA123"},
    )

    with (
        patch("api.services.telephony.providers.twilio.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.twilio.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(id=123)
        )

        with pytest.raises(HTTPException) as exc_info:
            await handle_twiml_webhook(
                workflow_id=7,
                user_id=8,
                workflow_run_id=123,
                organization_id=11,
                request=request,
            )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid webhook signature"


@pytest.mark.asyncio
async def test_twilio_status_callback_rejects_legacy_header_name():
    provider = _provider()
    form_data = {"CallSid": "CA123", "CallStatus": "completed"}
    request = _request(
        path="/api/v1/telephony/twilio/status-callback/123",
        query={},
        form_data=form_data,
        headers={"x-webhook-signature": "not-a-twilio-signature"},
    )

    with (
        patch("api.services.telephony.providers.twilio.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.twilio.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.twilio.routes._process_status_update",
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
            await handle_twilio_status_callback(workflow_run_id=123, request=request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid webhook signature"
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_twilio_status_callback_accepts_valid_signature():
    provider = _provider()
    form_data = {"CallSid": "CA123", "CallStatus": "completed"}
    request = _request(
        path="/api/v1/telephony/twilio/status-callback/123",
        query={},
        form_data=form_data,
        headers={
            "x-twilio-signature": _signature(
                provider,
                path="/api/v1/telephony/twilio/status-callback/123",
                query={},
                form_data=form_data,
            )
        },
    )

    with (
        patch("api.services.telephony.providers.twilio.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.twilio.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.twilio.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_twilio_status_callback(
            workflow_run_id=123, request=request
        )

    assert result == {"status": "success"}
    process_status.assert_awaited_once()
