import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api.enums import OrganizationConfigurationKey
from api.routes import organization as routes
from api.services.integrations.bigquery.sink import (
    BigQueryConfig,
    BigQuerySink,
    to_bigquery_row,
)
from api.services.observability.call_events import configuration, delivery
from api.services.observability.call_events.base import EventBuffer, ExportResult
from api.services.observability.call_events.configuration import CallEventsSettings
from api.services.observability.call_events.events import CallEvent


@pytest.fixture
def bigquery_config(monkeypatch):
    monkeypatch.setattr("api.constants.AUTH_PROVIDER", "local")
    return BigQueryConfig(
        table="example-project.analytics.call_events", auth_mode="application_default"
    )


def test_row_contract_and_stable_identity():
    event = CallEvent(
        event="latency_breakdown",
        ts=1726326192.123,
        run_id=42,
        org_id=7,
        workflow_id=3,
        turn=2,
        detail={"stt_ttfb_ms": 12.3, "llm_ttfb_ms": None},
    )
    row = to_bigquery_row(event)
    assert row == to_bigquery_row(event)
    assert row["insertId"] == event.event_id
    assert row["json"] == {
        "event": "latency_breakdown",
        "ts": "2024-09-14T15:03:12.123Z",
        "run_id": 42,
        "org_id": 7,
        "workflow_id": 3,
        "turn": 2,
        "severity": "info",
        "value_ms": None,
        "node_id": None,
        "node_name": None,
        "detail": '{"stt_ttfb_ms": 12.3, "llm_ttfb_ms": null}',
    }
    detail = {"text": "x" * 1025}
    event.detail = detail
    assert json.loads(to_bigquery_row(event)["json"]["detail"]) == {
        "truncated": True,
        "chars": len(json.dumps(detail)),
    }


async def test_bigquery_partial_failure_and_payload(bigquery_config):
    events = tuple(CallEvent(event="user_turn_started", run_id=1) for _ in range(3))
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "insertErrors": [
                    {"index": 1, "errors": [{"reason": "backendError"}]},
                    {"index": 2, "errors": [{"reason": "invalid"}]},
                ]
            },
        )

    sink = BigQuerySink(bigquery_config)
    await sink._client.aclose()
    sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    sink._headers = AsyncMock(return_value={"Authorization": "Bearer test"})
    try:
        result = await sink.export(events)
        assert result == ExportResult(
            frozenset({events[0].event_id}),
            frozenset({events[1].event_id}),
            frozenset({events[2].event_id}),
        )
        assert str(requests[0].url).endswith(
            "/projects/example-project/datasets/analytics/tables/call_events/insertAll"
        )
        payload = json.loads(requests[0].content)
        assert payload["rows"] == [to_bigquery_row(e) for e in events]
        assert payload["skipInvalidRows"] is True
    finally:
        await sink.close()


@pytest.mark.parametrize(
    "status,retry", [(429, True), (500, True), (403, False), (400, False)]
)
async def test_bigquery_http_failures(bigquery_config, status, retry):
    event = CallEvent(event="call_ended")
    sink = BigQuerySink(bigquery_config)
    await sink._client.aclose()
    sink._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    )
    sink._headers = AsyncMock(return_value={})
    try:
        result = await sink.export((event,))
        assert not result.accepted
        assert result.retryable == (
            frozenset({event.event_id}) if retry else frozenset()
        )
        assert result.rejected == (
            frozenset() if retry else frozenset({event.event_id})
        )
    finally:
        await sink.close()


@pytest.fixture
def call_events_schema():
    # Existing table metadata, independent of the adapter's accepted types.
    return [
        {"mode": "REQUIRED", "name": "ts", "type": "TIMESTAMP"},
        {"name": "run_id", "type": "INTEGER"},
        {"name": "org_id", "type": "INTEGER"},
        {"name": "workflow_id", "type": "INTEGER"},
        {"name": "turn", "type": "INTEGER"},
        {"mode": "REQUIRED", "name": "event", "type": "STRING"},
        {"name": "severity", "type": "STRING"},
        {"name": "value_ms", "type": "FLOAT"},
        {"name": "node_id", "type": "STRING"},
        {"name": "node_name", "type": "STRING"},
        {"name": "detail", "type": "JSON"},
    ]


