from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from api.services.telephony.providers.tone.config import ToneConfigurationResponse
from api.services.telephony.providers.tone.provider import ToneProvider
from api.services.telephony.providers.tone.routes import (
    handle_tone_hangup_callback,
    handle_tone_webhook,
)


def test_tone_configuration_response_masks_sensitive_fields():
    response = ToneConfigurationResponse(
        api_key="tone_live_abc123xyz",
        webhook_secret="my-super-secret-webhook-key",
        from_numbers=["+917314624707"],
    )
    assert response.api_key == "*" * 15 + "3xyz"
    assert "my-super-secret" not in response.webhook_secret
    assert response.webhook_secret.endswith("-key")  # actual last 4 of "my-super-secret-webhook-key"
    assert response.from_numbers == ["+917314624707"]  # non-sensitive unchanged


def _provider(*, webhook_secret: str | None = None) -> ToneProvider:
    cfg = {
        "api_key": "tone_live_test_key",
        "from_numbers": ["+917314624707"],
    }
    if webhook_secret is not None:
        cfg["webhook_secret"] = webhook_secret
    return ToneProvider(cfg)


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


@pytest.mark.asyncio
async def test_tone_webhook_saves_call_sid():
    form_data = {
        "CallSid": "call-abc-123",
        "From": "+917314624707",
        "To": "+919876543210",
        "Direction": "outbound",
    }
    query = {
        "workflow_id": 7,
        "user_id": 8,
        "workflow_run_id": 123,
        "organization_id": 11,
    }
    request = _request(
        path="/api/v1/telephony/tone-webhook",
        query=query,
        form_data=form_data,
    )

    with patch(
        "api.services.telephony.providers.tone.routes.db_client"
    ) as db_client:
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(gathered_context={}, workflow_id=7)
        )
        db_client.update_workflow_run = AsyncMock()

        response = await handle_tone_webhook(
            workflow_id=7,
            user_id=8,
            workflow_run_id=123,
            organization_id=11,
            request=request,
        )

    assert response == {"status": "ok"}
    db_client.update_workflow_run.assert_awaited_once()
    _, kwargs = db_client.update_workflow_run.call_args
    assert kwargs["gathered_context"]["call_id"] == "call-abc-123"
    assert kwargs["run_id"] == 123


@pytest.mark.asyncio
async def test_tone_webhook_missing_workflow_run():
    request = _request(
        path="/api/v1/telephony/tone-webhook",
        query={
            "workflow_id": 7,
            "user_id": 8,
            "workflow_run_id": 999,
            "organization_id": 11,
        },
        form_data={"CallSid": "call-xyz"},
    )

    with patch(
        "api.services.telephony.providers.tone.routes.db_client"
    ) as db_client:
        db_client.get_workflow_run_by_id = AsyncMock(return_value=None)
        db_client.update_workflow_run = AsyncMock()

        result = await handle_tone_webhook(
            workflow_id=7,
            user_id=8,
            workflow_run_id=999,
            organization_id=11,
            request=request,
        )

    assert result == {"status": "ignored", "reason": "workflow_run_not_found"}
    db_client.update_workflow_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_tone_status_callback_processes_completed_call():
    provider = _provider()
    form_data = {
        "CallSid": "call-completed-1",
        "Status": "completed",
        "Duration": "45",
        "From": "+917314624707",
        "To": "+919876543210",
    }
    request = _request(
        path="/api/v1/telephony/tone/hangup-callback/123",
        query={},
        form_data=form_data,
    )

    with (
        patch("api.services.telephony.providers.tone.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.tone.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.tone.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_tone_hangup_callback(
            workflow_run_id=123, request=request
        )

    assert result == {"status": "success"}
    process_status.assert_awaited_once()
    args, _ = process_status.call_args
    run_id_arg, status_update = args
    assert run_id_arg == 123
    assert status_update.status == "completed"
    assert status_update.call_id == "call-completed-1"
    assert status_update.duration == "45"


@pytest.mark.asyncio
async def test_tone_status_callback_ignores_unknown_workflow_run():
    request = _request(
        path="/api/v1/telephony/tone/hangup-callback/999",
        query={},
        form_data={"CallSid": "call-x", "Status": "completed"},
    )

    with (
        patch("api.services.telephony.providers.tone.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.tone.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=None)

        result = await handle_tone_hangup_callback(
            workflow_run_id=999, request=request
        )

    assert result == {"status": "ignored", "reason": "workflow_run_not_found"}
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_tone_callback_with_secret_accepts_valid():
    secret = "s3cret-shared-token"
    provider = _provider(webhook_secret=secret)
    form_data = {
        "CallSid": "call-secret-ok",
        "Status": "completed",
        "Duration": "30",
    }
    request = _request(
        path="/api/v1/telephony/tone/hangup-callback/123",
        query={},
        form_data=form_data,
        headers={"X-Tone-Webhook-Secret": secret},
    )

    with (
        patch("api.services.telephony.providers.tone.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.tone.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.tone.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_tone_hangup_callback(
            workflow_run_id=123, request=request
        )

    assert result == {"status": "success"}
    process_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_tone_callback_with_secret_rejects_invalid():
    provider = _provider(webhook_secret="the-real-secret")
    request = _request(
        path="/api/v1/telephony/tone/hangup-callback/123",
        query={},
        form_data={"CallSid": "call-bad-secret", "Status": "completed"},
        headers={"X-Tone-Webhook-Secret": "wrong-secret"},
    )

    with (
        patch("api.services.telephony.providers.tone.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.tone.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.tone.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_tone_hangup_callback(
            workflow_run_id=123, request=request
        )

    assert result == {"status": "error", "reason": "invalid_signature"}
    process_status.assert_not_awaited()
