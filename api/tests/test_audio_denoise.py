import io
import subprocess
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.constants import APP_ROOT_DIR
from api.routes.audio import router
from api.services.auth.depends import get_user
from api.services.pipecat.noise_reduction import file_denoise
from api.services.pipecat.noise_reduction.file_denoise import (
    AudioDecodeError,
    AudioTooLongError,
    denoise_audio_file,
    denoise_pcm_48k,
)

SPEECH_WAV = (
    APP_ROOT_DIR.parent / "pipecat/scripts/provider-watch/assets/speech-16k.wav"
)


def _wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.astype(np.int16).tobytes())
    return buffer.getvalue()


def _read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as wav:
        return np.frombuffer(
            wav.readframes(wav.getnframes()), np.int16
        ), wav.getframerate()


def _speech() -> np.ndarray:
    with wave.open(str(SPEECH_WAV)) as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), np.int16)


def _noisy_speech(seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """1 s of noise, then speech in the same noise, at 16 kHz."""
    speech = _speech()
    clean = np.concatenate([np.zeros(16000, np.int16), speech])
    noise = np.random.default_rng(seed).standard_normal(len(clean)) * 800
    noisy = np.clip(clean + noise, -32768, 32767).astype(np.int16)
    return clean, noisy


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


# --- service ---------------------------------------------------------------


def test_denoise_pcm_48k_is_aligned_with_input():
    speech = np.repeat(_speech(), 3)  # crude 16 kHz -> 48 kHz, fine for alignment
    out = denoise_pcm_48k(speech)

    assert out.shape == speech.shape
    a, b = speech.astype(np.float64), out.astype(np.float64)
    lags = [np.dot(a[: len(a) - lag], b[lag:]) for lag in range(0, 200)]
    lags_back = [np.dot(a[lag:], b[: len(b) - lag]) for lag in range(0, 200)]
    assert max(lags) >= max(lags_back)
    assert int(np.argmax(lags)) <= 2


async def test_denoise_file_keeps_length_and_rate_and_removes_noise():
    _, noisy = _noisy_speech()

    result = await denoise_audio_file(_wav_bytes(noisy, 16000))
    out, rate = _read_wav(result.audio)

    assert result.media_type == "audio/wav"
    assert rate == result.sample_rate == 16000
    assert abs(len(out) - len(noisy)) <= 16
    assert result.duration_secs == pytest.approx(len(noisy) / 16000, abs=0.01)
    # The leading second is noise only; skip the first half while RNNoise settles.
    assert _rms(out[8000:16000]) < _rms(noisy[8000:16000]) * 0.1
    level_change_db = 20 * np.log10(_rms(out[16000:]) / _rms(noisy[16000:]))
    assert -6 < level_change_db < 1


@pytest.mark.parametrize("sample_rate", [8000, 22050, 48000])
async def test_denoise_file_resamples_to_requested_rate(sample_rate):
    _, noisy = _noisy_speech()

    result = await denoise_audio_file(_wav_bytes(noisy, 16000), sample_rate=sample_rate)
    out, rate = _read_wav(result.audio)

    assert rate == sample_rate
    assert abs(len(out) - len(noisy) * sample_rate / 16000) <= sample_rate / 1000


@pytest.mark.parametrize(
    "output_format,codec", [("mp3", "mp3"), ("flac", "flac"), ("wav", "pcm_s16le")]
)
async def test_denoise_file_encodes_requested_format(output_format, codec):
    _, noisy = _noisy_speech()

    result = await denoise_audio_file(_wav_bytes(noisy, 16000), output_format)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,channels"]
        + ["-of", "csv=p=0", "pipe:0"],
        input=result.audio,
        capture_output=True,
        check=True,
    )
    assert probe.stdout.decode().strip() == f"{codec},1"


async def test_denoise_file_accepts_compressed_stereo_input():
    _, noisy = _noisy_speech()
    stereo_mp3 = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "s16le", "-ar", "16000", "-ac", "1"]
        + ["-i", "pipe:0", "-ac", "2", "-ar", "44100", "-f", "mp3", "pipe:1"],
        input=noisy.tobytes(),
        capture_output=True,
        check=True,
    ).stdout

    result = await denoise_audio_file(stereo_mp3)
    out, rate = _read_wav(result.audio)

    assert rate == 44100
    assert len(out) > 44100 * 5


@pytest.mark.parametrize("data", [b"not audio at all", b"\x00" * 4096])
async def test_denoise_file_rejects_non_audio(data):
    with pytest.raises(AudioDecodeError):
        await denoise_audio_file(data)


async def test_denoise_file_rejects_audio_over_the_duration_limit(monkeypatch):
    monkeypatch.setattr(file_denoise, "MAX_DURATION_SECS", 1)
    _, noisy = _noisy_speech()

    with pytest.raises(AudioTooLongError):
        await denoise_audio_file(_wav_bytes(noisy, 16000))


# --- route -----------------------------------------------------------------


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=1, selected_organization_id=1
    )
    return TestClient(app)


def test_route_returns_denoised_file(client):
    _, noisy = _noisy_speech()

    response = client.post(
        "/audio/denoise",
        files={"file": ("my call;1.wav", _wav_bytes(noisy, 16000), "audio/wav")},
        data={"output_format": "wav"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="my_call_1-denoised.wav"'
    )
    assert float(response.headers["x-audio-duration-seconds"]) == pytest.approx(
        len(noisy) / 16000, abs=0.01
    )
    assert response.headers["x-audio-sample-rate"] == "16000"
    assert int(response.headers["x-processing-time-ms"]) >= 0
    out, _ = _read_wav(response.content)
    assert _rms(out[8000:16000]) < _rms(noisy[8000:16000]) * 0.1


def test_route_rejects_garbage_with_400(client):
    response = client.post(
        "/audio/denoise", files={"file": ("x.wav", b"garbage", "audio/wav")}
    )

    assert response.status_code == 400


def test_route_rejects_empty_file_with_400(client):
    response = client.post(
        "/audio/denoise", files={"file": ("x.wav", b"", "audio/wav")}
    )

    assert response.status_code == 400


def test_route_rejects_oversized_upload_with_413(client, monkeypatch):
    monkeypatch.setattr("api.routes.audio.MAX_UPLOAD_BYTES", 1000)

    response = client.post(
        "/audio/denoise", files={"file": ("x.wav", b"\x00" * 2000, "audio/wav")}
    )

    assert response.status_code == 413


@pytest.mark.parametrize(
    "data",
    [{"output_format": "ogg"}, {"sample_rate": "4000"}, {"sample_rate": "96000"}],
)
def test_route_validates_options(client, data):
    _, noisy = _noisy_speech()

    response = client.post(
        "/audio/denoise",
        files={"file": ("x.wav", _wav_bytes(noisy[:16000], 16000), "audio/wav")},
        data=data,
    )

    assert response.status_code == 422


def test_route_requires_authentication():
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).post(
        "/audio/denoise", files={"file": ("x.wav", b"data", "audio/wav")}
    )

    assert response.status_code in (401, 403)
