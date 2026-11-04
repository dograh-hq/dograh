"""Cloudonix session callbacks and transfer diagnostics.

Outbound calls report their Cloudonix session status to a signed
status-callback URL so an unanswered call ends like it does on every other
provider. Transfers record their significant events on the run, including the
caller's own conference join, which is the only proof that the caller reached
the advocate.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.db.models import (
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import TelephonyCallStatus
from api.services.telephony.call_transfer_manager import (
    TRANSFER_RUN_INDEX_TTL_S,
    CallTransferManager,
)
from api.services.telephony.providers.cloudonix import routes, transfer_log
from api.services.telephony.providers.cloudonix.provider import CloudonixProvider
from api.services.telephony.providers.cloudonix.strategies import (
    CloudonixConferenceStrategy,
)
from api.services.telephony.transfer_event_protocol import (
    TransferContext,
    TransferEventType,
    TransferRedisChannels,
)

DOMAIN = "acme.cloudonix.net"
ANSWERED_AT = 1792587833239
ENDED_AT = 1792587862726


def _provider(bearer_token="secret-token", domain=DOMAIN) -> CloudonixProvider:
    return CloudonixProvider(
        {
            "bearer_token": bearer_token,
            "domain_id": domain,
            "from_numbers": ["+15551230001"],
        }
    )


def _request(body: object, path: str = "/") -> Request:
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("example.test", 443),
            "path": path,
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


class _FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._text

    async def json(self):
        return self._payload


class _RecordingSession:
    def __init__(self, response):
        self.response = response
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, endpoint, **kwargs):
        self.posts.append((endpoint, kwargs))
        return self.response


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("new", TelephonyCallStatus.INITIATED),
        ("processing", TelephonyCallStatus.INITIATED),
        ("ringing", TelephonyCallStatus.RINGING),
        ("connected", TelephonyCallStatus.IN_PROGRESS),
        ("noanswer", TelephonyCallStatus.NO_ANSWER),
        ("busy", TelephonyCallStatus.BUSY),
        ("cancel", TelephonyCallStatus.CANCELED),
        ("external", TelephonyCallStatus.CANCELED),
        ("nocredit", TelephonyCallStatus.FAILED),
        ("error", TelephonyCallStatus.ERROR),
        ("BUSY", TelephonyCallStatus.BUSY),
    ],
)
def test_unanswered_session_statuses_map_to_call_statuses(raw, expected):
    parsed = _provider().parse_status_callback({"token": "sess-1", "status": raw})

    assert parsed["status"] == expected
    assert parsed["call_id"] == "sess-1"
    assert parsed["duration"] == "0"


@pytest.mark.parametrize("raw", ["answer", "error", "external", "nocredit"])
def test_a_session_that_was_answered_always_completes(raw):
    """Once connected, the pipeline owns the outcome: a not-connected status
    here would overwrite the run's disposition."""
    parsed = _provider().parse_status_callback(
        {
            "token": "sess-1",
            "status": raw,
            "callAnswerTime": ANSWERED_AT,
            "callEndTime": ENDED_AT,
        }
    )

    assert parsed["status"] == TelephonyCallStatus.COMPLETED
    assert parsed["duration"] == "29"


def test_parsed_callback_keeps_diagnostics_but_not_the_session_profile():
    """The session profile carries the CXML, whose stream URL holds the media
    capability token; it must never reach the run's logs."""
    parsed = _provider().parse_status_callback(
        {
            "token": "sess-1",
            "status": "busy",
            "callerId": "+15551230001",
            "destination": "+15551230002",
            "direction": "application",
            "startTime": "2026-10-21T13:03:43.152Z",
            "lastError": "SIP 486",
            "profile": {
                "app-call-data": {"cxml": '<Stream url="wss://x/ws/1/2/3/SECRET"/>'}
            },
        }
    )

    assert parsed["extra"] == {
        "cloudonix_status": "busy",
        "session": "sess-1",
        "start_time": "2026-10-21T13:03:43.152Z",
        "last_error": "SIP 486",
    }
    assert "SECRET" not in json.dumps(parsed)
    assert parsed["from_number"] == "+15551230001"
    assert parsed["to_number"] == "+15551230002"


# ---------------------------------------------------------------------------
# Signed status-callback URL
# ---------------------------------------------------------------------------


