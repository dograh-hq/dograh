"""Tests for the Noveum integration package (api/services/integrations/noveum).

Covers the AGENTS.md testing minimum for a new integration:
- node spec property order + registry/masking wiring for the sensitive field
- node validation (required-when-enabled)
- create_runtime_sessions: disabled → [], enabled → one session
- the storage audio sink: object-store write + manifest entry
- run_completion: validation failure, disabled skip, missing snapshot,
  schema-version guard, and the happy path (audio upload + trace send)

The noveum-trace SDK itself is faked at the package's own seams
(build_deferred_observer / build_noveum_client), so these tests do not require
noveum-trace to be importable.
"""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.configuration.masking import mask_key, mask_workflow_definition
from api.services.integrations.noveum.collector import (
    NOVEUM_PAYLOAD_LOG_KEY,
    NOVEUM_SNAPSHOT_SCHEMA_VERSION,
    build_audio_storage_key,
    build_deferred_observer,
    build_payload_envelope,
    make_storage_audio_sink,
)
from api.services.integrations.noveum.completion import run_completion
from api.services.integrations.noveum.node import NODE, NoveumNodeData
from api.services.integrations.noveum.runtime import (
    NoveumRuntimeSession,
    create_runtime_sessions,
)
from api.services.workflow.node_specs import all_specs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow_def(nodes):
    return {"nodes": nodes, "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}


def _noveum_node(node_id="noveum-1", api_key="", **extra_data):
    data = {
        "name": "Noveum",
        "noveum_enabled": True,
        "noveum_project": "voice-agent",
        **extra_data,
    }
    if api_key:
        data["noveum_api_key"] = api_key
    return {
        "id": node_id,
        "type": "noveum",
        "position": {"x": 0, "y": 0},
        "data": data,
    }


def _graph_node(data: NoveumNodeData):
    return types.SimpleNamespace(node_type="noveum", data=data)


def _runtime_context(nodes):
    return types.SimpleNamespace(
        workflow_run_id=42,
        workflow_run=types.SimpleNamespace(mode="phonecall"),
        workflow_graph=types.SimpleNamespace(
            nodes={f"n{i}": node for i, node in enumerate(nodes)}
        ),
        run_definition=types.SimpleNamespace(version_number=3),
        user_config=types.SimpleNamespace(
            stt=types.SimpleNamespace(provider="deepgram", model="nova-3"),
            llm=types.SimpleNamespace(provider="openai", model="gpt-4o"),
            tts=types.SimpleNamespace(provider="elevenlabs", model="turbo"),
            realtime=None,
        ),
        is_realtime=False,
        context_messages_provider=lambda: [],
    )


def _node_data(**overrides):
    data = {
        "name": "Noveum",
        "noveum_enabled": True,
        "noveum_api_key": "nv-key",
        "noveum_project": "voice-agent",
        **overrides,
    }
    return NoveumNodeData.model_validate(data)


def _trace_snapshot(**attributes):
    return {
        "trace_id": "trace-1",
        "name": "pipecat.conversation",
        "start_time": "2026-01-01T00:00:00+00:00",
        "end_time": "2026-01-01T00:01:00+00:00",
        "attributes": dict(attributes),
        "spans": [],
    }


def _envelope(manifest=None, schema_version=NOVEUM_SNAPSHOT_SCHEMA_VERSION):
    return {
        "schema_version": schema_version,
        "trace": _trace_snapshot(),
        "audio_manifest": manifest or [],
    }


def _completion_context(logs=None, recording_url="https://dograh/rec.wav"):
    return types.SimpleNamespace(
        workflow_run_id=42,
        workflow_run=types.SimpleNamespace(
            logs=logs or {}, recording_url=recording_url
        ),
        workflow_definition={},
        definition_id=1,
        organization_id=1,
        public_token=None,
    )


# ---------------------------------------------------------------------------
# Node spec + registry wiring
# ---------------------------------------------------------------------------


def test_noveum_spec_property_order_stable():
    spec = next(spec for spec in all_specs() if spec.name == "noveum")
    assert [prop.name for prop in spec.properties] == [
        "name",
        "noveum_enabled",
        "noveum_api_key",
        "noveum_project",
        "noveum_environment",
        "noveum_record_audio",
    ]


def test_noveum_node_registered_with_sensitive_api_key():
    assert NODE.type_name == "noveum"
    assert NODE.sensitive_fields == ("noveum_api_key",)

    from api.services.integrations import get_node_secret_fields

    assert "noveum_api_key" in get_node_secret_fields("noveum")


def test_enabled_node_requires_api_key_and_project():
    with pytest.raises(ValueError, match="noveum_api_key"):
        NoveumNodeData.model_validate({"name": "Noveum", "noveum_enabled": True})

    with pytest.raises(ValueError, match="noveum_project"):
        NoveumNodeData.model_validate(
            {"name": "Noveum", "noveum_enabled": True, "noveum_api_key": "nv-key"}
        )


def test_disabled_node_validates_without_credentials():
    data = NoveumNodeData.model_validate({"name": "Noveum", "noveum_enabled": False})
    assert data.noveum_enabled is False
    assert data.noveum_api_key is None


def test_masks_noveum_api_key():
    real_key = "noveum_live_abcdefghijklmnop"
    wf = _make_workflow_def([_noveum_node(api_key=real_key)])

    masked = mask_workflow_definition(wf)

    masked_key = masked["nodes"][0]["data"]["noveum_api_key"]
    assert masked_key == mask_key(real_key)
    assert masked_key.endswith("mnop")
    assert masked_key.startswith("*")
    assert real_key not in str(masked)


# ---------------------------------------------------------------------------
# Runtime sessions
# ---------------------------------------------------------------------------


def test_create_runtime_sessions_empty_without_noveum_node():
    context = _runtime_context([])
    assert create_runtime_sessions(context) == []


def test_create_runtime_sessions_skips_disabled_node():
    context = _runtime_context([_graph_node(_node_data(noveum_enabled=False))])
    assert create_runtime_sessions(context) == []


def test_create_runtime_sessions_builds_one_deferred_session():
    context = _runtime_context([_graph_node(_node_data())])

    with patch(
        "api.services.integrations.noveum.runtime.build_deferred_observer"
    ) as build_mock:
        sessions = create_runtime_sessions(context)

    assert len(sessions) == 1
    assert sessions[0].name == "noveum"
    kwargs = build_mock.call_args.kwargs
    assert kwargs["record_audio"] is True
    assert kwargs["audio_sink"] is not None


def test_create_runtime_sessions_disables_audio_sink_when_opted_out():
    context = _runtime_context(
        [_graph_node(_node_data(noveum_record_audio=False))]
    )

    with patch(
        "api.services.integrations.noveum.runtime.build_deferred_observer"
    ) as build_mock:
        sessions = create_runtime_sessions(context)

    assert len(sessions) == 1
    kwargs = build_mock.call_args.kwargs
    assert kwargs["record_audio"] is False
    assert kwargs["audio_sink"] is None


async def test_attach_registers_observer_and_wires_task_sync():
    observer = MagicMock()
    observer.attach_to_task_sync = MagicMock()
    session = NoveumRuntimeSession(observer, manifest=[], call_attributes={})
    task = MagicMock()

    session.attach(task)

    task.add_observer.assert_called_once_with(observer)
    observer.attach_to_task_sync.assert_called_once_with(task)


async def test_on_call_finished_returns_envelope_with_disposition():
    observer = MagicMock()
    observer._finish_conversation = AsyncMock()
    observer.build_payload_snapshot.return_value = _trace_snapshot()
    manifest = [{"audio_uuid": "a1", "storage_key": "k1", "kind": "tts"}]
    session = NoveumRuntimeSession(
        observer, manifest=manifest, call_attributes={"dograh.workflow_run_id": 42}
    )

    result = await session.on_call_finished(
        gathered_context={"call_disposition": "completed"}
    )

    observer._finish_conversation.assert_awaited_once()
    envelope = result[NOVEUM_PAYLOAD_LOG_KEY]
    assert envelope["schema_version"] == NOVEUM_SNAPSHOT_SCHEMA_VERSION
    assert envelope["audio_manifest"] == manifest
    attrs = envelope["trace"]["attributes"]
    assert attrs["dograh.workflow_run_id"] == 42
    assert attrs["dograh.call_disposition"] == "completed"


async def test_on_call_finished_none_when_no_trace():
    observer = MagicMock()
    observer._finish_conversation = AsyncMock()
    observer.build_payload_snapshot.return_value = None
    session = NoveumRuntimeSession(observer, manifest=[], call_attributes={})

    assert await session.on_call_finished(gathered_context={}) is None


# ---------------------------------------------------------------------------
# Storage audio sink
# ---------------------------------------------------------------------------


async def test_storage_sink_writes_wav_and_records_manifest():
    manifest: list[dict] = []
    sink = make_storage_audio_sink(42, manifest)
    fake_fs = MagicMock()
    fake_fs.acreate_file_from_bytes = AsyncMock(return_value=True)

    with patch("api.services.integrations.noveum.collector.storage_fs", fake_fs):
        ok = await sink(
            wav_bytes=b"RIFFwav",
            audio_uuid="uuid-1",
            kind="tts",
            trace_id="trace-1",
            span_id="span-1",
            metadata={"duration_ms": 120.0},
        )

    assert ok is True
    expected_key = build_audio_storage_key(42, "tts", "uuid-1")
    fake_fs.acreate_file_from_bytes.assert_awaited_once_with(expected_key, b"RIFFwav")
    assert manifest == [
        {
            "audio_uuid": "uuid-1",
            "storage_key": expected_key,
            "kind": "tts",
            "trace_id": "trace-1",
            "span_id": "span-1",
            "metadata": {"duration_ms": 120.0},
        }
    ]


async def test_storage_sink_failure_records_nothing():
    manifest: list[dict] = []
    sink = make_storage_audio_sink(42, manifest)
    fake_fs = MagicMock()
    fake_fs.acreate_file_from_bytes = AsyncMock(return_value=False)

    with patch("api.services.integrations.noveum.collector.storage_fs", fake_fs):
        ok = await sink(
            wav_bytes=b"RIFFwav",
            audio_uuid="uuid-1",
            kind="stt",
            trace_id="trace-1",
            span_id="span-1",
        )

    assert ok is False
    assert manifest == []


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

_BUILD_CLIENT = "api.services.integrations.noveum.completion.build_noveum_client"
_READ_AUDIO = "api.services.integrations.noveum.completion._read_stored_audio"


async def test_completion_records_validation_failure():
    context = _completion_context(logs={NOVEUM_PAYLOAD_LOG_KEY: _envelope()})
    nodes = [{"id": "n1", "type": "noveum", "data": {"noveum_enabled": True}}]

    results = await run_completion(nodes, context)

    assert results["noveum_n1"] == {"error": "validation_failed"}


async def test_completion_skips_disabled_node():
    context = _completion_context(logs={NOVEUM_PAYLOAD_LOG_KEY: _envelope()})
    nodes = [
        {
            "id": "n1",
            "type": "noveum",
            "data": {"name": "Noveum", "noveum_enabled": False},
        }
    ]

    results = await run_completion(nodes, context)

    assert results == {}


async def test_completion_reports_no_trace_when_snapshot_absent():
    # Runs that never start a pipeline legitimately have no payload — this is
    # a status, not an error (no false alarms in operator-facing annotations).
    context = _completion_context(logs={})
    nodes = [_noveum_node(api_key="nv-key")]

    results = await run_completion(nodes, context)

    assert results["noveum_noveum-1"] == {"status": "no_trace"}


async def test_completion_rejects_unknown_schema_version():
    context = _completion_context(
        logs={NOVEUM_PAYLOAD_LOG_KEY: _envelope(schema_version=999)}
    )
    nodes = [_noveum_node(api_key="nv-key")]

    results = await run_completion(nodes, context)

    assert results["noveum_noveum-1"] == {"error": "unsupported_schema_version"}


async def test_completion_happy_path_uploads_audio_and_sends_trace():
    manifest = [
        {
            "audio_uuid": "uuid-1",
            "storage_key": "recordings/42/noveum/tts/uuid-1.wav",
            "kind": "tts",
            "trace_id": "trace-1",
            "span_id": "span-1",
            "metadata": {"duration_ms": 120.0},
        },
        {
            "audio_uuid": "uuid-2",
            "storage_key": "recordings/42/noveum/stt/uuid-2.wav",
            "kind": "stt",
            "trace_id": "trace-1",
            "span_id": "span-2",
            "metadata": {},
        },
    ]
    context = _completion_context(
        logs={NOVEUM_PAYLOAD_LOG_KEY: _envelope(manifest=manifest)}
    )
    nodes = [_noveum_node(api_key="nv-key")]
    client = MagicMock()

    with (
        patch(_BUILD_CLIENT, return_value=client) as build_mock,
        patch(_READ_AUDIO, new=AsyncMock(return_value=b"RIFFwav")),
    ):
        results = await run_completion(nodes, context)

    build_mock.assert_called_once_with(
        api_key="nv-key",
        project="voice-agent",
        environment="production",
        service_version=None,
    )
    # Direct, delivery-observed sends — one send_audio_sync per manifest entry.
    assert client.send_audio_sync.call_count == 2
    exported_uuids = {
        call.kwargs["audio_uuid"] for call in client.send_audio_sync.call_args_list
    }
    assert exported_uuids == {"uuid-1", "uuid-2"}

    client.send_trace_dict.assert_called_once()
    # Trace ships BEFORE the audio phase: a slow/stuck audio phase near the
    # arq job timeout must never starve the trace send.
    call_names = [name for name, _, _ in client.mock_calls]
    assert call_names.index("send_trace_dict") < call_names.index("send_audio_sync")
    sent_trace = client.send_trace_dict.call_args.args[0]
    assert sent_trace["trace_id"] == "trace-1"
    assert sent_trace["attributes"]["dograh.recording_url"] == "https://dograh/rec.wav"
    # Placeholder live-phase identity must be replaced with the node's values.
    assert sent_trace["attributes"]["noveum.project"] == "voice-agent"
    assert sent_trace["attributes"]["noveum.environment"] == "production"

    client.shutdown.assert_called_once()

    result = results["noveum_noveum-1"]
    assert result["status"] == "delivered"
    assert result["audio_uploaded"] == 2
    assert result["audio_failed"] == 0


async def test_completion_passes_agent_version_as_service_version():
    # dograh.agent_version stamped in the live phase must surface as the
    # completion client's service_version (stringified).
    envelope = {
        "schema_version": NOVEUM_SNAPSHOT_SCHEMA_VERSION,
        "trace": _trace_snapshot(**{"dograh.agent_version": 2}),
        "audio_manifest": [],
    }
    context = _completion_context(logs={NOVEUM_PAYLOAD_LOG_KEY: envelope})
    nodes = [_noveum_node(api_key="nv-key")]
    client = MagicMock()

    with patch(_BUILD_CLIENT, return_value=client) as build_mock:
        await run_completion(nodes, context)

    assert build_mock.call_args.kwargs["service_version"] == "2"


async def test_completion_audio_read_failure_still_reports_delivered():
    manifest = [
        {
            "audio_uuid": "uuid-1",
            "storage_key": "recordings/42/noveum/tts/uuid-1.wav",
            "kind": "tts",
            "trace_id": "trace-1",
            "span_id": "span-1",
            "metadata": {},
        }
    ]
    context = _completion_context(
        logs={NOVEUM_PAYLOAD_LOG_KEY: _envelope(manifest=manifest)}
    )
    nodes = [_noveum_node(api_key="nv-key")]
    client = MagicMock()

    with (
        patch(_BUILD_CLIENT, return_value=client),
        patch(_READ_AUDIO, new=AsyncMock(return_value=None)),
    ):
        results = await run_completion(nodes, context)

    client.send_audio_sync.assert_not_called()
    client.send_trace_dict.assert_called_once()
    result = results["noveum_noveum-1"]
    assert result["status"] == "delivered"
    assert result["audio_uploaded"] == 0
    assert result["audio_failed"] == 1


async def test_completion_counts_real_upload_failures():
    # send_audio_sync raising (direct send observed a failure) → counted as
    # failed, trace still delivered.
    manifest = [
        {
            "audio_uuid": "uuid-ok",
            "storage_key": "recordings/42/noveum/tts/uuid-ok.wav",
            "kind": "tts",
            "trace_id": "trace-1",
            "span_id": "span-1",
            "metadata": {},
        },
        {
            "audio_uuid": "uuid-bad",
            "storage_key": "recordings/42/noveum/stt/uuid-bad.wav",
            "kind": "stt",
            "trace_id": "trace-1",
            "span_id": "span-2",
            "metadata": {},
        },
    ]
    context = _completion_context(
        logs={NOVEUM_PAYLOAD_LOG_KEY: _envelope(manifest=manifest)}
    )
    nodes = [_noveum_node(api_key="nv-key")]
    client = MagicMock()

    def _send(**kwargs):
        if kwargs["audio_uuid"] == "uuid-bad":
            raise RuntimeError("401")

    client.send_audio_sync.side_effect = _send

    with (
        patch(_BUILD_CLIENT, return_value=client),
        patch(_READ_AUDIO, new=AsyncMock(return_value=b"RIFFwav")),
    ):
        results = await run_completion(nodes, context)

    result = results["noveum_noveum-1"]
    assert result["status"] == "delivered"
    assert result["audio_uploaded"] == 1
    assert result["audio_failed"] == 1


async def test_completion_trace_send_failure_is_not_reported_delivered():
    # The trace POST failing must surface as an error — never "delivered" —
    # and must skip the audio phase entirely (no orphaned audio on Noveum
    # for a trace that was never delivered).
    manifest = [
        {
            "audio_uuid": "uuid-1",
            "storage_key": "recordings/42/noveum/tts/uuid-1.wav",
            "kind": "tts",
            "trace_id": "trace-1",
            "span_id": "span-1",
            "metadata": {},
        }
    ]
    context = _completion_context(
        logs={NOVEUM_PAYLOAD_LOG_KEY: _envelope(manifest=manifest)}
    )
    nodes = [_noveum_node(api_key="nv-key")]
    client = MagicMock()
    client.send_trace_dict.side_effect = RuntimeError("api rejected trace")

    with patch(_BUILD_CLIENT, return_value=client):
        results = await run_completion(nodes, context)

    result = results["noveum_noveum-1"]
    assert "error" in result
    assert result.get("status") != "delivered"
    client.send_audio_sync.assert_not_called()
    client.shutdown.assert_called_once()


async def test_completion_malformed_envelope_yields_error_without_raising():
    # A persisted envelope that is present but not a dict must not crash the
    # whole handler (the .get() calls run outside the per-node try/except) —
    # each enabled node records a bounded error and export is skipped.
    context = _completion_context(logs={NOVEUM_PAYLOAD_LOG_KEY: "not-a-dict"})
    nodes = [_noveum_node(api_key="nv-key")]

    results = await run_completion(nodes, context)

    assert results["noveum_noveum-1"] == {"error": "malformed_payload"}


async def test_completion_counts_non_dict_manifest_entry_as_failure():
    # A non-dict manifest entry must be counted as an individual failure, not
    # raise inside asyncio.gather after the trace POST already succeeded.
    envelope = {
        "schema_version": NOVEUM_SNAPSHOT_SCHEMA_VERSION,
        "trace": _trace_snapshot(),
        "audio_manifest": ["not-a-dict"],
    }
    context = _completion_context(logs={NOVEUM_PAYLOAD_LOG_KEY: envelope})
    nodes = [_noveum_node(api_key="nv-key")]
    client = MagicMock()

    with patch(_BUILD_CLIENT, return_value=client):
        results = await run_completion(nodes, context)

    client.send_trace_dict.assert_called_once()
    client.send_audio_sync.assert_not_called()
    result = results["noveum_noveum-1"]
    assert result["status"] == "delivered"
    assert result["audio_uploaded"] == 0
    assert result["audio_failed"] == 1


async def test_read_stored_audio_cleans_temp_on_download_error(tmp_path):
    # adownload_file raising after the temp WAV was created must not leak the
    # temp file.
    from api.services.integrations.noveum import completion as completion_mod

    seg = tmp_path / "seg.wav"

    def _make():
        seg.write_bytes(b"")
        return str(seg)

    fake_fs = MagicMock()
    fake_fs.adownload_file = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch.object(completion_mod, "_make_temp_wav_path", _make),
        patch.object(completion_mod, "storage_fs", fake_fs),
    ):
        result = await completion_mod._read_stored_audio("some-key")

    assert result is None
    assert not seg.exists()


