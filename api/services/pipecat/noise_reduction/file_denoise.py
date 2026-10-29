"""Denoise a whole audio file and re-encode it.

Two modes:

- ``speech`` (default) replays the file through :class:`SpeechGatedDenoiser`
  in 20 ms frames at 16 kHz (8 kHz when 8 kHz output is asked for), exactly as
  a live call would feed it, so the result is what STT would receive: the
  user's denoised speech, silence everywhere else.
- ``full`` denoises the whole file with RNNoise at its native 48 kHz in a
  worker thread (ctypes releases the GIL).

In both modes the engine's fixed delay is trimmed so the output lines up with
the input. A semaphore bounds how many files are processed at once so uploads
cannot starve the live calls sharing the worker.
"""

import asyncio
import ctypes
import io
import json
import shutil
import time
import wave
from dataclasses import dataclass
from typing import Literal

import numpy as np
from loguru import logger

from api.services.pipecat.noise_reduction.rnnoise import (
    RNNOISE_SAMPLE_RATE,
    load_rnnoise_library,
)
from api.services.pipecat.noise_reduction.speech_gate import (
    DENOISER_DELAY_SECS,
    SpeechGatedDenoiser,
    gate_mask,
)

OutputFormat = Literal["wav", "mp3", "flac"]
DenoiseMode = Literal["speech", "full"]

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_DURATION_SECS = 600
MIN_SAMPLE_RATE = 8000
MAX_SAMPLE_RATE = 48000
MAX_CONCURRENT_JOBS = 2
FFMPEG_TIMEOUT_SECS = 120

# RNNoise's output trails its input by two 10 ms frames.
ENGINE_DELAY_FRAMES = 2

# WAV is wrapped in Python: ffmpeg cannot fill in the header's length fields
# when writing to a pipe, and some players reject the placeholder values.
_ENCODERS: dict[str, tuple[list[str], str]] = {
    "wav": (["-f", "s16le", "-acodec", "pcm_s16le"], "audio/wav"),
    "mp3": (["-f", "mp3", "-acodec", "libmp3lame", "-q:a", "2"], "audio/mpeg"),
    "flac": (["-f", "flac", "-acodec", "flac"], "audio/flac"),
}

_job_slots = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


class AudioDecodeError(ValueError):
    """The upload is not audio ffmpeg can read."""


class AudioTooLongError(ValueError):
    """The upload is longer than ``MAX_DURATION_SECS``."""


@dataclass
class DenoisedAudio:
    audio: bytes
    media_type: str
    sample_rate: int
    duration_secs: float
    processing_ms: float
    speech_segments: int | None = None
    speech_secs: float | None = None


async def denoise_audio_file(
    data: bytes,
    output_format: OutputFormat = "wav",
    sample_rate: int | None = None,
    mode: DenoiseMode = "speech",
) -> DenoisedAudio:
    """Return a denoised, mono copy of the audio file in ``data``.

    Args:
        data: The uploaded file, in any format ffmpeg can decode.
        output_format: Container/codec of the result.
        sample_rate: Output rate in Hz. Defaults to the processing rate (16 kHz)
            in ``speech`` mode and to the input's rate, clamped to 8-48 kHz, in
            ``full`` mode.
        mode: ``speech`` for what STT would receive, ``full`` to denoise
            everything.

    Raises:
        AudioDecodeError: ``data`` is not decodable audio.
        AudioTooLongError: The audio is longer than ``MAX_DURATION_SECS``.
    """
    started = time.perf_counter()
    speech_segments = speech_secs = None
    async with _job_slots:
        if mode == "speech":
            work_rate = 8000 if sample_rate == 8000 else 16000
            sample_rate = sample_rate or work_rate
        else:
            work_rate = RNNOISE_SAMPLE_RATE
            if sample_rate is None:
                sample_rate = await _probe_sample_rate(data)
        sample_rate = min(max(sample_rate, MIN_SAMPLE_RATE), MAX_SAMPLE_RATE)

        pcm = await _run_ffmpeg(
            ["-i", "pipe:0", "-ac", "1", "-ar", str(work_rate)]
            + ["-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"],
            data,
        )
        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            raise AudioDecodeError("No audio found in the file")
        duration_secs = samples.size / work_rate
        if duration_secs > MAX_DURATION_SECS:
            raise AudioTooLongError(
                f"Audio is {duration_secs:.0f} s long; "
                f"the limit is {MAX_DURATION_SECS} s"
            )

        if mode == "speech":
            denoised, segments = await gate_speech(samples, work_rate)
            speech_segments = len(segments)
            speech_secs = sum(end - start for start, end in segments) / work_rate
        else:
            denoised = await asyncio.to_thread(denoise_pcm_48k, samples)

        encoder_args, media_type = _ENCODERS[output_format]
        audio = await _run_ffmpeg(
            ["-f", "s16le", "-ar", str(work_rate), "-ac", "1", "-i", "pipe:0"]
            + ["-ar", str(sample_rate), *encoder_args, "pipe:1"],
            denoised.tobytes(),
        )
        if output_format == "wav":
            audio = _wrap_wav(audio, sample_rate)

    return DenoisedAudio(
        audio=audio,
        media_type=media_type,
        sample_rate=sample_rate,
        duration_secs=duration_secs,
        processing_ms=(time.perf_counter() - started) * 1000,
        speech_segments=speech_segments,
        speech_secs=speech_secs,
    )