def test_status_callback_token_is_bound_to_run_domain_and_key():
    provider = _provider()
    url = provider.build_status_callback_url("https://api.example.com", 9)
    prefix = "https://api.example.com/api/v1/telephony/cloudonix/status-callback/9/"
    assert url.startswith(prefix)
    token = url.removeprefix(prefix)

    assert provider.verify_status_callback_token(9, token)
    assert not provider.verify_status_callback_token(10, token)
    assert not _provider(bearer_token="other").verify_status_callback_token(9, token)
    assert not _provider(domain="other.cloudonix.net").verify_status_callback_token(
        9, token
    )
    assert not provider.verify_status_callback_token(9, None)
    assert not provider.verify_status_callback_token(9, "tökén")
    assert not _provider(bearer_token=None).verify_status_callback_token(9, token)


@pytest.mark.asyncio
async def test_outbound_call_registers_the_signed_status_callback():
    provider = _provider()
    session = _RecordingSession(
        _FakeResponse(200, {"token": "sess-1", "domainId": 3, "subscriberId": None})
    )

    with (
        patch(
            "api.services.telephony.providers.cloudonix.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.cloudonix.provider.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ws.test")),
        ),
    ):
        await provider.initiate_call(
            "+15551230002",
            "unused",
            workflow_run_id=9,
            workflow_id=7,
            organization_id=4,
        )

    callback = session.posts[0][1]["json"]["callback"]
    assert callback == provider.build_status_callback_url("https://api.example.com", 9)


# ---------------------------------------------------------------------------
# Status-callback route
# ---------------------------------------------------------------------------


