"""Filesystem-backed cache and audio fetcher for workflow recordings.

Downloads recording files from object storage on first access, converts them
to raw 16-bit mono PCM at the pipeline sample rate via ffmpeg, trims
leading/trailing silence, and caches the processed bytes on disk so
subsequent plays (even from other workers) avoid downloading and conversion.
"""

import asyncio
import os
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import NamedTuple
from weakref import WeakValueDictionary

import numpy as np
from loguru import logger

from api.services.worker_sync.manager import get_worker_sync_manager
from api.services.worker_sync.protocol import WorkerSyncEvent, WorkerSyncEventType
from pipecat.audio.utils import SPEAKING_THRESHOLD

from .audio_file_cache import (
    CACHE_DIR,
    convert_audio_file,
    download_storage_file,
    read_cached_file,
    write_cache_file,
)


class RecordingAudio(NamedTuple):
    """Audio bytes paired with the recording's transcript (when available)."""

    audio: bytes
    transcript: str | None = None


# ---------------------------------------------------------------------------
# Shared metadata and cache identity
# ---------------------------------------------------------------------------


class _Recording(NamedTuple):
    id: int
    organization_id: int
    recording_id: str
    transcript: str | None
    storage_key: str
    storage_backend: str
    created_at: datetime | None


# Cache detached values, not ORM objects or per-call state. Typed lookup keys
# distinguish a numeric descriptive ID ("42") from a primary key (42).
_METADATA_TTL_SECONDS = 60.0
_MAX_METADATA_ENTRIES = 2048
_metadata_cache: OrderedDict[tuple[int, int | str], tuple[float, _Recording]] = (
    OrderedDict()
)
# Only in-flight queries retain these per-organization invalidation signals.
# Weak values avoid retaining every organization that has ever mutated a row.
_metadata_invalidations: WeakValueDictionary[int, asyncio.Event] = WeakValueDictionary()
_download_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _cached_recording(organization_id: int, identifier: int | str) -> _Recording | None:
    key = (organization_id, identifier)
    cached = _metadata_cache.get(key)
    if cached is None:
        return None
    expires_at, recording = cached
    if time.monotonic() >= expires_at:
        del _metadata_cache[key]
        return None
    _metadata_cache.move_to_end(key)
    return recording


def _remember_recording(recording) -> _Recording:
    metadata = _Recording(
        recording.id,
        recording.organization_id,
        recording.recording_id,
        recording.transcript or None,
        recording.storage_key,
        recording.storage_backend,
        recording.created_at,
    )
    expires_at = time.monotonic() + _METADATA_TTL_SECONDS
    for identifier in (metadata.id, metadata.recording_id):
        key = (metadata.organization_id, identifier)
        _metadata_cache[key] = (expires_at, metadata)
        _metadata_cache.move_to_end(key)
    while len(_metadata_cache) > _MAX_METADATA_ENTRIES:
        _metadata_cache.popitem(last=False)
    return metadata


def _clear_recording_metadata(organization_id: int) -> None:
    invalidated = _metadata_invalidations.pop(organization_id, None)
    if invalidated is not None:
        invalidated.set()
    for key in list(_metadata_cache):
        if key[0] == organization_id:
            del _metadata_cache[key]


async def handle_recording_cache_sync(event: WorkerSyncEvent) -> None:
    """Invalidate aliases and transcripts on every API worker after a mutation."""
    await _invalidate_local_recordings(int(event.org_id))


def _remove_legacy_cache_files(organization_id: int) -> None:
    # A name can be reassigned to another existing recording. Legacy files have
    # no primary-key identity, so discard them after mutations; canonical PCM
    # remains reusable through renames and metadata eviction.
    try:
        with os.scandir(CACHE_DIR) as entries:
            for entry in entries:
                if entry.name.startswith(f"{organization_id}_") and entry.name.endswith(
                    ".pcm"
                ):
                    try:
                        os.unlink(entry.path)
                    except FileNotFoundError:
                        pass
    except OSError:
        logger.exception("Could not remove legacy recording cache files")


async def _invalidate_local_recordings(organization_id: int) -> None:
    _clear_recording_metadata(organization_id)
    await asyncio.to_thread(_remove_legacy_cache_files, organization_id)


async def invalidate_recording_cache(organization_id: int) -> None:
    """Invalidate locally immediately and notify other workers after a DB commit."""
    await _invalidate_local_recordings(organization_id)
    try:
        await get_worker_sync_manager().broadcast(
            WorkerSyncEventType.RECORDINGS,
            action="invalidate",
            org_id=str(organization_id),
        )
    except Exception:  # noqa: BLE001 - a committed mutation must not fail on cache eviction
        # The bounded TTL also covers dropped pub/sub events and non-API consumers.
        logger.exception("Could not broadcast recording cache invalidation")


