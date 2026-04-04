"""
Hold audio utility for loading, caching, and playing hold music files.

This module provides functionality to load hold music audio files at specific sample rates
with caching to improve performance during multiple calls, and a reusable loop that queues
audio frames until a stop event is set.
"""

import asyncio
from typing import Dict, Optional, Tuple

import numpy as np
from loguru import logger

from pipecat.frames.frames import OutputAudioRawFrame

try:
    import soundfile as sf
except ModuleNotFoundError as e:
    logger.error(f"Exception: {e}")
    logger.error("In order to use hold audio, you need to `pip install soundfile`.")
    raise Exception(f"Missing module: {e}")


# Global cache for loaded hold music data
_hold_audio_cache: Dict[Tuple[str, int], np.ndarray] = {}


def load_hold_audio(file_path: str, sample_rate: int) -> Optional[bytes]:
    """Load hold music audio file at the specified sample rate with caching.

    Args:
        file_path: Path to the hold music audio file
        sample_rate: Target sample rate (8000 or 16000 Hz supported)

    Returns:
        Audio data as bytes (PCM16) or None if loading failed
    """
    cache_key = (file_path, sample_rate)

    # Check cache first
    if cache_key in _hold_audio_cache:
        logger.debug(f"Using cached hold audio for {file_path} at {sample_rate}Hz")
        audio_data = _hold_audio_cache[cache_key]
        return audio_data.tobytes()

    try:
        logger.info(f"Loading hold audio from {file_path} at {sample_rate}Hz")

        # Load audio file
        sound, file_sample_rate = sf.read(file_path, dtype="int16")
        logger.info(
            f"Audio file loaded - file sample_rate: {file_sample_rate}, target: {sample_rate}"
        )

        # Ensure mono audio (take first channel if stereo)
        if len(sound.shape) > 1:
            sound = sound[:, 0]

        # Resample if needed
        if file_sample_rate != sample_rate:
            logger.warning(
                f"Hold music file has sample rate {file_sample_rate}, expected {sample_rate}"
            )
            # For now, we'll use the audio as-is and let the transport handle resampling
            # In a production system, you might want to use librosa or scipy for proper resampling

        # Convert to int16 and cache
        audio_data = sound.astype(np.int16)
        _hold_audio_cache[cache_key] = audio_data

        logger.info(
            f"Hold audio loaded successfully: {len(audio_data)} samples at {sample_rate}Hz"
        )
        return audio_data.tobytes()

    except Exception as e:
        logger.error(f"Failed to load hold audio file {file_path}: {e}")
        return None


def clear_hold_audio_cache():
    """Clear the hold audio cache to free memory."""
    global _hold_audio_cache
    _hold_audio_cache.clear()
    logger.info("Hold audio cache cleared")


def get_cache_info() -> Dict[str, int]:
    """Get information about the current cache state.

    Returns:
        Dictionary with cache statistics
    """
    return {
        "cached_files": len(_hold_audio_cache),
        "total_cache_size": sum(len(data) for data in _hold_audio_cache.values()),
    }


async def play_hold_audio_loop(
    task,
    stop_event: asyncio.Event,
    sample_rate: int = 16000,
    hold_music_file: Optional[str] = None,
) -> None:
    """Play hold/ring-back audio in a loop until *stop_event* is set.

    This is a shared helper used by call-transfer hold music and the
    pre-call data fetch ringer.  The caller is responsible for creating
    the ``asyncio.Event`` and setting it when playback should stop.

    Args:
        task: A ``PipelineTask`` (or anything with ``queue_frame``).
        stop_event: Set this event to terminate the loop.
        sample_rate: Target sample rate for audio playback.
        hold_music_file: Path to a WAV file.  When *None* the default
            ``transfer_hold_ring_{sample_rate}.wav`` asset is used.
    """
    if hold_music_file is None:
        from api.constants import APP_ROOT_DIR

        hold_music_file = str(
            APP_ROOT_DIR / "assets" / f"transfer_hold_ring_{sample_rate}.wav"
        )

    hold_audio_data = load_hold_audio(hold_music_file, sample_rate)
    if not hold_audio_data:
        logger.warning(f"Hold audio loop: failed to load {hold_music_file}, skipping")
        return

    num_samples = len(hold_audio_data) // 2  # 16-bit PCM = 2 bytes per sample
    duration = num_samples / sample_rate

    logger.debug(f"Hold audio loop: playing at {sample_rate}Hz")
    try:
        while not stop_event.is_set():
            frame = OutputAudioRawFrame(
                audio=hold_audio_data,
                sample_rate=sample_rate,
                num_channels=1,
            )
            await task.queue_frame(frame)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=duration + 1.5)
                break
            except asyncio.TimeoutError:
                pass
    except Exception as e:
        logger.error(f"Hold audio loop: error: {e}")
    logger.debug("Hold audio loop: stopped")
