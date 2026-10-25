"""RNNoise engine that calls the native library directly.

Pipecat's ``RNNoiseFilter`` goes through ``pyrnnoise``, whose bundled library
build is ~3.5x slower than the one vendored under ``api/native/rnnoise`` and
which pushes every chunk through libav filter graphs on top. Together that
costs ~3-4 ms per 20 ms telephony frame, run inline on the event loop for
every call. Driving the vendored library over ctypes and doing the 48 kHz
round trip with soxr streams brings the same frame to ~1 ms.
"""

import ctypes
import functools
import importlib.util
import os
from pathlib import Path

import numpy as np
import soxr
from loguru import logger

from api.constants import APP_ROOT_DIR
from pipecat.audio.filters.base_audio_filter import BaseAudioFilter
from pipecat.frames.frames import FilterControlFrame, FilterEnableFrame

RNNOISE_SAMPLE_RATE = 48000

VENDORED_LIBRARY_PATH = APP_ROOT_DIR / "native" / "rnnoise" / "librnnoise.so"


def _candidate_library_paths() -> list[Path]:
    """Return library locations in the order they should be tried.

    An explicit ``RNNOISE_LIBRARY_PATH`` wins. The vendored build is x86-64
    only, so on other architectures the copy bundled with ``pyrnnoise`` is
    used when that package is installed. It is located without importing it,
    because its package import pulls in libav.
    """
    paths = []
    override = os.getenv("RNNOISE_LIBRARY_PATH")
    if override:
        paths.append(Path(override))
    paths.append(VENDORED_LIBRARY_PATH)
    spec = importlib.util.find_spec("pyrnnoise")
    if spec is not None and spec.origin:
        paths.append(Path(spec.origin).parent / "librnnoise.so")
    return paths


@functools.cache
def load_rnnoise_library() -> ctypes.CDLL:
    """Load librnnoise once per process and declare its signatures.

    Raises:
        OSError: When no candidate library can be loaded.
    """
    errors = []
    for path in _candidate_library_paths():
        if not path.exists():
            continue
        try:
            lib = ctypes.CDLL(str(path))
        except OSError as e:
            errors.append(f"{path}: {e}")
            continue
        lib.rnnoise_create.argtypes = [ctypes.c_void_p]
        lib.rnnoise_create.restype = ctypes.c_void_p
        lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        lib.rnnoise_destroy.restype = None
        lib.rnnoise_get_frame_size.argtypes = []
        lib.rnnoise_get_frame_size.restype = ctypes.c_int
        lib.rnnoise_process_frame.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
        ]
        lib.rnnoise_process_frame.restype = ctypes.c_float
        logger.info(f"Loaded RNNoise library from {path}")
        return lib
    detail = "; ".join(errors) if errors else "no library file found"
    raise OSError(f"Unable to load librnnoise ({detail})")


class RNNoiseAudioFilter(BaseAudioFilter):
    """Streaming RNNoise suppression for 16-bit mono PCM at any sample rate.

    Audio is resampled to 48 kHz, cut into the library's 10 ms frames,
    denoised in place and resampled back. Samples that do not yet fill a frame
    are carried over to the next call, so a call may return slightly fewer (or
    more) bytes than it was given, but output keeps pace with input over time.
    """

    def __init__(self, resampler_quality: str = "QQ") -> None:
        """Initialize the filter.

        Args:
            resampler_quality: soxr quality for the 48 kHz round trip. "QQ"
                adds well under 1 ms of delay; the higher qualities buffer
                tens of milliseconds at 8 kHz, which delays every turn.
        """
        self._resampler_quality = resampler_quality
        self._lib: ctypes.CDLL | None = None
        self._state: int | None = None
        self._frame_size = 0
        self._sample_rate = 0
        self._filtering = True
        self._resampler_in: soxr.ResampleStream | None = None
        self._resampler_out: soxr.ResampleStream | None = None
        self._pending = np.zeros(0, dtype=np.float32)
        self._frame = np.zeros(0, dtype=np.float32)
        self._frame_ptr = None

    async def start(self, sample_rate: int):
        """Create the denoiser state for one call.

        Raises:
            OSError: When the native library cannot be loaded.
        """
        self._sample_rate = sample_rate
        self._lib = load_rnnoise_library()
        self._frame_size = self._lib.rnnoise_get_frame_size()
        self._state = self._lib.rnnoise_create(None)
        if not self._state:
            raise RuntimeError("rnnoise_create returned NULL")
        self._frame = np.zeros(self._frame_size, dtype=np.float32)
        self._frame_ptr = self._frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        self._reset_stream()

    async def stop(self):
        """Release the denoiser state."""
        if self._lib is not None and self._state:
            self._lib.rnnoise_destroy(self._state)
        self._state = None
        self._resampler_in = None
        self._resampler_out = None

    async def process_frame(self, frame: FilterControlFrame):
        """Handle runtime enable/disable requests."""
        if isinstance(frame, FilterEnableFrame) and frame.enable != self._filtering:
            self._filtering = frame.enable
            # Buffered audio from before the toggle no longer lines up with
            # what comes next, so start the streams from scratch.
            self._reset_stream()

    async def filter(self, audio: bytes) -> bytes:
        """Return denoised audio for ``audio`` (16-bit mono PCM)."""
        if not self._filtering or not self._state or not audio:
            return audio

        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
        if self._resampler_in is not None:
            samples = self._resampler_in.resample_chunk(samples)
        if self._pending.size:
            samples = np.concatenate((self._pending, samples))

        frame_count = samples.size // self._frame_size
        processed_size = frame_count * self._frame_size
        self._pending = samples[processed_size:].copy()
        if frame_count == 0:
            return b""

        denoised = np.empty(processed_size, dtype=np.float32)
        for i in range(frame_count):
            start = i * self._frame_size
            end = start + self._frame_size
            # RNNoise works on float samples in int16 scale, in place.
            self._frame[:] = samples[start:end]
            self._lib.rnnoise_process_frame(
                self._state, self._frame_ptr, self._frame_ptr
            )
            denoised[start:end] = self._frame

        if self._resampler_out is not None:
            denoised = self._resampler_out.resample_chunk(denoised)
        return np.clip(denoised, -32768, 32767).astype(np.int16).tobytes()

    def _reset_stream(self):
        self._pending = np.zeros(0, dtype=np.float32)
        if self._sample_rate == RNNOISE_SAMPLE_RATE:
            self._resampler_in = None
            self._resampler_out = None
            return
        self._resampler_in = soxr.ResampleStream(
            self._sample_rate,
            RNNOISE_SAMPLE_RATE,
            1,
            dtype="float32",
            quality=self._resampler_quality,
        )
        self._resampler_out = soxr.ResampleStream(
            RNNOISE_SAMPLE_RATE,
            self._sample_rate,
            1,
            dtype="float32",
            quality=self._resampler_quality,
        )