def _run(**overrides):
    fields = {
        "id": 9,
        "workflow_id": 3,
        "gathered_context": {"call_id": "sess-1"},
        "logs": {},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


async def _post_status(body, *, run=None, provider=None, token=None):
    provider = provider or _provider()
    run = run or _run()
    if token is None:
        token = provider._status_callback_token(run.id)
    db = SimpleNamespace(
        get_workflow_run_by_id=AsyncMock(return_value=run),
        get_workflow_by_id=AsyncMock(return_value=SimpleNamespace(organization_id=4)),
    )
    process = AsyncMock()
    with (
        patch.object(routes, "db_client", db),
        patch.object(
            routes,
            "get_telephony_provider_for_run",
            new=AsyncMock(return_value=provider),
        ),
        patch.object(routes, "_process_status_update", new=process),
    ):
        result = await routes.handle_cloudonix_status_callback(
            run.id, token, _request(body)
        )
    return result, process


@pytest.mark.asyncio
async def test_unanswered_outbound_call_is_processed_like_other_providers():
    result, process = await _post_status({"token": "sess-1", "status": "busy"})

    assert result == {"status": "success"}
    run_id, update = process.await_args.args
    assert run_id == 9
    assert update.status == TelephonyCallStatus.BUSY
    assert update.call_id == "sess-1"
    assert update.duration == "0"
    assert update.extra["cloudonix_status"] == "busy"


@pytest.mark.asyncio
async def test_status_callback_accepts_a_one_element_list_payload():
    result, process = await _post_status([{"token": "sess-1", "status": "noanswer"}])

    assert result == {"status": "success"}
    assert process.await_args.args[1].status == TelephonyCallStatus.NO_ANSWER


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["0" * 64, "not-a-token"], ids=["wrong", "garbage"])
async def test_status_callback_rejects_a_bad_token_as_not_found(token):
    with pytest.raises(HTTPException) as exc:
        await _post_status({"token": "sess-1", "status": "busy"}, token=token)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_status_callback_rejects_a_run_on_another_provider():
    other = SimpleNamespace(verify_status_callback_token=lambda *_: True)
    with pytest.raises(HTTPException) as exc:
        await _post_status(
            {"token": "sess-1", "status": "busy"}, provider=other, token="any"
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_status_callback_for_another_session_is_ignored():
    result, process = await _post_status({"token": "sess-other", "status": "busy"})

    assert result == {"status": "ignored", "reason": "session_mismatch"}
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_callback_before_the_session_is_stored_is_accepted():
    """The 'created' callback can land before initiate_call's caller has
    persisted the session token; the signed URL already binds it to the run."""
    result, process = await _post_status(
        {"token": "sess-1", "status": "new"}, run=_run(gathered_context={})
    )

    assert result == {"status": "success"}
    assert process.await_args.args[1].status == TelephonyCallStatus.INITIATED


@pytest.mark.asyncio
async def test_repeated_session_status_is_not_recorded_again():
    run = _run(logs={"telephony_status_callbacks": [{"status": "in-progress"}]})
    result, process = await _post_status(
        {"token": "sess-1", "status": "connected"}, run=run
    )

    assert result == {"status": "ignored", "reason": "unchanged"}
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_update_after_the_final_status_is_dropped():
    """Cloudonix delivers out of order; a trailing 'connected' must not become
    the last callback, which campaign failure counting reads."""
    run = _run(
        logs={
            "telephony_status_callbacks": [
                {"status": "ringing"},
                {"status": "no-answer"},
            ]
        }
    )
    result, process = await _post_status(
        {"token": "sess-1", "status": "ringing"}, run=run
    )

    assert result == {"status": "ignored", "reason": "unchanged"}
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_callback_without_a_status_is_ignored():
    result, process = await _post_status({"token": "sess-1"})

    assert result == {"status": "ignored", "reason": "no_status"}
    process.assert_not_awaited()


# ---------------------------------------------------------------------------
# Transfer callbacks
# ---------------------------------------------------------------------------


def _transfer_context(**overrides):
    fields = {
        "transfer_id": "transfer-1",
        "call_sid": None,
        "target_number": "+15551230002",
        "tool_uuid": "tool-1",
        "original_call_sid": "caller-sess",
        "conference_name": "transfer-caller-sess",
        "initiated_at": 0.0,
        "workflow_run_id": 9,
    }
    fields.update(overrides)
    return TransferContext(**fields)


async def _post_transfer(handler, body, *, context):
    manager = SimpleNamespace(
        get_transfer_context=AsyncMock(return_value=context),
        publish_transfer_event=AsyncMock(),
    )
    record = AsyncMock()
    with (
        patch.object(
            routes, "get_call_transfer_manager", new=AsyncMock(return_value=manager)
        ),
        patch.object(routes, "record_transfer_callback", new=record),
    ):
        result = await handler("transfer-1", _request(body))
    return result, manager, record


@pytest.mark.asyncio
async def test_destination_join_still_completes_the_transfer_and_is_recorded():
    body = {"StatusCallbackEvent": "participant-join", "Session": "dest-sess"}
    result, manager, record = await _post_transfer(
        routes.handle_cloudonix_transfer_result, body, context=_transfer_context()
    )

    assert result == {"status": "completed"}
    event = manager.publish_transfer_event.await_args.args[0]
    assert event.type == TransferEventType.DESTINATION_ANSWERED
    assert event.transfer_call_sid == "dest-sess"
    record.assert_awaited_once_with("transfer-1", "destination", body)


@pytest.mark.asyncio
async def test_destination_events_after_the_transfer_settled_are_still_recorded():
    body = {"status": "answer", "token": "dest-sess"}
    result, manager, record = await _post_transfer(
        routes.handle_cloudonix_transfer_result, body, context=None
    )

    assert result == {"status": "ignored", "reason": "unknown_transfer"}
    manager.publish_transfer_event.assert_not_awaited()
    record.assert_awaited_once_with("transfer-1", "destination", body)


@pytest.mark.asyncio
async def test_destination_leave_does_not_drive_the_transfer():
    body = {"StatusCallbackEvent": "participant-leave", "Session": "dest-sess"}
    result, manager, _ = await _post_transfer(
        routes.handle_cloudonix_transfer_result, body, context=_transfer_context()
    )

    assert result == {"status": "pending"}
    manager.publish_transfer_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_caller_join_is_recorded_and_never_read_as_the_destination_answering():
    body = {"StatusCallbackEvent": "participant-join", "Session": "caller-sess"}
    result, manager, record = await _post_transfer(
        routes.handle_cloudonix_transfer_caller_event,
        body,
        context=_transfer_context(),
    )

    assert result == {"status": "recorded"}
    manager.publish_transfer_event.assert_not_awaited()
    record.assert_awaited_once_with("transfer-1", "caller", body)


# ---------------------------------------------------------------------------
# Transfer event log
# ---------------------------------------------------------------------------


async def _record(leg, data, existing, run_id=9):
    manager = SimpleNamespace(get_transfer_run_id=AsyncMock(return_value=run_id))
    appended = []

    async def append(run, key, entry, should_append=None):
        if should_append is None or should_append(existing):
            appended.append((run, key, entry))
            existing.append(entry)
            return True
        return False

    db = SimpleNamespace(append_workflow_run_log_entry=AsyncMock(side_effect=append))
    with (
        patch.object(
            transfer_log,
            "get_call_transfer_manager",
            new=AsyncMock(return_value=manager),
        ),
        patch.object(transfer_log, "db_client", db),
    ):
        await transfer_log.record_transfer_callback("transfer-1", leg, data)
    return appended


@pytest.mark.asyncio
async def test_transfer_log_keeps_status_changes_and_conference_events_only():
    existing: list[dict] = []
    callbacks = [
        ("destination", {"status": "new", "token": "dest-sess"}),
        ("destination", {"status": "processing", "token": "dest-sess"}),
        ("destination", {"status": "ringing", "token": "dest-sess"}),
        ("destination", {"status": "ringing", "token": "dest-sess"}),
        ("destination", {"status": "connected", "token": "dest-sess"}),
        ("destination", {"StatusCallbackEvent": "participant-join", "Session": "d"}),
        ("destination", {"StatusCallbackEvent": "participant-join", "Session": "d"}),
        (
            "destination",
            {
                "StatusCallbackEvent": "participant-leave",
                "Session": "d",
                "ParticipantCallStatus": "completed",
                "ReasonParticipantLeft": "participant hung up",
            },
        ),
        (
            "destination",
            {
                "status": "answer",
                "token": "dest-sess",
                "answerTime": "2026-10-21T13:03:53.239Z",
                "endTime": "2026-10-21T13:04:22.726Z",
                "profile": {"cxml": "SECRET"},
            },
        ),
        # Delivered after the final status, as Cloudonix does.
        ("destination", {"status": "connected", "token": "dest-sess"}),
    ]
    for leg, data in callbacks:
        await _record(leg, data, existing)

    assert [(e["source"], e["event"]) for e in existing] == [
        ("session", "ringing"),
        ("session", "connected"),
        ("conference", "participant-join"),
        ("conference", "participant-leave"),
        ("session", "answer"),
    ]
    leave = existing[3]
    assert leave["participant_status"] == "completed"
    assert leave["reason"] == "participant hung up"
    assert existing[4]["end_time"] == "2026-10-21T13:04:22.726Z"
    assert all(e["leg"] == "destination" for e in existing)
    assert "SECRET" not in json.dumps(existing)


@pytest.mark.asyncio
async def test_transfer_log_dedupes_per_leg():
    existing: list[dict] = []
    join = {"StatusCallbackEvent": "participant-join"}
    await _record("destination", {**join, "Session": "dest-sess"}, existing)
    await _record("caller", {**join, "Session": "caller-sess"}, existing)

    assert [(e["leg"], e["event"]) for e in existing] == [
        ("destination", "participant-join"),
        ("caller", "participant-join"),
    ]


@pytest.mark.asyncio
async def test_transfer_log_skips_transfers_without_a_known_run():
    existing: list[dict] = []
    appended = await _record(
        "destination", {"status": "ringing"}, existing, run_id=None
    )

    assert appended == []


@pytest.mark.asyncio
async def test_transfer_log_never_raises_into_the_transfer():
    manager = SimpleNamespace(
        get_transfer_run_id=AsyncMock(side_effect=RuntimeError("redis down"))
    )
    with patch.object(
        transfer_log, "get_call_transfer_manager", new=AsyncMock(return_value=manager)
    ):
        await transfer_log.record_transfer_callback(
            "transfer-1", "destination", {"status": "ringing"}
        )


# ---------------------------------------------------------------------------
# Conference CXML for both legs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destination_conference_reports_join_and_leave():
    provider = _provider()
    session = _RecordingSession(_FakeResponse(200, {"token": "dest-sess"}))

    with (
        patch(
            "api.services.telephony.providers.cloudonix.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.cloudonix.provider.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ws.test")),
        ),
    ):
        await provider.transfer_call(
            destination="+15551230002",
            transfer_id="transfer-1",
            conference_name="transfer-caller-sess",
        )

    payload = session.posts[0][1]["json"]
    url = (
        "https://api.example.com/api/v1/telephony/cloudonix/transfer-result/transfer-1"
    )
    assert payload["callback"] == url
    assert f'statusCallback="{url}"' in payload["cxml"]
    assert 'statusCallbackEvent="join leave"' in payload["cxml"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_status", "switched"), [(200, True), (404, False)], ids=["ok", "rejected"]
)
async def test_caller_switch_reports_its_conference_events_and_is_recorded(
    http_status, switched
):
    strategy = CloudonixConferenceStrategy()
    session = _RecordingSession(_FakeResponse(http_status, text="no such session"))
    record = AsyncMock()

    with (
        patch("aiohttp.ClientSession", return_value=session),
        patch(
            "api.services.telephony.providers.cloudonix.strategies.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ws.test")),
        ),
        patch.object(
            strategy,
            "_find_transfer_context_for_call",
            new=AsyncMock(return_value=_transfer_context()),
        ),
        patch.object(strategy, "_cleanup_transfer_context", new=AsyncMock()),
        patch.object(transfer_log, "record_transfer_event", new=record),
    ):
        result = await strategy.execute_transfer(
            {"call_sid": "caller-sess", "account_sid": DOMAIN, "auth_token": "t"}
        )

    assert result is switched
    cxml = session.posts[0][1]["json"]["cxml"]
    caller_url = (
        "https://api.example.com/api/v1/telephony/cloudonix/transfer-result/"
        "transfer-1/caller"
    )
    assert f'statusCallback="{caller_url}"' in cxml
    assert 'statusCallbackEvent="join leave"' in cxml
    record.assert_awaited_once_with(
        "transfer-1",
        "caller",
        "dograh",
        "switch-requested",
        session="caller-sess",
        http_status=http_status,
        error=None if switched else "no such session",
    )


