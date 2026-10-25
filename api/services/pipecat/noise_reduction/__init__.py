"""Background noise reduction for caller audio.

The filter built here runs inside the input transport, so VAD, STT and the
call recording all receive denoised audio.
"""

from api.services.pipecat.noise_reduction.factory import build_audio_in_filter

__all__ = ["build_audio_in_filter"]