@pytest.mark.parametrize("detail_type", ["JSON", "STRING"])
async def test_connection_check_is_read_only_and_validates_schema(
    bigquery_config, call_events_schema, detail_type
):
    fields = call_events_schema
    fields[-1]["type"] = detail_type
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"schema": {"fields": fields}})

    sink = BigQuerySink(bigquery_config)
    await sink._client.aclose()
    sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    sink._headers = AsyncMock(return_value={})
    try:
        await sink.validate_connection()
        fields[:] = []
        with pytest.raises(ValueError, match="schema"):
            await sink.validate_connection()
        assert all(r.method == "GET" for r in requests)
    finally:
        await sink.close()


@pytest.mark.parametrize(
    "schema_change,error",
    [
        ("missing", "Missing column 'detail'"),
        ("type", "Column 'detail' has type RECORD; expected JSON or STRING"),
        ("repeated", "Column 'detail' must not be REPEATED"),
        ("extra_required", "additional required columns"),
    ],
)
async def test_connection_check_rejects_incompatible_schema(
    bigquery_config, call_events_schema, schema_change, error
):
    fields = call_events_schema
    if schema_change == "missing":
        fields.pop()
    elif schema_change == "type":
        fields[-1]["type"] = "RECORD"
    elif schema_change == "repeated":
        fields[-1]["mode"] = "REPEATED"
    else:
        fields.append({"name": "extra", "type": "STRING", "mode": "REQUIRED"})

    sink = BigQuerySink(bigquery_config)
    await sink._client.aclose()
    sink._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"schema": {"fields": fields}})
        )
    )
    sink._headers = AsyncMock(return_value={})
    try:
        with pytest.raises(ValueError, match=error):
            await sink.validate_connection()
    finally:
        await sink.close()


def test_hosted_org_cannot_use_server_identity(monkeypatch):
    monkeypatch.setattr("api.constants.AUTH_PROVIDER", "stack")
    with pytest.raises(ValueError, match="self-hosted"):
        BigQueryConfig(
            table="example-project.analytics.call_events",
            auth_mode="application_default",
        )


def test_buffer_freezes_details_and_preserves_summary():
    buffer = EventBuffer(max_events=2)
    detail = {"chars": 5}
    buffer.emit(CallEvent(event="transcript_final", detail=detail))
    detail["chars"] = 99
    buffer.emit(CallEvent(event="user_turn_started"))
    buffer.emit(CallEvent(event="call_ended"))
    events = buffer.seal()
    buffer.emit(CallEvent(event="tts_stopped"))
    assert events[0].detail == {"chars": 5}
    assert [e.event for e in events] == ["transcript_final", "call_ended"]
    assert buffer.dropped == 1
    assert buffer.events == []


@pytest.fixture
async def preferences_client(monkeypatch):
    rows = {}
    user = SimpleNamespace(selected_organization_id=7)

    async def get(org_id, key):
        value = rows.get((org_id, key))
        return SimpleNamespace(value=value) if value is not None else None

    async def put(org_id, key, value):
        rows[(org_id, key)] = value

    async def delete(org_id, key):
        rows.pop((org_id, key), None)

    monkeypatch.setattr(configuration.db_client, "get_configuration", get)
    monkeypatch.setattr(configuration.db_client, "upsert_configuration", put)
    monkeypatch.setattr(configuration.db_client, "delete_configuration", delete)
    monkeypatch.setattr(BigQueryConfig, "credentials", lambda self: None)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_user] = lambda: user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, rows, user


