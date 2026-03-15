"""Filesystem-backed cache and audio fetcher for workflow recordings.

Downloads recording files from object storage on first access, converts them
to raw 16-bit mono PCM at the pipeline sample rate via ffmpeg, trims
leading/trailing silence, and caches the processed bytes on disk so
subsequent plays (even from other workers) are instantaneous.
"""

import asyncio
import os
import shutil
import tempfile
from typing import Awaitable, Callable, Optional

import numpy as np
from loguru import logger

from api.constants import APP_ROOT_DIR
from pipecat.audio.utils import SPEAKING_THRESHOLD

# ---------------------------------------------------------------------------
# Filesystem cache directory
# ---------------------------------------------------------------------------

_CACHE_DIR = os.path.join(os.path.dirname(APP_ROOT_DIR), "dograh_pcm_cache")
os.makedirs(_CACHE_DIR, exist_ok=True)


def _cache_path(recording_id: str, sample_rate: int) -> str:
    """Return the on-disk path for a cached PCM file."""
    return os.path.join(_CACHE_DIR, f"{recording_id}_{sample_rate}.pcm")


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_recording_audio_fetcher(
    organization_id: int,
    pipeline_sample_rate: int,
) -> Callable[[str], Awaitable[Optional[bytes]]]:
    """Create an async callback that returns raw PCM bytes for a recording_id.

    The returned callable:
    1. Checks the filesystem cache (keyed by ``recording_id`` + sample rate).
    2. On miss, looks up the recording in the DB, downloads the audio file
       from S3/MinIO, converts it to 16-bit mono PCM at *pipeline_sample_rate*,
       trims leading/trailing silence, caches the result on disk, and returns it.

    Args:
        organization_id: Organization owning the recordings.
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
        cached = _cache_path(recording_id, pipeline_sample_rate)

        # 1. Serve from filesystem cache
        if os.path.exists(cached):
            logger.debug(f"Recording {recording_id} served from disk cache")
            return _read_file(cached)

        # 2. DB lookup
        recording = await db_client.get_recording_by_recording_id(
            recording_id, organization_id
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
        recordings = await db_client.get_recordings_for_workflow(
            workflow_id, organization_id
        )
        if not recordings:
            return

        # Skip if every recording is already cached on disk
        uncached = [
            r
            for r in recordings
            if not os.path.exists(_cache_path(r.recording_id, pipeline_sample_rate))
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
    ext = _ext_from_key(recording.storage_key)
    tmp_path = os.path.join(
        tempfile.gettempdir(), f"dograh_dl_{recording.recording_id}{ext}"
    )
    try:
        storage = get_storage_fn(recording.storage_backend)
        success = await storage.adownload_file(recording.storage_key, tmp_path)
        if not success:
            logger.error(f"Failed to download recording {recording.recording_id}")
            return None

        pcm_data = await _audio_file_to_pcm(tmp_path, sample_rate)
        if pcm_data is None:
            return None

        pcm_data = _trim_silence(pcm_data, sample_rate)

        # Write to disk cache atomically (write to tmp then rename)
        cached = _cache_path(recording.recording_id, sample_rate)
        tmp_cache = cached + ".tmp"
        _write_file(tmp_cache, pcm_data)
        os.replace(tmp_cache, cached)

        return pcm_data
    except Exception:
        logger.exception(f"Error fetching recording {recording.recording_id}")
        return None
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# File I/O helpers (run via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _write_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# Audio conversion
# ---------------------------------------------------------------------------


async def _audio_file_to_pcm(
    file_path: str, target_sample_rate: int
) -> Optional[bytes]:
    """Convert an audio file to raw 16-bit mono PCM bytes via ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.error("ffmpeg not found on PATH — cannot decode recording")
        return None

    cmd = [
        ffmpeg,
        "-i",
        file_path,
        "-f",
        "s16le",  # raw 16-bit signed little-endian PCM
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",  # mono
        "-ar",
        str(target_sample_rate),
        "-loglevel",
        "error",
        "pipe:1",  # output to stdout
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.error(f"ffmpeg failed (rc={proc.returncode}): {stderr.decode()}")
            return None

        if not stdout:
            logger.error("ffmpeg produced no output")
            return None

        return stdout
    except Exception:
        logger.exception("ffmpeg subprocess error")
        return None


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ext_from_key(storage_key: str) -> str:
    """Extract file extension from a storage key, defaulting to .wav."""
    _, ext = os.path.splitext(storage_key)
    return ext if ext else ".wav"