def _cache_path(organization_id: int, recording_pk: int, sample_rate: int) -> str:
    """Use one immutable identity for reads, writes and warming.

    The prefix separates this namespace from legacy descriptive-ID filenames,
    which could be numeric and collide with another recording's primary key.
    """
    return os.path.join(
        CACHE_DIR, f"recording_{organization_id}_{recording_pk}_{sample_rate}.pcm"
    )


def _read_cached_recording(
    recording: _Recording, sample_rate: int, *, read_audio: bool = True
) -> bytes | None:
    cached = _cache_path(recording.organization_id, recording.id, sample_rate)
    try:
        if not os.path.exists(cached):
            # Only probe a legacy name resolved through org-scoped DB metadata.
            # Descriptive IDs never become arbitrary filesystem paths.
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", recording.recording_id):
                return None
            legacy = os.path.join(
                CACHE_DIR,
                f"{recording.organization_id}_{recording.recording_id}_{sample_rate}.pcm",
            )
            try:
                # A new row must not inherit audio cached before its creation.
                if (
                    recording.created_at
                    and os.path.getmtime(legacy) < recording.created_at.timestamp()
                ):
                    return None
                os.replace(legacy, cached)
            except FileNotFoundError:
                # Another worker may already have migrated this file.
                pass
        if not read_audio:
            # Warming an existing entry needs no PCM read, only valid metadata.
            return b"" if os.path.getsize(cached) else None
        audio = read_cached_file(cached)
        if audio:
            logger.debug(f"Recording {recording.id} served from disk cache")
            return audio
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception(f"Could not read recording cache for {recording.id}")
    return None


async def _get_recording_audio(
    recording: _Recording, sample_rate: int, get_storage_fn, *, read_audio: bool = True
):
    # Warmers and foreground fetches share the same fill. The caller owns the
    # download; cancellation releases the lock, allowing the next caller to retry.
    path = _cache_path(recording.organization_id, recording.id, sample_rate)
    lock = _download_locks.setdefault(path, asyncio.Lock())
    async with lock:
        cached = await asyncio.to_thread(
            _read_cached_recording, recording, sample_rate, read_audio=read_audio
        )
        if cached is not None:
            return cached
        return await _download_and_convert(recording, sample_rate, get_storage_fn)


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_recording_audio_fetcher(
    organization_id: int,
    pipeline_sample_rate: int,
) -> Callable[..., Awaitable[RecordingAudio | None]]:
    """Create an async callback that returns PCM and transcript for a recording.

    The returned callable accepts **one** of two keyword arguments:
    - ``recording_pk``  – the immutable integer primary key (used by
      dropdown-based selections: greeting, edges, tool configs).
    - ``recording_id``  – the human-readable string ID (used by
      prompt-based ``RECORDING_ID: xxx`` references).

    Flow:
    1. Resolves active, organization-scoped metadata through a bounded process
       cache, shared by primary-key and descriptive-ID lookups across calls.
    2. Checks the filesystem cache (keyed by org + pk + sample rate).
    3. On miss, downloads, converts and trims the audio, then caches it on disk.

    Args:
        organization_id: Organization owning the recordings.
        pipeline_sample_rate: Target PCM sample rate for the pipeline.
    """
    from api.db import db_client
    from api.services.storage import get_storage_for_backend

    _storage_cache: dict[str, object] = {}

    def _get_storage(backend: str):
        if backend not in _storage_cache:
            _storage_cache[backend] = get_storage_for_backend(backend)
        return _storage_cache[backend]

    async def fetch(
        *,
        recording_pk: int | None = None,
        recording_id: str | None = None,
    ) -> RecordingAudio | None:
        if recording_pk is None and recording_id is None:
            logger.warning("fetch called with neither recording_pk nor recording_id")
            return None

        identifier = recording_pk if recording_pk is not None else recording_id
        recording = _cached_recording(organization_id, identifier)
        while recording is None:
            invalidated = _metadata_invalidations.setdefault(
                organization_id, asyncio.Event()
            )
            if recording_pk is not None:
                row = await db_client.get_recording_by_id(recording_pk, organization_id)
            else:
                row = await db_client.get_recording_by_recording_id(
                    recording_id, organization_id
                )
            if (
                row is None
                or row.organization_id != organization_id
                or not row.is_active
            ):
                logger.warning(f"Recording {identifier} not found in database")
                return None
            # Do not repopulate metadata from a query that overlapped invalidation.
            if invalidated.is_set():
                continue
            recording = _remember_recording(row)

        pcm_data = await _get_recording_audio(
            recording, pipeline_sample_rate, _get_storage
        )
        if pcm_data is None:
            return None
        return RecordingAudio(audio=pcm_data, transcript=recording.transcript)

    return fetch


