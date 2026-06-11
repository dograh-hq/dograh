import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from api.services.telephony.providers.voicelink.provider import VoiceLinkProvider
from api.services.telephony.providers.voicelink.routes import handle_voicelink_events


def _provider() -> VoiceLinkProvider:
    return VoiceLinkProvider(
        {
            "api_base": "https://app.voicelink.co.in/api",
            "username": "reseller-user",
            "password": "placeholder-password",
            "did_number": "919484959244",
            "from_numbers": ["919484959244"],
        }
    )


def _body(event: str = "call.completed") -> str:
    return json.dumps(
        {
            "event": event,
            "timestamp": "2026-06-11T10:01:08Z",
            "call": {
                "id": "5b2f9c1e-aaaa-bbbb-cccc-1234567890ab",
                "direction": "outbound",
                "callType": "outbound",
                "from": "919484959244",
                "to": "7340400524",
                "status": "completed",
                "hangupCause": "16",
                "durationSec": 60,
                "customParameters": {"workflow_run_id": 123},
            },
        },
        separators=(",", ":"),
    )


def _request(body: str) -> Request:
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
            "path": "/api/v1/telephony/voicelink/events/123",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_voicelink_events_route_processes_status_update():
    provider = _provider()
    body = _body("call.completed")

    with (
        patch(
            "api.services.telephony.providers.voicelink.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.voicelink.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.voicelink.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_voicelink_events(_request(body), workflow_run_id=123)

    assert result == {"status": "success"}
    process_status.assert_awaited_once()
    _, status_update = process_status.await_args.args
    assert status_update.status == "completed"
    assert status_update.call_id == "5b2f9c1e-aaaa-bbbb-cccc-1234567890ab"
    assert status_update.duration == "60"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event,expected_status",
    [
        ("call.initiated", "initiated"),
        ("call.ringing", "ringing"),
        ("call.answered", "in-progress"),
        ("call.ended", "completed"),
        ("call.failed", "failed"),
    ],
)
async def test_voicelink_events_route_maps_lifecycle_events(event, expected_status):
    provider = _provider()

    with (
        patch(
            "api.services.telephony.providers.voicelink.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.voicelink.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.voicelink.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_voicelink_events(
            _request(_body(event)), workflow_run_id=123
        )

    assert result == {"status": "success"}
    _, status_update = process_status.await_args.args
    assert status_update.status == expected_status


@pytest.mark.asyncio
async def test_voicelink_events_route_ignores_unknown_workflow_run():
    with (
        patch(
            "api.services.telephony.providers.voicelink.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.voicelink.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=None)

        result = await handle_voicelink_events(_request(_body()), workflow_run_id=123)

    assert result == {"status": "ignored", "reason": "workflow_run_not_found"}
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_voicelink_events_route_rejects_invalid_json_without_raising():
    with patch(
        "api.services.telephony.providers.voicelink.routes._process_status_update",
        new_callable=AsyncMock,
    ) as process_status:
        result = await handle_voicelink_events(
            _request("not-json{"), workflow_run_id=123
        )

    assert result == {"status": "error", "reason": "invalid_json"}
    process_status.assert_not_awaited()
