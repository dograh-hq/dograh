"""Shared helper for building audio output mixers used by telephony transports."""

import os

from loguru import logger

from api.constants import APP_ROOT_DIR
from api.services.pipecat.audio_file_cache import get_cached_ambient_noise_path
from pipecat.audio.mixers.silence_mixer import SilenceAudioMixer
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer

librnnoise_path = os.path.normpath(
    str(APP_ROOT_DIR / "native" / "rnnoise" / "librnnoise.so")
)


async def build_audio_out_mixer(
    audio_out_sample_rate: int,
    ambient_noise_config: dict | None,
):
    """Build the audio output mixer based on the ambient noise configuration.

    Returns a ``SoundfileMixer`` when ambient noise is enabled, or a
    ``SilenceAudioMixer`` otherwise.  Supports custom user-uploaded audio
    files via the ``storage_key`` / ``storage_backend`` fields in the config.
    """
    if not ambient_noise_config or not ambient_noise_config.get("enabled", False):
        return SilenceAudioMixer()

    volume = ambient_noise_config.get("volume", 0.3)

    storage_key = ambient_noise_config.get("storage_key")
    storage_backend = ambient_noise_config.get("storage_backend")

    if storage_key and storage_backend:
        cached_path = await get_cached_ambient_noise_path(
            storage_key, storage_backend, audio_out_sample_rate
        )
        if cached_path:
            return SoundfileMixer(
                sound_files={"custom": cached_path},
                default_sound="custom",
                volume=volume,
            )
        logger.warning("Custom ambient noise file unavailable, falling back to default")

    return SoundfileMixer(
        sound_files={
            "office": APP_ROOT_DIR
            / "assets"
            / f"office-ambience-{audio_out_sample_rate}-mono.wav"
        },
        default_sound="office",
        volume=volume,
    )