async def test_read_stored_audio_cleans_temp_on_cancellation(tmp_path):
    # CancelledError is a BaseException, so `except Exception` never sees it:
    # an arq job_timeout / worker shutdown cancelling the download must still
    # unlink the temp WAV, and must propagate rather than return None.
    from api.services.integrations.noveum import completion as completion_mod

    seg = tmp_path / "seg.wav"

    def _make():
        seg.write_bytes(b"")
        return str(seg)

    fake_fs = MagicMock()
    fake_fs.adownload_file = AsyncMock(side_effect=asyncio.CancelledError())

    with (
        patch.object(completion_mod, "_make_temp_wav_path", _make),
        patch.object(completion_mod, "storage_fs", fake_fs),
    ):
        with pytest.raises(asyncio.CancelledError):
            await completion_mod._read_stored_audio("some-key")

    assert not seg.exists()


# ---------------------------------------------------------------------------
# Collector: envelope sanitization + deferred observer construction
# ---------------------------------------------------------------------------


async def test_envelope_sanitized_to_pure_json():
    # A non-JSON value in span attributes must not survive into the envelope:
    # workflow_run.logs is a JSON column and one bad value would make the
    # whole commit (including dograh's own keys) fail. default=str converts.
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    observer = MagicMock()
    observer.build_payload_snapshot.return_value = {
        "trace_id": "trace-1",
        "attributes": {},
        "spans": [{"attributes": {"weird.timestamp": _dt(2026, 1, 1, tzinfo=_UTC)}}],
    }

    envelope = build_payload_envelope(observer, [], {"dograh.workflow_run_id": 42})

    assert envelope is not None
    stamped = envelope["trace"]["spans"][0]["attributes"]["weird.timestamp"]
    assert isinstance(stamped, str)
    import json as _json

    _json.dumps(envelope)  # must not raise: pure-JSON by construction


