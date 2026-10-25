"""Speech-gated denoising: STT hears the user's denoised speech and nothing else.

Every frame is denoised (RNNoise needs a continuous stream to track the
noise) and analysed by Silero VAD on the raw audio. While the user is not
speaking the output is silence. When VAD confirms speech the gate opens and
also releases the audio buffered just before that moment, so the start of the
first word is not lost to VAD's confirmation delay; it stays open for a short
hold after VAD reports the end of speech, so word endings are not clipped.
Short fades at both edges avoid clicks.

The gate's VAD is more sensitive than the agent's turn-taking VAD: opening on
a little noise costs nothing because that audio is denoised, while missing a
quiet word loses it for STT. Inside the gate a share of the original audio is
mixed back in (the speech floor), because RNNoise suppresses soft consonants
by 10 dB or more on a fifth of speech frames.
"""

from dataclasses import dataclass, field

import numpy as np

from api.services.pipecat.noise_reduction.rnnoise import RNNoiseAudioFilter
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

# Must cover VAD's start_secs (0.2 s) plus soft word onsets that score low.
DEFAULT_PRE_ROLL_SECS = 0.4
DEFAULT_POST_ROLL_SECS = 0.3
DEFAULT_SPEECH_FLOOR = 0.3
DEFAULT_FADE_SECS = 0.01
# RNNoiseAudioFilter output trails its input by two 10 ms engine frames.
DENOISER_DELAY_SECS = 0.02

_GATE_OPEN_STATES = (VADState.SPEAKING, VADState.STOPPING)


@dataclass
class GateResult:
    """Output of one :meth:`SpeechGatedDenoiser.process` call.

    Parameters:
        stt_audio: What to send to STT: silence while the user is quiet, the
            buffered pre-roll plus the current audio when speech starts, the
            gate's audio while it continues.
        denoised: The ungated audio the gate passes when open (denoised plus
            the speech floor), aligned with the denoised stream.
        speaking: Whether the gate is open after this call.
    """

    stt_audio: bytes
    denoised: bytes
    speaking: bool


@dataclass
class _Segment:
    start: int
    end: int | None = None


@dataclass
class SpeechGatedDenoiser:
    """Streaming speech gate over 16-bit mono PCM at 8 or 16 kHz.

    Parameters:
        sample_rate: Input rate; Silero VAD supports 8000 and 16000.
        vad_params: Gate VAD thresholds; more sensitive than the agent's.
        pre_roll_secs: Audio released from before speech is confirmed.
        post_roll_secs: How long the gate stays open after speech ends.
        speech_floor: Share of the original audio mixed in while open (0-1).
        fade_secs: Fade length at the gate's edges.
    """

    sample_rate: int
    vad_params: VADParams = field(
        default_factory=lambda: VADParams(confidence=0.5, stop_secs=0.2, min_volume=0.3)
    )
    pre_roll_secs: float = DEFAULT_PRE_ROLL_SECS
    post_roll_secs: float = DEFAULT_POST_ROLL_SECS
    speech_floor: float = DEFAULT_SPEECH_FLOOR
    fade_secs: float = DEFAULT_FADE_SECS

    def __post_init__(self):
        if self.sample_rate not in (8000, 16000):
            raise ValueError("Speech gating supports 8000 or 16000 Hz input")
        self._denoiser = RNNoiseAudioFilter()
        self._vad = SileroVADAnalyzer(
            sample_rate=self.sample_rate, params=self.vad_params
        )
        self._pre_roll_samples = int(self.pre_roll_secs * self.sample_rate)
        self._post_roll_samples = int(self.post_roll_secs * self.sample_rate)
        self._held = 0
        # Raw audio delayed to line up with the denoised stream.
        delay = round(DENOISER_DELAY_SECS * self.sample_rate)
        self._raw_line = np.zeros(delay, dtype=np.int16)
        self._fade_samples = max(1, int(self.fade_secs * self.sample_rate))
        self._pre_roll = np.zeros(0, dtype=np.int16)
        self._open = False
        self._denoised_pos = 0
        self._segments: list[_Segment] = []

    @property
    def fade_samples(self) -> int:
        return self._fade_samples

    @property
    def segments(self) -> list[tuple[int, int | None]]:
        """Gate-open spans, in samples of the denoised stream (``end`` None if open)."""
        return [(s.start, s.end) for s in self._segments]

    async def start(self):
        self._vad.set_sample_rate(self.sample_rate)
        await self._denoiser.start(self.sample_rate)

    async def stop(self):
        await self._denoiser.stop()

    async def process(self, audio: bytes) -> GateResult:
        """Gate one chunk of raw user audio (any length, typically 20 ms)."""
        denoised = np.frombuffer(await self._denoiser.filter(audio), dtype=np.int16)
        denoised = self._apply_speech_floor(audio, denoised)
        state = await self._vad.analyze_audio(audio)
        speaking = state in _GATE_OPEN_STATES
        self._denoised_pos += denoised.size

        if not self._open and speaking:
            out = np.concatenate((self._pre_roll, denoised))
            out = self._fade(out, rising=True)
            self._pre_roll = np.zeros(0, dtype=np.int16)
            self._segments.append(_Segment(start=self._denoised_pos - out.size))
            self._open = True
        elif not self._open:
            self._pre_roll = np.concatenate((self._pre_roll, denoised))[
                -self._pre_roll_samples :
            ]
            out = np.zeros(len(audio) // 2, dtype=np.int16)
        elif speaking:
            self._held = 0
            out = denoised
        elif self._held < self._post_roll_samples:
            self._held += denoised.size
            out = denoised
        else:
            out = self._fade(denoised, rising=False)
            self._segments[-1].end = self._denoised_pos
            self._open = False
            self._held = 0

        return GateResult(
            stt_audio=out.tobytes(), denoised=denoised.tobytes(), speaking=self._open
        )

    def _apply_speech_floor(self, audio: bytes, denoised: np.ndarray) -> np.ndarray:
        self._raw_line = np.concatenate(
            (self._raw_line, np.frombuffer(audio, dtype=np.int16))
        )
        raw = self._raw_line[: denoised.size]
        self._raw_line = self._raw_line[denoised.size :]
        if self.speech_floor <= 0 or denoised.size == 0:
            return denoised
        mixed = denoised.astype(np.float32) + self.speech_floor * raw.astype(np.float32)
        return np.clip(mixed, -32768, 32767).astype(np.int16)

    def _fade(self, samples: np.ndarray, *, rising: bool) -> np.ndarray:
        n = min(self._fade_samples, samples.size)
        if n == 0:
            return samples
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        out = samples.astype(np.float32)
        if rising:
            out[:n] *= ramp
        else:
            out[-n:] *= ramp[::-1]
        return out.astype(np.int16)


def gate_mask(
    segments: list[tuple[int, int | None]], length: int, fade_samples: int
) -> np.ndarray:
    """Float mask over a denoised stream: 1 inside gate-open spans, 0 outside."""
    mask = np.zeros(length, dtype=np.float32)
    for start, end in segments:
        end = length if end is None else min(end, length)
        start = max(0, start)
        if end <= start:
            continue
        mask[start:end] = 1.0
        n = min(fade_samples, end - start)
        mask[start : start + n] = np.linspace(0.0, 1.0, n, dtype=np.float32)
        mask[end - n : end] = np.minimum(
            mask[end - n : end], np.linspace(1.0, 0.0, n, dtype=np.float32)
        )
    return mask
