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
    handle_plivo_transfer_result,
    handle_plivo_transfer_xml,
    handle_plivo_xml_webhook,
)
from api.services.telephony.transfer_event_protocol import TransferContext


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
            workflow_run_id=123,
            organization_id=11,
            request=request,
        )

    assert response.body == b"<Response/>"
    get_webhook_response.assert_awaited_once_with(7, 11, 123)
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


@pytest.mark.asyncio
async def test_destination_answer_is_published_once_for_duplicate_callbacks():
    provider = _provider()
    provider.verify_inbound_signature = AsyncMock(return_value=True)
    request = _request(
        path="/api/v1/telephony/plivo/transfer-xml/conference-1/transfer-1",
        query={},
        form_data={"CallUUID": "destination-call-uuid"},
    )
    context = TransferContext(
        transfer_id="transfer-1",
        call_sid="destination-request-uuid",
        target_number="+15551230003",
        tool_uuid="tool-1",
        original_call_sid="original-call-uuid",
        conference_name="conference-1",
        initiated_at=0,
        workflow_run_id=123,
    )
    manager = SimpleNamespace(
        get_transfer_context=AsyncMock(return_value=context),
        claim_transfer_step=AsyncMock(side_effect=[True, False]),
        publish_transfer_event=AsyncMock(),
        store_transfer_context=AsyncMock(),
    )

    with (
        patch(
            "api.services.telephony.providers.plivo.routes.get_call_transfer_manager",
            new=AsyncMock(return_value=manager),
        ),
        patch("api.services.telephony.providers.plivo.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.plivo.routes.get_telephony_provider_for_run",
            new=AsyncMock(return_value=provider),
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        first_response = await handle_plivo_transfer_xml(
            "conference-1", "transfer-1", request
        )
        duplicate_request = _request(
            path="/api/v1/telephony/plivo/transfer-xml/conference-1/transfer-1",
            query={},
            form_data={"CallUUID": "destination-call-uuid"},
        )
        duplicate_response = await handle_plivo_transfer_xml(
            "conference-1", "transfer-1", duplicate_request
        )

    assert b"<Conference" in first_response.body
    assert b"<Conference" in duplicate_response.body
    manager.publish_transfer_event.assert_awaited_once()
    event = manager.publish_transfer_event.await_args.args[0]
    assert event.action == "destination_answered"
    assert event.transfer_call_sid == "destination-call-uuid"
    manager.store_transfer_context.assert_awaited_once_with(context)


@pytest.mark.asyncio
async def test_original_leg_xml_does_not_publish_destination_answered():
    provider = _provider()
    provider.verify_inbound_signature = AsyncMock(return_value=True)
    request = _request(
        path="/api/v1/telephony/plivo/transfer-xml/conference-1/transfer-1",
        query={"leg": "aleg"},
        form_data={"CallUUID": "original-call-uuid"},
    )
    context = TransferContext(
        transfer_id="transfer-1",
        call_sid="destination-call-uuid",
        target_number="+15551230003",
        tool_uuid="tool-1",
        original_call_sid="original-call-uuid",
        conference_name="conference-1",
        initiated_at=0,
        workflow_run_id=123,
    )
    manager = SimpleNamespace(
        get_transfer_context=AsyncMock(return_value=context),
        publish_transfer_event=AsyncMock(),
    )

    with (
        patch(
            "api.services.telephony.providers.plivo.routes.get_call_transfer_manager",
            new=AsyncMock(return_value=manager),
        ),
        patch("api.services.telephony.providers.plivo.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.plivo.routes.get_telephony_provider_for_run",
            new=AsyncMock(return_value=provider),
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        response = await handle_plivo_transfer_xml(
            "conference-1", "transfer-1", request
        )

    assert b"<Conference" in response.body
    manager.publish_transfer_event.assert_not_awaited()


def _transfer_context() -> TransferContext:
    return TransferContext(
        transfer_id="transfer-1",
        call_sid="destination-call-uuid",
        target_number="+15551230003",
        tool_uuid="tool-1",
        original_call_sid="original-call-uuid",
        conference_name="conference-1",
        initiated_at=0,
        workflow_run_id=123,
    )


async def _invoke_transfer_result(
    hangup_cause: str,
    *,
    claim_result: bool = True,
):
    provider = _provider()
    provider.verify_inbound_signature = AsyncMock(return_value=True)
    request = _request(
        path="/api/v1/telephony/plivo/transfer-result/transfer-1",
        query={},
        form_data={
            "Event": "Hangup",
            "CallUUID": "destination-call-uuid",
            "HangupCause": hangup_cause,
        },
    )
    manager = SimpleNamespace(
        get_transfer_context=AsyncMock(return_value=_transfer_context()),
        claim_transfer_step=AsyncMock(return_value=claim_result),
        publish_transfer_event=AsyncMock(),
        remove_transfer_context=AsyncMock(),
    )

    with (
        patch(
            "api.services.telephony.providers.plivo.routes.get_call_transfer_manager",
            new=AsyncMock(return_value=manager),
        ),
        patch("api.services.telephony.providers.plivo.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.plivo.routes.get_telephony_provider_for_run",
            new=AsyncMock(return_value=provider),
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )
        response = await handle_plivo_transfer_result("transfer-1", request)

    return response, manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hangup_cause", "expected_reason"),
    [
        ("USER_BUSY", "busy"),
        ("NO_ANSWER", "no_answer"),
        ("ORIGINATOR_CANCEL", "no_answer"),
        ("CALL_REJECTED", "call_failed"),
    ],
)
async def test_transfer_result_publishes_terminal_failure(
    hangup_cause: str, expected_reason: str
):
    response, manager = await _invoke_transfer_result(hangup_cause)

    assert response == {"status": "success"}
    manager.publish_transfer_event.assert_awaited_once()
    event = manager.publish_transfer_event.await_args.args[0]
    assert event.reason == expected_reason
    assert event.transfer_call_sid == "destination-call-uuid"
    manager.remove_transfer_context.assert_awaited_once_with("transfer-1")


@pytest.mark.asyncio
async def test_transfer_result_normal_clearing_only_cleans_up_context():
    response, manager = await _invoke_transfer_result("NORMAL_CLEARING")

    assert response == {"status": "success"}
    manager.publish_transfer_event.assert_not_awaited()
    manager.claim_transfer_step.assert_not_awaited()
    manager.remove_transfer_context.assert_awaited_once_with("transfer-1")


@pytest.mark.asyncio
async def test_duplicate_transfer_failure_is_not_published_or_cleaned_up_twice():
    response, manager = await _invoke_transfer_result("USER_BUSY", claim_result=False)

    assert response == {"status": "success"}
    manager.publish_transfer_event.assert_not_awaited()
    manager.remove_transfer_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_result_without_hangup_cause_remains_pending():
    response, manager = await _invoke_transfer_result("")

    assert response == {"status": "pending"}
    manager.claim_transfer_step.assert_not_awaited()
    manager.publish_transfer_event.assert_not_awaited()
    manager.remove_transfer_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_result_rejects_unknown_transfer_id():
    request = _request(
        path="/api/v1/telephony/plivo/transfer-result/missing-transfer",
        query={},
        form_data={"Event": "Hangup", "HangupCause": "USER_BUSY"},
    )
    manager = SimpleNamespace(
        get_transfer_context=AsyncMock(return_value=None),
        publish_transfer_event=AsyncMock(),
    )

    with patch(
        "api.services.telephony.providers.plivo.routes.get_call_transfer_manager",
        new=AsyncMock(return_value=manager),
    ):
        response = await handle_plivo_transfer_result("missing-transfer", request)

    assert response == {"status": "error", "reason": "invalid_transfer_id"}
    manager.publish_transfer_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_result_rejects_invalid_signature():
    provider = _provider()
    provider.verify_inbound_signature = AsyncMock(return_value=False)
    request = _request(
        path="/api/v1/telephony/plivo/transfer-result/transfer-1",
        query={},
        form_data={
            "Event": "Hangup",
            "CallUUID": "destination-call-uuid",
            "HangupCause": "USER_BUSY",
        },
    )
    manager = SimpleNamespace(
        get_transfer_context=AsyncMock(return_value=_transfer_context()),
        claim_transfer_step=AsyncMock(),
        publish_transfer_event=AsyncMock(),
        remove_transfer_context=AsyncMock(),
    )

    with (
        patch(
            "api.services.telephony.providers.plivo.routes.get_call_transfer_manager",
            new=AsyncMock(return_value=manager),
        ),
        patch("api.services.telephony.providers.plivo.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.plivo.routes.get_telephony_provider_for_run",
            new=AsyncMock(return_value=provider),
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )
        response = await handle_plivo_transfer_result("transfer-1", request)

    assert response == {"status": "error", "reason": "invalid_signature"}
    manager.claim_transfer_step.assert_not_awaited()
    manager.publish_transfer_event.assert_not_awaited()
    manager.remove_transfer_context.assert_not_awaited()
