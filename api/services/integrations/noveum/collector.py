from __future__ import annotations

import math
from typing import Any, Awaitable, Callable

from loguru import logger

from api.services.storage import storage_fs

# Version of the {"trace", "audio_manifest"} envelope persisted to
# workflow_run.logs. Bump on any breaking shape change so completion can
# refuse snapshots it does not understand.
NOVEUM_SNAPSHOT_SCHEMA_VERSION = 1

NOVEUM_PAYLOAD_LOG_KEY = "noveum_payload"


def build_audio_storage_key(workflow_run_id: int, kind: str, audio_uuid: str) -> str:
    """Object-store key for one recorded audio segment, namespaced under the
    run's recordings prefix (mirrors workflow_run_artifacts key conventions)."""
    return f"recordings/{workflow_run_id}/noveum/{kind}/{audio_uuid}.wav"


def make_storage_audio_sink(
    workflow_run_id: int,
    manifest: list[dict[str, Any]],
) -> Callable[..., Awaitable[bool]]:
    """
    Build the observer ``audio_sink``: writes each WAV segment to Dograh's
    object store (no Noveum network I/O during the live call) and records a
    manifest entry so the completion phase can upload the bytes to Noveum
    later under the same ``audio_uuid`` the observer stamped on the span.
    """

    async def _sink(
        *,
        wav_bytes: bytes,
        audio_uuid: str,
        kind: str,
        trace_id: str,
        span_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        storage_key = build_audio_storage_key(workflow_run_id, kind, audio_uuid)
        try:
            ok = await storage_fs.acreate_file_from_bytes(storage_key, wav_bytes)
        except Exception as exc:
            logger.warning(
                f"Noveum audio sink failed to store {kind} segment {audio_uuid}: {exc}"
            )
            return False
        if not ok:
            logger.warning(
                f"Noveum audio sink could not store {kind} segment {audio_uuid} "
                f"at {storage_key}"
            )
            return False
        manifest.append(
            {
                "audio_uuid": audio_uuid,
                "storage_key": storage_key,
                "kind": kind,
                "trace_id": trace_id,
                "span_id": span_id,
                "metadata": metadata or {},
            }
        )
        return True

    return _sink


def build_deferred_observer(
    *,
    record_audio: bool,
    audio_sink: Callable[..., Awaitable[bool]] | None,
) -> Any:
    """
    Construct a deferred ``NoveumTraceObserver`` around a per-call client
    whose ``DeferredTransport`` captures the finished trace in memory instead
    of sending it — the live call path never performs Noveum network I/O.
    Audio segments go to the sink. The per-call client is cheap: no HTTP
    session, no background thread, no atexit hook, no global-config mutation.
    """
    from noveum_trace.core.client import NoveumClient
    from noveum_trace.core.config import Config
    from noveum_trace.integrations.pipecat.pipecat_observer import NoveumTraceObserver
    from noveum_trace.transport.deferred_transport import DeferredTransport

    # Placeholder credentials: this client never authenticates — real BYOK
    # creds are applied per node in the completion phase.
    client = NoveumClient(
        config=Config.create(project="deferred", api_key="deferred"),
        transport_instance=DeferredTransport(),
    )

    return NoveumTraceObserver(
        client=client,
        deferred=True,
        record_audio=record_audio,
        audio_sink=audio_sink,
        # Dograh's on_call_finished calls _finish_conversation itself, AFTER
        # dograh's own on_pipeline_finished handler has run stop_recording()
        # (which flushes the AudioBufferProcessor). The SDK's safety net would
        # fire BEFORE that flush on cancelled calls, finishing the trace with
        # incomplete conversation audio — so it is disabled here.
        register_finish_safety_net=False,
    )


def build_payload_envelope(
    observer: Any,
    manifest: list[dict[str, Any]],
    extra_trace_attributes: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Snapshot the observer's finished trace and wrap it with the audio manifest.
    ``extra_trace_attributes`` (call metadata: run id, models, disposition) are
    merged into the trace's attributes so they ship with the trace as-is.

    The envelope is sanitized to pure-JSON types before being returned: it is
    persisted inside ``workflow_run.logs`` alongside Dograh's own keys, and one
    non-JSON-serializable span attribute would otherwise make the whole logs
    commit raise — silently discarding Dograh's data too. The recursive
    sanitizer (not a ``json.dumps(default=str)`` round-trip, which misses two
    cases) also stringifies non-string dict KEYS and non-finite floats
    (NaN/Infinity pass ``dumps`` but still break the Postgres JSON commit).
    """
    trace_snapshot = observer.build_payload_snapshot()
    if trace_snapshot is None:
        return None

    attributes = trace_snapshot.setdefault("attributes", {})
    attributes.update(extra_trace_attributes)

    envelope = {
        "schema_version": NOVEUM_SNAPSHOT_SCHEMA_VERSION,
        "trace": trace_snapshot,
        "audio_manifest": manifest,
    }
    try:
        return _to_pure_json(envelope)
    except Exception as exc:
        logger.warning(f"Noveum envelope sanitization failed: {exc}")
        return None


def _to_pure_json(value: Any) -> Any:
    """Recursively convert to JSON-native types: stringify non-string dict
    keys, non-finite floats, and any value that is not a JSON primitive."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(k): _to_pure_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_pure_json(v) for v in value]
    return str(value)
