from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.constants import BACKEND_API_ENDPOINT
from api.services.integrations.base import IntegrationCompletionContext
from api.services.storage import storage_fs

from .client import build_noveum_client
from .collector import NOVEUM_PAYLOAD_LOG_KEY, NOVEUM_SNAPSHOT_SCHEMA_VERSION
from .node import NoveumNodeData

# Concurrent in-flight audio uploads per node. Each upload runs the SDK's
# synchronous send inside a worker thread; this bounds both thread count and
# pressure on the Noveum API.
_AUDIO_UPLOAD_CONCURRENCY = 6


def _build_recording_url(
    context: IntegrationCompletionContext,
) -> str | None:
    workflow_run = context.workflow_run
    if context.public_token:
        base_url = f"{BACKEND_API_ENDPOINT}/api/v1/public/download/workflow/{context.public_token}"
        return f"{base_url}/recording" if workflow_run.recording_url else None
    return workflow_run.recording_url


def _make_temp_wav_path() -> str:
    fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    return tmp_path


def _read_file(tmp_path: str) -> bytes:
    with open(tmp_path, "rb") as f:
        return f.read()


async def _read_stored_audio(storage_key: str) -> bytes | None:
    """Fetch one stored WAV segment back as bytes (via a temp file — the
    filesystem abstraction has no direct bytes-read API). Full-WAV reads run in
    a worker thread so they never block the arq event loop."""
    tmp_path: str | None = None
    try:
        # Created synchronously so tmp_path is always bound before the first
        # cancellable await — an off-loop mkstemp whose await is cancelled
        # would leave a file nothing knows the name of. It is two syscalls.
        tmp_path = _make_temp_wav_path()
        ok = await storage_fs.adownload_file(storage_key, tmp_path)
        if not ok:
            return None
        return await asyncio.to_thread(_read_file, tmp_path)
    except Exception as exc:
        logger.warning(f"Noveum completion failed to read {storage_key}: {exc}")
        return None
    finally:
        # Covers every exit: success, failure, and CancelledError (a
        # BaseException, so `except Exception` never saw it) from an arq
        # job_timeout or worker shutdown landing on one of the awaits above.
        # Unlink is inline, not awaited: an await inside finally during
        # cancellation re-raises immediately and would skip the cleanup.
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def _upload_manifest_audio(
    client: Any, manifest: Any
) -> tuple[int, int]:
    """
    Upload every parked audio segment to Noveum under the audio_uuid the
    observer already stamped on the span, with bounded parallelism.

    Sends are direct and delivery-observed: ``send_audio_sync`` performs the
    POST inline (in a worker thread, off the event loop) and raises on
    failure, so the returned ``(uploaded, failed)`` counts are REAL outcomes,
    not enqueue counts. Runs after the trace send, so audio failures never
    affect trace delivery.
    """
    semaphore = asyncio.Semaphore(_AUDIO_UPLOAD_CONCURRENCY)

    async def _upload_entry(entry: Any) -> bool:
        # A non-dict entry (corrupt/tampered manifest) is counted as an
        # individual failure rather than raising inside asyncio.gather, which
        # would abort the node result AFTER the trace POST already succeeded.
        if not isinstance(entry, dict):
            return False
        audio_uuid = entry.get("audio_uuid")
        storage_key = entry.get("storage_key")
        if not audio_uuid or not storage_key:
            return False
        async with semaphore:
            audio_bytes = await _read_stored_audio(storage_key)
            if not audio_bytes:
                return False
            try:
                await asyncio.to_thread(
                    client.send_audio_sync,
                    audio_data=audio_bytes,
                    trace_id=entry.get("trace_id"),
                    span_id=entry.get("span_id"),
                    audio_uuid=audio_uuid,
                    metadata=entry.get("metadata") or None,
                )
                return True
            except Exception as exc:
                logger.warning(
                    f"Noveum completion failed to upload audio {audio_uuid}: {exc}"
                )
                return False

    # A persisted manifest that is not a list (corrupt/tampered log) must not
    # raise during iteration — treat it as empty so trace delivery still stands.
    if not isinstance(manifest, list) or not manifest:
        return 0, 0

    outcomes = await asyncio.gather(*(_upload_entry(entry) for entry in manifest))
    uploaded = sum(1 for ok in outcomes if ok)
    return uploaded, len(outcomes) - uploaded