# ---------------------------------------------------------------------------
# Cache warming
# ---------------------------------------------------------------------------


async def warm_recording_cache(
    organization_id: int,
    pipeline_sample_rate: int,
) -> None:
    """Pre-fetch all active recordings for an organization into the disk cache.

    Launched as a background ``asyncio.Task`` at pipeline startup so that
    recordings are ready before the first playback request. Errors are logged
    but never propagated — a cache miss falls back to the on-demand fetch path.
    """
    from api.db import db_client
    from api.services.storage import get_storage_for_backend

    try:
        invalidated = _metadata_invalidations.setdefault(
            organization_id, asyncio.Event()
        )
        recordings = await db_client.get_recordings(organization_id=organization_id)
        if not recordings or invalidated.is_set():
            return
        recordings = [
            _remember_recording(row)
            for row in recordings
            if row.organization_id == organization_id and row.is_active
        ]

        # Resolve storage instances once per backend, not per recording
        storage_by_backend: dict[str, object] = {}

        def _get_storage(backend: str):
            if backend not in storage_by_backend:
                storage_by_backend[backend] = get_storage_for_backend(backend)
            return storage_by_backend[backend]

        for recording in recordings:
            try:
                pcm_data = await _get_recording_audio(
                    recording, pipeline_sample_rate, _get_storage, read_audio=False
                )
                if pcm_data:
                    logger.debug(
                        f"Cache warm: loaded {recording.recording_id} "
                        f"({len(pcm_data)} bytes)"
                    )
            except Exception:  # noqa: BLE001 - warming is best effort per recording
                logger.exception(
                    f"Cache warm: error processing {recording.recording_id}"
                )

        logger.info(f"Recording cache warm complete for org {organization_id}")
    except Exception:  # noqa: BLE001 - background warming must not fail the call
        logger.exception("Recording cache warm failed")


# ---------------------------------------------------------------------------
# Shared download → convert → trim → cache-to-disk helper
# ---------------------------------------------------------------------------


async def _download_and_convert(
    recording, sample_rate: int, get_storage_fn
) -> bytes | None:
    """Download a recording from storage, convert to PCM, trim, and cache to disk.

    Returns the processed PCM bytes, or None on failure.
    """
    tmp_path = await download_storage_file(
        recording.storage_key, recording.storage_backend, get_storage_fn
    )
    if not tmp_path:
        return None

    try:
        pcm_data = await convert_audio_file(tmp_path, sample_rate, output_format="pcm")
        if pcm_data is None:
            return None

        pcm_data = _trim_silence(pcm_data, sample_rate)

        # Write to disk cache
        cached = _cache_path(
            recording.organization_id,
            recording.id,
            sample_rate,
        )
        await asyncio.to_thread(write_cache_file, cached, pcm_data)

        return pcm_data
    except Exception:  # noqa: BLE001 - playback handles missing audio with its fallback
        logger.exception(f"Error fetching recording {recording.recording_id}")
        return None
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Silence trimming
# ---------------------------------------------------------------------------


def _trim_silence(pcm_data: bytes, sample_rate: int) -> bytes:
    """Trim leading and trailing silence from raw 16-bit mono PCM bytes.

    Uses 10ms frames and the same amplitude threshold as pipecat's
    ``is_silence`` to detect speech boundaries.
    """
    data = np.frombuffer(pcm_data, dtype=np.int16)
    frame_size = int(sample_rate * 0.01)  # 10ms frames
    num_frames = len(data) // frame_size

    if num_frames == 0:
        return pcm_data

    # Find first non-silent frame
    first_speech = None
    for i in range(num_frames):
        frame = data[i * frame_size : (i + 1) * frame_size]
        if np.abs(frame).max() > SPEAKING_THRESHOLD:
            first_speech = i
            break

    if first_speech is None:
        # Entire clip is silence — return as-is to avoid empty audio
        return pcm_data

    # Find last non-silent frame
    last_speech = first_speech
    for i in range(num_frames - 1, first_speech - 1, -1):
        frame = data[i * frame_size : (i + 1) * frame_size]
        if np.abs(frame).max() > SPEAKING_THRESHOLD:
            last_speech = i
            break

    start = first_speech * frame_size
    end = (last_speech + 1) * frame_size
    trimmed = data[start:end]

    trimmed_duration = len(trimmed) / sample_rate
    original_duration = len(data) / sample_rate
    if original_duration - trimmed_duration > 0.05:
        logger.debug(
            f"Trimmed silence: {original_duration:.2f}s → {trimmed_duration:.2f}s"
        )

    return trimmed.tobytes()
