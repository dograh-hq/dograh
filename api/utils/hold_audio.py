"""Utility for loading and playing hold audio files."""

from typing import Dict

import soundfile as sf
from loguru import logger

from api.constants import APP_ROOT_DIR

# Cache for loaded audio data
_audio_cache: Dict[str, bytes] = {}


def load_hold_audio(sample_rate: int) -> bytes:
    """Load hold audio file as raw PCM bytes for the given sample rate.

    Args:
        sample_rate: The sample rate to load (8000 or 16000)

    Returns:
        Raw PCM audio bytes (16-bit signed, mono)

    Raises:
        FileNotFoundError: If the audio file doesn't exist
        ValueError: If sample rate is not supported
    """
    if sample_rate not in (8000, 16000):
        raise ValueError(
            f"Unsupported sample rate: {sample_rate}. Must be 8000 or 16000"
        )

    cache_key = f"hold_ring_{sample_rate}"

    if cache_key in _audio_cache:
        return _audio_cache[cache_key]

    # Construct path to the audio file
    assets_dir = APP_ROOT_DIR / "assets"
    audio_file = assets_dir / f"transfer_hold_ring_{sample_rate}.wav"

    if not audio_file.exists():
        raise FileNotFoundError(f"Hold audio file not found: {audio_file}")

    # Load the audio file
    audio_data, file_sample_rate = sf.read(str(audio_file), dtype="int16")

    if file_sample_rate != sample_rate:
        logger.warning(
            f"Audio file sample rate ({file_sample_rate}) doesn't match "
            f"requested rate ({sample_rate})"
        )

    # Convert to bytes
    audio_bytes = audio_data.tobytes()

    # Cache for future use
    _audio_cache[cache_key] = audio_bytes

    logger.debug(
        f"Loaded hold audio: {audio_file.name}, "
        f"duration={len(audio_data) / sample_rate:.2f}s"
    )

    return audio_bytes


def get_hold_audio_duration_ms(sample_rate: int) -> int:
    """Get the duration of the hold audio in milliseconds.

    Args:
        sample_rate: The sample rate (8000 or 16000)

    Returns:
        Duration in milliseconds
    """
    audio_bytes = load_hold_audio(sample_rate)
    # 2 bytes per sample (16-bit PCM)
    num_samples = len(audio_bytes) // 2
    duration_ms = int((num_samples / sample_rate) * 1000)
    return duration_ms
