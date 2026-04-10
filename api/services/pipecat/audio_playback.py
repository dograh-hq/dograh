"""Utilities for playing audio through the pipeline transport.

Provides one-shot and looping playback of raw PCM audio.  All playback
should be routed through ``transport.output().queue_frame`` so the audio
reaches the caller without passing through STT (which would otherwise
generate phantom transcriptions).
"""

import asyncio
import uuid
from typing import Awaitable, Callable, Dict, Optional, Tuple

import numpy as np
from loguru import logger

from pipecat.frames.frames import (
    Frame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)

try:
    import soundfile as sf
except ModuleNotFoundError as e:
    logger.error(f"Exception: {e}")
    logger.error("In order to use audio playback, you need to `pip install soundfile`.")
    raise Exception(f"Missing module: {e}")


# ---------------------------------------------------------------------------
# Audio file loading / caching
# ---------------------------------------------------------------------------

_audio_cache: Dict[Tuple[str, int], bytes] = {}


def load_audio_file(file_path: str, sample_rate: int) -> Optional[bytes]:
    """Load an audio file as PCM-16 bytes, caching the result.

    Args:
        file_path: Path to a WAV audio file.
        sample_rate: Target sample rate (used as cache key; no resampling
            is performed here).

    Returns:
        Raw PCM-16 bytes, or *None* on failure.
    """
    cache_key = (file_path, sample_rate)
    if cache_key in _audio_cache:
        logger.debug(f"Using cached audio for {file_path} at {sample_rate}Hz")
        return _audio_cache[cache_key]

    try:
        logger.info(f"Loading audio from {file_path} at {sample_rate}Hz")
        sound, file_sample_rate = sf.read(file_path, dtype="int16")
        logger.info(
            f"Audio file loaded - file sample_rate: {file_sample_rate}, target: {sample_rate}"
        )

        # Ensure mono (take first channel if stereo)
        if len(sound.shape) > 1:
            sound = sound[:, 0]

        if file_sample_rate != sample_rate:
            logger.warning(
                f"Audio file has sample rate {file_sample_rate}, expected {sample_rate}"
            )

        audio_bytes = sound.astype(np.int16).tobytes()
        _audio_cache[cache_key] = audio_bytes
        logger.info(f"Audio loaded: {len(sound)} samples at {sample_rate}Hz")
        return audio_bytes

    except Exception as e:
        logger.error(f"Failed to load audio file {file_path}: {e}")
        return None


def clear_audio_cache() -> None:
    """Clear the audio file cache to free memory."""
    _audio_cache.clear()
    logger.info("Audio cache cleared")


# ---------------------------------------------------------------------------
# Playback helpers
# ---------------------------------------------------------------------------


async def play_audio(
    audio_data: bytes,
    *,
    sample_rate: int,
    queue_frame: Callable[[Frame], Awaitable[None]],
    transcript: Optional[str] = None,
    append_to_context: bool = False,
) -> None:
    """Play raw PCM-16 audio once.

    Pushes ``TTSStarted -> TTSAudioRaw -> TTSStopped`` so downstream
    processors (audio buffer, context aggregators) handle the audio
    correctly.

    When *transcript* is provided a ``TTSTextFrame`` is also pushed so
    that observers (e.g. ``RealtimeFeedbackObserver``) can relay the
    spoken text to the UI.

    Args:
        audio_data: Raw 16-bit mono PCM bytes.
        sample_rate: Pipeline sample rate (e.g. 16000).
        queue_frame: Frame sink -- typically ``transport.output().queue_frame``.
        transcript: Optional transcript of the recording.
        append_to_context: Whether the transcript should be appended to
            the LLM assistant context.  Defaults to False.
    """
    context_id = str(uuid.uuid4())
    await queue_frame(TTSStartedFrame(context_id=context_id))
    if transcript:
        tts_text = TTSTextFrame(
            text=transcript, aggregated_by="recording", context_id=context_id
        )
        tts_text.append_to_context = append_to_context
        await queue_frame(tts_text)
    await queue_frame(
        TTSAudioRawFrame(
            audio=audio_data,
            sample_rate=sample_rate,
            num_channels=1,
            context_id=context_id,
        )
    )
    await queue_frame(TTSStoppedFrame(context_id=context_id))


async def play_audio_loop(
    *,
    stop_event: asyncio.Event,
    sample_rate: int,
    queue_frame: Callable[[Frame], Awaitable[None]],
    audio_file: Optional[str] = None,
) -> None:
    """Play audio in a loop until *stop_event* is set.

    Used for hold music during call transfers and ringers during
    pre-call data fetches.

    Args:
        stop_event: Set this event to terminate the loop.
        sample_rate: Target sample rate for audio playback.
        queue_frame: Frame sink -- typically ``transport.output().queue_frame``.
        audio_file: Path to a WAV file.  When *None* the default
            ``transfer_hold_ring_{sample_rate}.wav`` asset is used.
    """
    if audio_file is None:
        from api.constants import APP_ROOT_DIR

        audio_file = str(
            APP_ROOT_DIR / "assets" / f"transfer_hold_ring_{sample_rate}.wav"
        )

    audio_data = load_audio_file(audio_file, sample_rate)
    if not audio_data:
        logger.warning(f"Audio loop: failed to load {audio_file}, skipping")
        return

    num_samples = len(audio_data) // 2  # 16-bit PCM = 2 bytes per sample
    duration = num_samples / sample_rate

    logger.debug(f"Audio loop: playing at {sample_rate}Hz")
    try:
        while not stop_event.is_set():
            frame = OutputAudioRawFrame(
                audio=audio_data,
                sample_rate=sample_rate,
                num_channels=1,
            )
            await queue_frame(frame)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=duration + 1.5)
                break
            except asyncio.TimeoutError:
                pass
    except Exception as e:
        logger.error(f"Audio loop error: {e}")
    logger.debug("Audio loop: stopped")