async def run_completion(
    nodes: list[dict[str, Any]],
    context: IntegrationCompletionContext,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    logs = context.workflow_run.logs
    envelope = logs.get(NOVEUM_PAYLOAD_LOG_KEY) if isinstance(logs, dict) else None
    # A persisted envelope that is present but not a dict (corrupt/tampered log)
    # would crash every later ``envelope.get(...)`` — which runs outside the
    # per-node try/except — losing every node's truthful result. Flag it so the
    # loop records a bounded per-node error instead of raising.
    envelope_malformed = envelope is not None and not isinstance(envelope, dict)
    recording_url = _build_recording_url(context)

    for node in nodes:
        node_id = node.get("id", "unknown")
        try:
            noveum_data = NoveumNodeData.model_validate(node.get("data", {}))
        except Exception as exc:
            logger.warning(f"Noveum node #{node_id} failed validation, skipping: {exc}")
            results[f"noveum_{node_id}"] = {"error": "validation_failed"}
            continue

        if not noveum_data.noveum_enabled:
            logger.debug(f"Noveum node '{noveum_data.name}' is disabled, skipping")
            continue

        if envelope_malformed:
            logger.warning(
                f"Noveum payload for node '{noveum_data.name}' (#{node_id}) is "
                f"malformed (type {type(envelope).__name__}), skipping export"
            )
            results[f"noveum_{node_id}"] = {"error": "malformed_payload"}
            continue

        if not envelope:
            # Not an error: runs that never start a pipeline (e.g. telephony
            # failures) legitimately produce no trace.
            logger.info(
                f"No Noveum payload for node '{noveum_data.name}' (#{node_id}) — "
                "run produced no trace"
            )
            results[f"noveum_{node_id}"] = {"status": "no_trace"}
            continue

        schema_version = envelope.get("schema_version")
        if schema_version != NOVEUM_SNAPSHOT_SCHEMA_VERSION:
            logger.warning(
                f"Noveum snapshot schema_version {schema_version} is not "
                f"{NOVEUM_SNAPSHOT_SCHEMA_VERSION}, skipping node #{node_id}"
            )
            results[f"noveum_{node_id}"] = {"error": "unsupported_schema_version"}
            continue

        trace_data = envelope.get("trace")
        if not trace_data:
            results[f"noveum_{node_id}"] = {"error": "missing_trace_snapshot"}
            continue

        trace_attributes = trace_data.setdefault("attributes", {})
        if recording_url:
            trace_attributes["dograh.recording_url"] = recording_url
        # The live-phase client ran with placeholder identity ("deferred" /
        # "development"); the wire export only fixes the TOP-LEVEL project and
        # environment fields, so overwrite the trace ATTRIBUTES with the
        # node's real values too.
        trace_attributes["noveum.project"] = noveum_data.noveum_project
        trace_attributes["noveum.environment"] = noveum_data.noveum_environment

        # The agent version was stamped into the trace attributes in the live
        # phase; surface it as the trace's top-level service_version too (the
        # transport applies config.service_version at export time).
        agent_version = trace_attributes.get("dograh.agent_version")

        client = None
        try:
            client = build_noveum_client(
                api_key=noveum_data.noveum_api_key or "",
                project=noveum_data.noveum_project or "",
                environment=noveum_data.noveum_environment,
                service_version=(
                    str(agent_version) if agent_version is not None else None
                ),
            )

            # Trace first, audio second: the trace is the payload that must
            # not be lost, and the audio phase is the slow part (N segments,
            # each up to several retried 30s POSTs during an outage) — sending
            # the trace up front means a worst-case audio phase hitting the
            # arq job_timeout can no longer starve the trace send. It also
            # means a failed trace send skips audio entirely, leaving no
            # orphaned audio on the Noveum side.
            # Direct, delivery-observed send (raises on failure) — run in a
            # worker thread so the network wait never blocks the arq loop.
            await asyncio.to_thread(client.send_trace_dict, trace_data)

            manifest = envelope.get("audio_manifest") or []
            uploaded, failed = await _upload_manifest_audio(client, manifest)

            # Recorded only AFTER the trace POST succeeded: these annotations
            # are the operator-facing audit record, so "delivered" must mean
            # the API accepted the trace, and the audio counts are outcomes.
            results[f"noveum_{node_id}"] = {
                "status": "delivered",
                "trace_id": trace_data.get("trace_id"),
                "project": noveum_data.noveum_project,
                "audio_uploaded": uploaded,
                "audio_failed": failed,
                "exported_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            logger.error(f"Noveum export failed for node '{noveum_data.name}': {exc}")
            results[f"noveum_{node_id}"] = {"error": str(exc)}
        finally:
            if client is not None:
                try:
                    # Nothing is ever enqueued (sends are direct), so this only
                    # stops the idle transport thread + closes the session;
                    # off-loop because the SDK's shutdown is synchronous.
                    await asyncio.to_thread(client.shutdown)
                except Exception as exc:
                    logger.warning(f"Noveum client shutdown failed: {exc}")

    return results