def sink_settings():
    return {
        "enabled": True,
        "sink_type": "bigquery",
        "config": {
            "table": "example-project.analytics.call_events",
            "auth_mode": "service_account",
            "client_email": "test@example-project.iam.gserviceaccount.com",
            "private_key": "private-test-key",
        },
    }


async def test_settings_org_isolation_secret_roundtrip_and_validation(
    preferences_client,
):
    client, rows, user = preferences_client
    url = "/organizations/preferences"
    assert (await client.get(url)).json()["call_events"]["enabled"] is False
    body = {"call_events": sink_settings()}
    response = await client.put(url, json=body)
    assert response.status_code == 200
    assert "private-test-key" not in response.text
    assert (await client.get(url)).json()["call_events"]["config"][
        "private_key"
    ] == configuration.MASKED_SECRET
    body["call_events"]["config"]["private_key"] = configuration.MASKED_SECRET
    assert (await client.put(url, json=body)).status_code == 200
    assert (
        rows[(7, configuration.CONFIG_KEY)]["config"]["private_key"]
        == "private-test-key"
    )

    user.selected_organization_id = 8
    assert (await client.get(url)).json()["call_events"]["enabled"] is False
    assert (await client.put(url, json=body)).status_code == 422
    await client.put(url, json={"call_events": None})
    assert (7, configuration.CONFIG_KEY) in rows
    user.selected_organization_id = None
    assert (await client.get(url)).status_code == 400
    assert (await client.put(url, json=body)).status_code == 400


async def test_partial_preferences_updates_preserve_legacy_settings_and_sink(
    preferences_client,
):
    client, rows, _ = preferences_client
    url = "/organizations/preferences"
    legacy_key = OrganizationConfigurationKey.MODEL_CONFIGURATION_PREFERENCES.value
    prefs_key = OrganizationConfigurationKey.ORGANIZATION_PREFERENCES.value
    original = {
        "timezone": "Europe/Rome",
        "test_phone_number": "+390123456789",
        "external_pbx_integrations_enabled": True,
        "disposition_mapping_enabled": True,
        "disposition_mapping": {"user_hangup": "HUNGUP"},
    }
    rows[(7, legacy_key)] = original
    response = await client.put(url, json={"call_events": sink_settings()})
    assert response.status_code == 200
    assert all(response.json()[key] == value for key, value in original.items())
    assert (7, prefs_key) not in rows
    stored_sink = rows[(7, configuration.CONFIG_KEY)]

    response = await client.put(url, json={"timezone": "UTC"})
    assert response.status_code == 200
    assert rows[(7, prefs_key)] == {**original, "timezone": "UTC"}
    assert rows[(7, configuration.CONFIG_KEY)] is stored_sink
    response = await client.put(url, json={"test_phone_number": None})
    assert response.json()["test_phone_number"] is None
    assert response.json()["timezone"] == "UTC"
    assert rows[(7, configuration.CONFIG_KEY)] is stored_sink

    await client.put(url, json={"call_events": None})
    assert (7, configuration.CONFIG_KEY) not in rows
    response = await client.get(url)
    assert response.json()["call_events"] == {
        "enabled": False,
        "sink_type": None,
        "config": {},
    }
    assert response.json()["disposition_mapping"] == original["disposition_mapping"]
    assert "call_events" not in rows[(7, prefs_key)]


async def test_invalid_sink_does_not_write_preferences_or_echo_credentials(
    preferences_client,
):
    client, rows, _ = preferences_client
    settings = sink_settings()
    settings["config"]["table"] = "bad-table"
    response = await client.put(
        "/organizations/preferences", json={"timezone": "UTC", "call_events": settings}
    )
    assert response.status_code == 422
    assert "private-test-key" not in response.text
    assert not rows


