"""Filesystem-backed cache and audio fetcher for workflow recordings.

Downloads recording files from object storage on first access, converts them
to raw 16-bit mono PCM at the pipeline sample rate via ffmpeg, trims
leading/trailing silence, and caches the processed bytes on disk so
subsequent plays (even from other workers) are instantaneous.
"""

import os
from typing import Awaitable, Callable, NamedTuple, Optional

import numpy as np
from loguru import logger

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
    transcript: Optional[str] = None


# ---------------------------------------------------------------------------
# Cache path helper
# ---------------------------------------------------------------------------


def _cache_path(organization_id: int, recording_id: str, sample_rate: int) -> str:
    """Return the on-disk path for a cached PCM file."""
    return os.path.join(
        CACHE_DIR, f"{organization_id}_{recording_id}_{sample_rate}.pcm"
    )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_recording_audio_fetcher(
    organization_id: int,
    pipeline_sample_rate: int,
) -> Callable[..., Awaitable[Optional[bytes]]]:
    """Create an async callback that returns raw PCM bytes for a recording.

    The returned callable accepts **one** of two keyword arguments:
    - ``recording_pk``  – the immutable integer primary key (used by
      dropdown-based selections: greeting, edges, tool configs).
    - ``recording_id``  – the human-readable string ID (used by
      prompt-based ``RECORDING_ID: xxx`` references).

    Flow:
    1. Checks the filesystem cache (keyed by org + pk + sample rate).
    2. On miss, looks up the recording in the DB, downloads the audio
       from S3/MinIO, converts to 16-bit mono PCM, trims silence, and
       caches the result on disk.

    Args:
        organization_id: Organization owning the recordings.
        pipeline_sample_rate: Target PCM sample rate for the pipeline.
    """
    from api.db import db_client
    from api.services.storage import get_storage_for_backend

    _storage_cache: dict[str, object] = {}
    _transcript_cache: dict[str, Optional[str]] = {}

    def _get_storage(backend: str):
        if backend not in _storage_cache:
            _storage_cache[backend] = get_storage_for_backend(backend)
        return _storage_cache[backend]

    async def _lookup_recording(
        cache_key: str,
        recording_pk: Optional[int],
        recording_id: Optional[str],
    ):
        """DB lookup with transcript caching."""
        if recording_pk is not None:
            recording = await db_client.get_recording_by_id(
                recording_pk, organization_id
            )
        else:
            recording = await db_client.get_recording_by_recording_id(
                recording_id, organization_id
            )
        if recording:
            _transcript_cache[cache_key] = recording.transcript or None
        return recording

    async def fetch(
        *,
        recording_pk: Optional[int] = None,
        recording_id: Optional[str] = None,
    ) -> Optional[RecordingAudio]:
        if recording_pk is None and recording_id is None:
            logger.warning("fetch called with neither recording_pk nor recording_id")
            return None

        # Use pk for cache key when available, otherwise recording_id
        cache_key = str(recording_pk) if recording_pk is not None else recording_id
        cached = _cache_path(organization_id, cache_key, pipeline_sample_rate)

        # 1. Serve from filesystem cache
        if os.path.exists(cached):
            logger.debug(f"Recording {cache_key} served from disk cache")
            audio = read_cached_file(cached)
            # Transcript may already be in memory from a prior fetch;
            # if not, do a lightweight DB lookup.
            if cache_key not in _transcript_cache:
                await _lookup_recording(cache_key, recording_pk, recording_id)
            return RecordingAudio(
                audio=audio, transcript=_transcript_cache.get(cache_key)
            )

        # 2. DB lookup
        recording = await _lookup_recording(cache_key, recording_pk, recording_id)

        if not recording:
            logger.warning(f"Recording {cache_key} not found in database")
            return None

        # 3. Download, convert, trim, and cache
        pcm_data = await _download_and_convert(
            recording, pipeline_sample_rate, _get_storage
        )
        if pcm_data is None:
            return None
        return RecordingAudio(
            audio=pcm_data, transcript=_transcript_cache.get(cache_key)
        )

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
        recordings = await db_client.get_recordings(organization_id=organization_id)
        if not recordings:
            return

        # Skip if every recording is already cached on disk
        uncached = [
            r
            for r in recordings
            if not os.path.exists(
                _cache_path(organization_id, str(r.id), pipeline_sample_rate)
            )
            and not os.path.exists(
                _cache_path(organization_id, r.recording_id, pipeline_sample_rate)
            )
        ]
        if not uncached:
            logger.debug(f"Recording cache already warm for org {organization_id}")
            return

        logger.info(
            f"Warming recording cache: {len(uncached)}/{len(recordings)} "
            f"recording(s) for org {organization_id}"
        )

        # Resolve storage instances once per backend, not per recording
        storage_by_backend: dict[str, object] = {}

        def _get_storage(backend: str):
            if backend not in storage_by_backend:
                storage_by_backend[backend] = get_storage_for_backend(backend)
            return storage_by_backend[backend]

        for recording in uncached:
            try:
                pcm_data = await _download_and_convert(
                    recording, pipeline_sample_rate, _get_storage
                )
                if pcm_data:
                    logger.debug(
                        f"Cache warm: loaded {recording.recording_id} "
                        f"({len(pcm_data)} bytes)"
                    )
            except Exception:
                logger.exception(
                    f"Cache warm: error processing {recording.recording_id}"
                )

        logger.info(f"Recording cache warm complete for org {organization_id}")
    except Exception:
        logger.exception("Recording cache warm failed")


# ---------------------------------------------------------------------------
# Shared download → convert → trim → cache-to-disk helper
# ---------------------------------------------------------------------------


async def _download_and_convert(
    recording, sample_rate: int, get_storage_fn
) -> Optional[bytes]:
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
            recording.recording_id,
            sample_rate,
        )
        write_cache_file(cached, pcm_data)

        return pcm_data
    except Exception:
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
