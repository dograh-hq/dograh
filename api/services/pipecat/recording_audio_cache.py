"""Filesystem-backed cache and audio fetcher for workflow recordings.

Downloads recording files from object storage on first access, converts them
to raw 16-bit mono PCM at the pipeline sample rate via ffmpeg, trims
leading/trailing silence, and caches the processed bytes on disk so
subsequent plays (even from other workers) are instantaneous.
"""

import os
from typing import Awaitable, Callable, Optional

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

# ---------------------------------------------------------------------------
# Cache path helper
# ---------------------------------------------------------------------------


def _cache_path(
    organization_id: int, workflow_id: int, recording_id: str, sample_rate: int
) -> str:
    """Return the on-disk path for a cached PCM file."""
    return os.path.join(
        CACHE_DIR, f"{organization_id}_{workflow_id}_{recording_id}_{sample_rate}.pcm"
    )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_recording_audio_fetcher(
    organization_id: int,
    workflow_id: int,
    pipeline_sample_rate: int,
) -> Callable[[str], Awaitable[Optional[bytes]]]:
    """Create an async callback that returns raw PCM bytes for a recording_id.

    The returned callable:
    1. Checks the filesystem cache (keyed by org/workflow/recording + sample rate).
    2. On miss, looks up the recording in the DB, downloads the audio file
       from S3/MinIO, converts it to 16-bit mono PCM at *pipeline_sample_rate*,
       trims leading/trailing silence, caches the result on disk, and returns it.

    Args:
        organization_id: Organization owning the recordings.
        workflow_id: Workflow the recordings belong to.
        pipeline_sample_rate: Target PCM sample rate for the pipeline.

    Returns:
        ``async (recording_id: str) -> Optional[bytes]``
    """
    from api.db import db_client
    from api.services.storage import get_storage_for_backend

    # Resolve storage instances once per backend at creation time, not per fetch.
    _storage_cache: dict[str, object] = {}

    def _get_storage(backend: str):
        if backend not in _storage_cache:
            _storage_cache[backend] = get_storage_for_backend(backend)
        return _storage_cache[backend]

    async def fetch(recording_id: str) -> Optional[bytes]:
        cached = _cache_path(
            organization_id, workflow_id, recording_id, pipeline_sample_rate
        )

        # 1. Serve from filesystem cache
        if os.path.exists(cached):
            logger.debug(f"Recording {recording_id} served from disk cache")
            return read_cached_file(cached)

        # 2. DB lookup
        recording = await db_client.get_recording_by_recording_id(
            recording_id, organization_id, workflow_id
        )
        if not recording:
            logger.warning(f"Recording {recording_id} not found in database")
            return None

        # 3. Download, convert, trim, and cache
        pcm_data = await _download_and_convert(
            recording, pipeline_sample_rate, _get_storage
        )
        return pcm_data

    return fetch


# ---------------------------------------------------------------------------
# Cache warming
# ---------------------------------------------------------------------------


async def warm_recording_cache(
    workflow_id: int,
    organization_id: int,
    pipeline_sample_rate: int,
) -> None:
    """Pre-fetch all active recordings for a workflow into the disk cache.

    Launched as a background ``asyncio.Task`` at pipeline startup so that
    recordings are ready before the first playback request. Errors are logged
    but never propagated — a cache miss falls back to the on-demand fetch path.
    """
    from api.db import db_client
    from api.services.storage import get_storage_for_backend

    try:
        recordings = await db_client.get_recordings(
            organization_id=organization_id, workflow_id=workflow_id
        )
        if not recordings:
            return

        # Skip if every recording is already cached on disk
        uncached = [
            r
            for r in recordings
            if not os.path.exists(
                _cache_path(
                    organization_id, workflow_id, r.recording_id, pipeline_sample_rate
                )
            )
        ]
        if not uncached:
            logger.debug(f"Recording cache already warm for workflow {workflow_id}")
            return

        logger.info(
            f"Warming recording cache: {len(uncached)}/{len(recordings)} "
            f"recording(s) for workflow {workflow_id}"
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

        logger.info(f"Recording cache warm complete for workflow {workflow_id}")
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
            recording.workflow_id,
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