def test_build_deferred_observer_wires_transport_model():
    # Needs the real SDK (skipped where noveum-trace isn't installed).
    pytest.importorskip("noveum_trace")
    pytest.importorskip("pipecat.frames.frames")
    from noveum_trace.transport.deferred_transport import DeferredTransport

    sink = AsyncMock(return_value=True)
    obs = build_deferred_observer(record_audio=True, audio_sink=sink)

    assert obs._deferred is True
    assert obs._audio_sink is sink
    assert obs._register_finish_safety_net is False
    assert obs._injected_client is not None
    assert isinstance(obs._injected_client.transport, DeferredTransport)


async def test_sanitizer_handles_nan_and_nonstring_keys():
    # The two gaps a plain json.dumps(default=str) round-trip misses: NaN
    # floats (pass dumps, break the Postgres JSON commit) and non-string dict
    # keys (raise TypeError in dumps, dropping the whole envelope).
    observer = MagicMock()
    observer.build_payload_snapshot.return_value = {
        "trace_id": "trace-1",
        "attributes": {},
        "spans": [
            {"attributes": {"latency": float("nan"), (1, 2): "tuple-keyed"}}
        ],
    }

    envelope = build_payload_envelope(observer, [], {})

    assert envelope is not None
    attrs = envelope["trace"]["spans"][0]["attributes"]
    assert attrs["latency"] == "nan"
    assert attrs["(1, 2)"] == "tuple-keyed"
    import json as _json

    _json.dumps(envelope, allow_nan=False)  # strictly JSON-native now
