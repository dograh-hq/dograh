import numpy as np
import pytest
from pipecat.audio.filters.base_audio_filter import BaseAudioFilter
from pipecat.frames.frames import FilterEnableFrame

from api.schemas.workflow_configurations import WorkflowConfigurationDefaults
from api.services.pipecat.noise_reduction import factory
from api.services.pipecat.noise_reduction.factory import build_audio_in_filter
from api.services.pipecat.noise_reduction.rnnoise import RNNoiseAudioFilter
from api.services.pipecat.noise_reduction.safe_filter import SafeAudioFilter


def _noise(sample_rate: int, seconds: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(sample_rate * seconds)) * 1000).astype(np.int16)


async def _run(filt: BaseAudioFilter, audio: np.ndarray, sample_rate: int) -> bytes:
    chunk = sample_rate // 50  # 20 ms, as telephony transports deliver it
    out = [
        await filt.filter(audio[i : i + chunk].tobytes())
        for i in range(0, len(audio), chunk)
    ]
    return b"".join(out)


def _rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


# --- factory ---------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [None, {}, {"enabled": False}, {"enabled": True, "engine": "unknown"}],
)
def test_build_audio_in_filter_returns_none_when_not_enabled(config):
    assert build_audio_in_filter(config) is None


def test_build_audio_in_filter_builds_fresh_filter_per_call():
    first = build_audio_in_filter({"enabled": True})
    second = build_audio_in_filter({"enabled": True, "engine": "rnnoise"})

    assert isinstance(first, SafeAudioFilter)
    assert isinstance(second, SafeAudioFilter)
    assert first is not second


def test_kill_switch_overrides_workflow_config(monkeypatch):
    monkeypatch.setattr(factory, "NOISE_REDUCTION_ENABLED", False)

    assert build_audio_in_filter({"enabled": True}) is None


def test_workflow_defaults_leave_noise_reduction_off():
    config = WorkflowConfigurationDefaults.model_validate(
        {"noise_reduction_configuration": None}
    )

    assert config.noise_reduction_configuration.enabled is False
    assert config.noise_reduction_configuration.engine == "rnnoise"


# --- RNNoise engine --------------------------------------------------------


@pytest.mark.parametrize("sample_rate", [8000, 16000, 24000, 48000])
async def test_rnnoise_keeps_pace_with_input(sample_rate):
    filt = RNNoiseAudioFilter()
    await filt.start(sample_rate)
    audio = _noise(sample_rate, 2.0)

    out = await _run(filt, audio, sample_rate)
    await filt.stop()

    assert len(out) % 2 == 0
    # Only the partial 10 ms frame and the resampler's delay may lag behind.
    lag = len(audio) - len(out) // 2
    assert 0 <= lag <= sample_rate // 100 + sample_rate // 200


@pytest.mark.parametrize("sample_rate", [8000, 16000])
async def test_rnnoise_suppresses_stationary_noise(sample_rate):
    filt = RNNoiseAudioFilter()
    await filt.start(sample_rate)
    audio = _noise(sample_rate, 2.0)

    out = np.frombuffer(await _run(filt, audio, sample_rate), dtype=np.int16)
    await filt.stop()

    # Skip the first half second while the recurrent state settles.
    settled = sample_rate // 2
    assert _rms(out[settled:]) < _rms(audio[settled:]) * 0.1


async def test_rnnoise_disable_frame_passes_audio_through():
    filt = RNNoiseAudioFilter()
    await filt.start(8000)
    await filt.process_frame(FilterEnableFrame(enable=False))
    audio = _noise(8000, 0.02).tobytes()

    assert await filt.filter(audio) == audio
    await filt.stop()


async def test_rnnoise_instances_do_not_share_state():
    audio = _noise(8000, 1.0, seed=1)
    other_call = RNNoiseAudioFilter()
    await other_call.start(8000)
    await _run(other_call, _noise(8000, 1.0, seed=2), 8000)

    first = RNNoiseAudioFilter()
    second = RNNoiseAudioFilter()
    await first.start(8000)
    await second.start(8000)
    await _run(other_call, _noise(8000, 0.5, seed=3), 8000)

    assert await _run(first, audio, 8000) == await _run(second, audio, 8000)
    for filt in (other_call, first, second):
        await filt.stop()


# --- SafeAudioFilter -------------------------------------------------------


class _FakeEngine(BaseAudioFilter):
    def __init__(self, *, fail_start=False, fail_filter=False):
        self.fail_start = fail_start
        self.fail_filter = fail_filter
        self.control_frames = []
        self.stopped = False

    async def start(self, sample_rate):
        if self.fail_start:
            raise OSError("no library")

    async def stop(self):
        self.stopped = True

    async def process_frame(self, frame):
        self.control_frames.append(frame)

    async def filter(self, audio):
        if self.fail_filter:
            raise RuntimeError("boom")
        return b"\x00" * len(audio)


async def test_safe_filter_passes_through_when_engine_fails_to_start():
    filt = SafeAudioFilter(_FakeEngine(fail_start=True), "fake")
    await filt.start(8000)

    assert await filt.filter(b"\x01\x02") == b"\x01\x02"
    assert filt.active is False
    assert "start failed" in filt.disabled_reason


async def test_safe_filter_trips_after_repeated_errors():
    engine = _FakeEngine(fail_filter=True)
    filt = SafeAudioFilter(engine, "fake", max_errors=3)
    await filt.start(8000)

    for _ in range(3):
        assert await filt.filter(b"\x01\x02") == b"\x01\x02"

    assert filt.active is False
    engine.fail_filter = False
    assert await filt.filter(b"\x01\x02") == b"\x01\x02"


async def test_safe_filter_trips_when_engine_stays_over_budget():
    filt = SafeAudioFilter(
        _FakeEngine(), "fake", frame_budget_ms=-1, max_consecutive_slow_frames=2
    )
    await filt.start(8000)

    assert await filt.filter(b"\x01\x02") == b"\x00\x00"
    assert filt.active is True
    await filt.filter(b"\x01\x02")

    assert filt.active is False
    assert "consecutive frames" in filt.disabled_reason


async def test_safe_filter_forwards_control_frames_and_stops_engine():
    engine = _FakeEngine()
    filt = SafeAudioFilter(engine, "fake")
    await filt.start(8000)
    frame = FilterEnableFrame(enable=False)

    await filt.process_frame(frame)
    await filt.stop()

    assert engine.control_frames == [frame]
    assert engine.stopped is True
