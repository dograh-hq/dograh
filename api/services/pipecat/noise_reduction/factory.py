"""Builds the per-call input audio filter from a workflow's configuration."""

from collections.abc import Callable

from loguru import logger
from pydantic import ValidationError

from api.constants import NOISE_REDUCTION_ENABLED
from api.schemas.workflow_configurations import NoiseReductionConfigurationDefaults
from api.services.pipecat.noise_reduction.rnnoise import RNNoiseAudioFilter
from api.services.pipecat.noise_reduction.safe_filter import SafeAudioFilter
from pipecat.audio.filters.base_audio_filter import BaseAudioFilter

ENGINES: dict[str, Callable[[], BaseAudioFilter]] = {
    "rnnoise": RNNoiseAudioFilter,
}


def build_audio_in_filter(
    noise_reduction_config: dict | None,
) -> BaseAudioFilter | None:
    """Return a fresh filter for one call, or ``None`` when noise reduction is off.

    Engines keep DSP state between frames, so every call must get its own
    instance; never share the returned filter between transports.
    """
    if not noise_reduction_config or not NOISE_REDUCTION_ENABLED:
        return None

    try:
        config = NoiseReductionConfigurationDefaults.model_validate(
            noise_reduction_config
        )
    except ValidationError as e:
        logger.warning(f"Ignoring invalid noise reduction configuration: {e}")
        return None
    if not config.enabled:
        return None

    return SafeAudioFilter(ENGINES[config.engine](), config.engine)