async def gate_speech(
    samples: np.ndarray, sample_rate: int, **gate_options
) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Run ``samples`` through the live speech gate in 20 ms frames.

    Returns the gated audio aligned with ``samples`` and the speech spans in
    input samples. Live, STT gets the pre-roll as a burst when the gate opens;
    here the same audio is placed back at its original position in time.
    """
    gate = SpeechGatedDenoiser(sample_rate, **gate_options)
    await gate.start()
    chunk = sample_rate // 50
    denoised_parts = []
    try:
        for i in range(0, samples.size, chunk):
            result = await gate.process(samples[i : i + chunk].tobytes())
            denoised_parts.append(result.denoised)
    finally:
        await gate.stop()

    denoised = np.frombuffer(b"".join(denoised_parts), dtype=np.int16)
    mask = gate_mask(gate.segments, denoised.size, gate.fade_samples)
    gated = (denoised.astype(np.float32) * mask).astype(np.int16)

    delay = round(DENOISER_DELAY_SECS * sample_rate)
    aligned = np.zeros(samples.size, dtype=np.int16)
    tail = gated[delay : delay + samples.size]
    aligned[: tail.size] = tail
    spans = [
        (max(0, start - delay), (denoised.size if end is None else end) - delay)
        for start, end in gate.segments
    ]
    return aligned, spans


def denoise_pcm_48k(samples: np.ndarray) -> np.ndarray:
    """Denoise 48 kHz mono int16 samples; the output is aligned with the input."""
    lib = load_rnnoise_library()
    frame_size = lib.rnnoise_get_frame_size()
    delay = ENGINE_DELAY_FRAMES * frame_size

    # Pad so every input sample, delayed by the engine, lands in a full frame.
    padded_size = -(-(samples.size + delay) // frame_size) * frame_size
    signal = np.zeros(padded_size, dtype=np.float32)
    signal[: samples.size] = samples

    state = lib.rnnoise_create(None)
    if not state:
        raise RuntimeError("rnnoise_create returned NULL")
    frame = np.zeros(frame_size, dtype=np.float32)
    frame_ptr = frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    try:
        for start in range(0, padded_size, frame_size):
            frame[:] = signal[start : start + frame_size]
            lib.rnnoise_process_frame(state, frame_ptr, frame_ptr)
            signal[start : start + frame_size] = frame
    finally:
        lib.rnnoise_destroy(state)

    aligned = signal[delay : delay + samples.size]
    return np.clip(aligned, -32768, 32767).astype(np.int16)


def _wrap_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


async def _probe_sample_rate(data: bytes) -> int:
    out = await _run_ffmpeg(
        ["-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate"]
        + ["-of", "json", "pipe:0"],
        data,
        binary="ffprobe",
    )
    try:
        return int(json.loads(out)["streams"][0]["sample_rate"])
    except (ValueError, KeyError, IndexError) as e:
        raise AudioDecodeError("No audio stream found in the file") from e


async def _run_ffmpeg(args: list[str], stdin: bytes, binary: str = "ffmpeg") -> bytes:
    path = shutil.which(binary)
    if not path:
        raise RuntimeError(f"{binary} is not installed")
    cmd = [path, *args] if binary == "ffprobe" else [path, "-loglevel", "error", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(stdin), timeout=FFMPEG_TIMEOUT_SECS
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace")[:500]
        logger.info(f"{binary} rejected upload: {detail}")
        raise AudioDecodeError("Could not decode the audio file")
    return stdout