# ---------------------------------------------------------------------------
# Transfer -> run index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_run_index_outlives_the_transfer_context():
    redis = _FakeRedis()
    manager = CallTransferManager(redis_client=redis)

    await manager.store_transfer_context(_transfer_context())
    await manager.remove_transfer_context("transfer-1")

    assert await manager.get_transfer_context("transfer-1") is None
    assert await manager.get_transfer_run_id("transfer-1") == 9
    run_key = TransferRedisChannels.transfer_run_key("transfer-1")
    assert redis.ttls[run_key] == TRANSFER_RUN_INDEX_TTL_S


@pytest.mark.asyncio
async def test_transfer_without_a_run_is_not_indexed():
    manager = CallTransferManager(redis_client=_FakeRedis())

    await manager.store_transfer_context(_transfer_context(workflow_run_id=None))

    assert await manager.get_transfer_run_id("transfer-1") is None


# ---------------------------------------------------------------------------
# Atomic log append (real database)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_workflow_run_log_entry_appends_under_the_predicate(
    db_session, async_session
):
    suffix = uuid.uuid4().hex
    org = OrganizationModel(provider_id=f"cloudonix-log-{suffix}")
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id=f"cloudonix-log-{suffix}", selected_organization_id=org.id
    )
    async_session.add(user)
    await async_session.flush()
    workflow = WorkflowModel(
        name="Cloudonix log", user_id=user.id, organization_id=org.id
    )
    async_session.add(workflow)
    await async_session.flush()
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode="cloudonix",
        logs={"inbound_webhook": {"from": "+15551230001"}},
    )
    async_session.add(run)
    await async_session.flush()

    first = await db_session.append_workflow_run_log_entry(
        run.id, "transfer_events", {"event": "ringing"}
    )
    duplicate = await db_session.append_workflow_run_log_entry(
        run.id,
        "transfer_events",
        {"event": "ringing"},
        should_append=lambda existing: existing[-1]["event"] != "ringing",
    )
    second = await db_session.append_workflow_run_log_entry(
        run.id, "transfer_events", {"event": "connected"}
    )
    missing = await db_session.append_workflow_run_log_entry(
        -1, "transfer_events", {"event": "ringing"}
    )

    stored = await db_session.get_workflow_run_by_id(run.id)
    assert (first, duplicate, second, missing) == (True, False, True, False)
    assert stored.logs == {
        "inbound_webhook": {"from": "+15551230001"},
        "transfer_events": [{"event": "ringing"}, {"event": "connected"}],
    }