async def test_connection_action_uses_unsaved_settings_without_writing(
    preferences_client, monkeypatch
):
    client, rows, user = preferences_client
    sink = SimpleNamespace(validate_connection=AsyncMock(), close=AsyncMock())
    spec = SimpleNamespace(config_model=BigQueryConfig, create=lambda _: sink)
    monkeypatch.setattr(configuration, "registration", lambda _: spec)
    rows[(7, configuration.CONFIG_KEY)] = sink_settings()
    # Resolve an existing masked secret while testing a different, unsaved table.
    body = sink_settings()
    body["config"]["private_key"] = configuration.MASKED_SECRET
    body["config"]["table"] = "example-project.analytics.other_table"
    spec.sensitive_fields = ("private_key",)
    response = await client.post("/organizations/call-events/test", json=body)
    assert response.status_code == 200
    sink.validate_connection.assert_awaited_once()
    sink.close.assert_awaited_once()
    assert rows[(7, configuration.CONFIG_KEY)] == sink_settings()
    sink.validate_connection.side_effect = ValueError("Invalid table schema")
    assert (
        await client.post("/organizations/call-events/test", json=body)
    ).status_code == 400
    assert sink.close.await_count == 2
    user.selected_organization_id = None
    assert (
        await client.post("/organizations/call-events/test", json=body)
    ).status_code == 400


async def test_delivery_retries_only_failed_rows_and_honors_revocation(
    monkeypatch, bigquery_config
):
    settings = CallEventsSettings(
        enabled=True, sink_type="bigquery", config=bigquery_config.model_dump()
    )
    events = (CallEvent(event="tts_started"), CallEvent(event="tts_stopped"))
    sink = SimpleNamespace(
        export=AsyncMock(
            return_value=ExportResult(
                frozenset({events[0].event_id}), frozenset({events[1].event_id})
            )
        ),
        close=AsyncMock(),
    )
    spec = SimpleNamespace(
        config_model=BigQueryConfig,
        create=lambda _: sink,
        destination_fields=("table",),
    )
    load = AsyncMock(
        side_effect=[settings, settings.model_copy(update={"enabled": False})]
    )
    monkeypatch.setattr(delivery, "load_settings", load)
    monkeypatch.setattr(delivery, "registration", lambda _: spec)
    await delivery._deliver(7, settings, events)
    sink.export.assert_awaited_once_with(events)
    assert load.await_args_list[0].args == (7,)
    assert load.await_count == 2
    sink.close.assert_awaited_once()


async def test_delivery_retry_preserves_identity(monkeypatch, bigquery_config):
    settings = CallEventsSettings(
        enabled=True, sink_type="bigquery", config=bigquery_config.model_dump()
    )
    events = (CallEvent(event="tts_started"), CallEvent(event="tts_stopped"))
    sink = SimpleNamespace(
        export=AsyncMock(
            side_effect=[
                ExportResult(
                    frozenset({events[0].event_id}), frozenset({events[1].event_id})
                ),
                ExportResult(frozenset({events[1].event_id})),
            ]
        ),
        close=AsyncMock(),
    )
    spec = SimpleNamespace(config_model=BigQueryConfig, create=lambda _: sink)
    monkeypatch.setattr(delivery, "load_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(delivery, "registration", lambda _: spec)
    await delivery._deliver(7, settings, events)
    assert sink.export.await_args_list[1].args == ((events[1],),)


async def test_shutdown_drains_and_closes_submission(monkeypatch, bigquery_config):
    settings = CallEventsSettings(
        enabled=True, sink_type="bigquery", config=bigquery_config.model_dump()
    )
    ran = asyncio.Event()

    async def deliver(*args):
        await asyncio.sleep(0)
        ran.set()

    monkeypatch.setattr(delivery, "_deliver", deliver)
    delivery.start()
    assert delivery.submit(7, settings, (CallEvent(event="call_ended"),), 100)
    await delivery.shutdown()
    assert ran.is_set()
    assert not delivery._tasks
    assert delivery._pending_bytes == 0
    assert not delivery.submit(7, settings, (CallEvent(event="call_ended"),), 100)
    delivery.start()
