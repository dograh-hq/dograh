"""Speed, latency, load and quality benchmark for caller-audio noise reduction.

Run from the project root with the API venv active:

    set -a && source api/.env.test && set +a
    python -m evals.noise_reduction.benchmark            # everything
    python -m evals.noise_reduction.benchmark --only speed,load

Quality metrics need ``pip install pesq pystoi`` (not API dependencies).
Results print as Markdown and are written to ``evals/noise_reduction/results/``.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

from api.services.pipecat.noise_reduction import build_audio_in_filter
from api.services.pipecat.noise_reduction.rnnoise import (
    RNNoiseAudioFilter,
    load_rnnoise_library,
)

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"

SPEECH_FILES = [
    "evals/stt/audio/billing_department.m4a",
    "evals/stt/audio/nope.m4a",
    "evals/stt/audio/not_so_sure.m4a",
    "evals/stt/audio/yes.m4a",
    "evals/stt/audio/multi_speaker.m4a",
    "evals/stt/audio/vad.m4a",
    "pipecat/scripts/provider-watch/assets/speech-16k.wav",
    "pipecat/evals/release/assets/capital_question.wav",
]
OFFICE_NOISE = "api/assets/office-ambience-16000-mono.wav"
HOLD_MUSIC = "pipecat/examples/flows/python/assets/hold_music/hold_music.wav"
DTMF_DIR = "pipecat/src/pipecat/audio/dtmf"

FRAME_MS = 20
GAP_SECS = 0.6
NOISE_ONLY_SECS = 30.0


# --- audio helpers ---------------------------------------------------------


def load_audio(path: str, sample_rate: int) -> np.ndarray:
    """Decode any file to mono float32 in [-1, 1] at ``sample_rate``."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(ROOT / path), "-ac", "1"]
        + ["-ar", str(sample_rate), "-f", "s16le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768


def mulaw_roundtrip(x: np.ndarray) -> np.ndarray:
    """Simulate a G.711 u-law phone line (8-bit companded quantisation)."""
    mu = 255.0
    y = np.sign(x) * np.log1p(mu * np.abs(np.clip(x, -1, 1))) / np.log1p(mu)
    y = np.round((y + 1) * 127.5) / 127.5 - 1
    return np.sign(y) * np.expm1(np.abs(y) * np.log1p(mu)) / mu


def to_int16(x: np.ndarray) -> np.ndarray:
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)) + 1e-12))


def build_speech(sample_rate: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Concatenate the speech clips with gaps; return audio and clip regions."""
    gap = np.zeros(int(GAP_SECS * sample_rate), dtype=np.float32)
    parts, regions, pos = [gap], [], len(gap)
    for path in SPEECH_FILES:
        clip = load_audio(path, sample_rate)
        clip = clip / max(rms(clip), 1e-6) * 0.08  # same level for every clip
        parts += [clip, gap]
        regions.append((pos, pos + len(clip)))
        pos += len(clip) + len(gap)
    return np.concatenate(parts), regions


def active_speech_power(speech: np.ndarray, sample_rate: int) -> float:
    """Mean power over 20 ms windows that are not near-silent."""
    n = sample_rate // 50
    frames = speech[: len(speech) // n * n].reshape(-1, n)
    power = np.mean(frames.astype(np.float64) ** 2, axis=1)
    return float(np.mean(power[power > power.max() * 1e-3]))


def make_noise(kind: str, sample_rate: int, length: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(length) / sample_rate
    if kind == "white":
        x = rng.standard_normal(length)
    elif kind == "pink":
        spectrum = np.fft.rfft(rng.standard_normal(length))
        freqs = np.fft.rfftfreq(length, 1 / sample_rate)
        spectrum[1:] /= np.sqrt(freqs[1:])
        x = np.fft.irfft(spectrum, length)
    elif kind == "hum":
        x = sum(np.sin(2 * np.pi * 50 * k * t) / k for k in range(1, 8))
        x = x + 0.05 * rng.standard_normal(length)
    elif kind == "keyboard":
        x = 0.02 * rng.standard_normal(length)
        click = np.exp(-np.arange(int(0.015 * sample_rate)) / (0.002 * sample_rate))
        for start in rng.integers(0, length - len(click), size=int(length / sample_rate * 6)):
            x[start : start + len(click)] += click * rng.standard_normal(len(click)) * 3
    elif kind in ("office", "music"):
        src = load_audio(OFFICE_NOISE if kind == "office" else HOLD_MUSIC, sample_rate)
        offset = int(rng.integers(0, len(src) - length)) if len(src) > length else 0
        x = np.resize(src[offset:], length)
    elif kind == "babble":
        # Four overlapping talkers from the speech clips, time-shifted.
        speech = np.concatenate([load_audio(p, sample_rate) for p in SPEECH_FILES])
        x = sum(np.roll(np.resize(speech, length), int(rng.integers(0, length))) for _ in range(4))
    else:
        raise ValueError(kind)
    return x.astype(np.float32) / max(rms(x), 1e-9)


def si_sdr(reference: np.ndarray, estimate: np.ndarray) -> float:
    reference = reference.astype(np.float64)
    estimate = estimate.astype(np.float64)
    scale = np.dot(estimate, reference) / (np.dot(reference, reference) + 1e-12)
    target = scale * reference
    return float(10 * np.log10(np.sum(target**2) / (np.sum((estimate - target) ** 2) + 1e-12)))


# --- running filters -------------------------------------------------------


async def run_filter(filt, audio: np.ndarray, sample_rate: int, frame_ms: int = FRAME_MS):
    """Feed ``audio`` in transport-sized frames; return output and per-frame ns."""
    chunk = sample_rate * frame_ms // 1000
    pcm = to_int16(audio) if audio.dtype != np.int16 else audio
    out, timings = [], []
    for i in range(0, len(pcm) - chunk + 1, chunk):
        frame = pcm[i : i + chunk].tobytes()
        started = time.perf_counter_ns()
        out.append(await filt.filter(frame))
        timings.append(time.perf_counter_ns() - started)
    result = np.frombuffer(b"".join(out), dtype=np.int16).astype(np.float32) / 32768
    return result, np.array(timings)


async def denoise(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    filt = RNNoiseAudioFilter()
    await filt.start(sample_rate)
    out, _ = await run_filter(filt, audio, sample_rate)
    await filt.stop()
    return out


def align(output: np.ndarray, delay: int, length: int) -> np.ndarray:
    """Shift output back by the engine delay and pad to ``length``."""
    shifted = output[delay:]
    return np.pad(shifted, (0, max(0, length - len(shifted))))[:length]


def measure_delay(reference: np.ndarray, output: np.ndarray, sample_rate: int) -> int:
    """Samples by which ``output`` lags ``reference`` (cross-correlation peak)."""
    n = min(len(reference), len(output), sample_rate * 10)
    max_lag = sample_rate // 20
    ref = reference[:n]
    scores = [np.dot(ref[: n - lag], output[lag:n]) for lag in range(max_lag)]
    return int(np.argmax(scores))


# --- VAD -------------------------------------------------------------------


class VadCounter:
    """Production VAD settings (stop_secs=0.2) driven synchronously."""

    def __init__(self, sample_rate: int):
        self.vad = SileroVADAnalyzer(sample_rate=sample_rate, params=VADParams(stop_secs=0.2))
        self.vad.set_sample_rate(sample_rate)
        self.sample_rate = sample_rate

    def speaking_mask(self, audio: np.ndarray) -> np.ndarray:
        """Per-20 ms frame: is the VAD in the SPEAKING state."""
        chunk = self.sample_rate // 50
        pcm = to_int16(audio)
        mask = []
        for i in range(0, len(pcm) - chunk + 1, chunk):
            state = self.vad._run_analyzer(pcm[i : i + chunk].tobytes())
            mask.append(state == VADState.SPEAKING)
        return np.array(mask)


def speech_starts(mask: np.ndarray) -> int:
    return int(np.sum(mask[1:] & ~mask[:-1]) + (1 if len(mask) and mask[0] else 0))


# --- benchmarks ------------------------------------------------------------


def percentiles_ms(ns: np.ndarray) -> dict:
    ms = ns / 1e6
    return {
        "p50": float(np.percentile(ms, 50)),
        "p95": float(np.percentile(ms, 95)),
        "p99": float(np.percentile(ms, 99)),
        "max": float(ms.max()),
        "mean": float(ms.mean()),
    }


async def bench_startup() -> dict:
    started = time.perf_counter()
    load_rnnoise_library()
    load_ms = (time.perf_counter() - started) * 1e3
    starts = []
    for _ in range(50):
        filt = RNNoiseAudioFilter()
        started = time.perf_counter()
        await filt.start(8000)
        starts.append((time.perf_counter() - started) * 1e3)
        await filt.stop()
    return {"library_load_ms": load_ms, "per_call_start_ms": float(np.median(starts))}


async def bench_speed() -> list[dict]:
    """Per-frame cost on 60 s of noisy speech, ours vs pipecat's RNNoiseFilter."""
    rows = []
    engines = {"dograh RNNoiseAudioFilter": RNNoiseAudioFilter}
    try:
        from pipecat.audio.filters.rnnoise_filter import RNNoise, RNNoiseFilter

        if RNNoise is not None:
            engines["pipecat RNNoiseFilter (pyrnnoise)"] = RNNoiseFilter
    except ImportError:
        pass

    for sample_rate in (8000, 16000):
        speech, _ = build_speech(sample_rate)
        speech = np.resize(speech, sample_rate * 60)
        audio = speech + 0.02 * make_noise("office", sample_rate, len(speech), 1)
        for frame_ms in (10, 20, 40):
            for name, cls in engines.items():
                if frame_ms != FRAME_MS and not name.startswith("dograh"):
                    continue
                filt = cls()
                await filt.start(sample_rate)
                await run_filter(filt, audio[: sample_rate * 2], sample_rate, frame_ms)  # warm-up
                _, timings = await run_filter(filt, audio, sample_rate, frame_ms)
                await filt.stop()
                stats = percentiles_ms(timings)
                cpu_pct = timings.sum() / 1e9 / (len(audio) / sample_rate) * 100
                rows.append(
                    {
                        "engine": name,
                        "sample_rate": sample_rate,
                        "frame_ms": frame_ms,
                        **stats,
                        "cpu_pct_of_one_core": cpu_pct,
                    }
                )
    return rows


async def bench_latency() -> list[dict]:
    """Added audio delay, measured on clean speech by cross-correlation."""
    rows = []
    for sample_rate in (8000, 16000, 24000, 48000):
        speech, _ = build_speech(sample_rate)
        out = await denoise(speech, sample_rate)
        delay = measure_delay(speech, out, sample_rate)

        filt = RNNoiseAudioFilter()
        await filt.start(sample_rate)
        chunk = sample_rate // 50
        frames_until_output = 0
        while not await filt.filter(to_int16(speech[:chunk]).tobytes()):
            frames_until_output += 1
        await filt.stop()
        rows.append(
            {
                "sample_rate": sample_rate,
                "added_delay_ms": delay / sample_rate * 1000,
                "empty_frames_at_start": frames_until_output,
            }
        )
    return rows


async def bench_load(call_counts=(1, 25, 50, 100, 150, 200), seconds: float = 10.0) -> list[dict]:
    """N simultaneous 8 kHz calls on one event loop, paced in real time.

    Each call goes through the production path (factory + SafeAudioFilter).
    Event-loop lag is how late each call's 20 ms tick fires; if it grows, the
    worker cannot keep up.
    """
    sample_rate = 8000
    chunk = sample_rate // 50
    speech, _ = build_speech(sample_rate)
    audio = to_int16(speech + 0.02 * make_noise("office", sample_rate, len(speech), 2))
    rows = []
    for calls in call_counts:
        filters = [build_audio_in_filter({"enabled": True}) for _ in range(calls)]
        for filt in filters:
            await filt.start(sample_rate)
        lags, frame_ns = [], []
        frames = int(seconds * 50)

        async def call(index: int, filt, frames=frames, lags=lags, frame_ns=frame_ns):
            offset = (index * 997) % (len(audio) - chunk * frames - 1)
            loop = asyncio.get_running_loop()
            next_tick = loop.time() + (index % 50) / 1000  # stagger like real calls
            for f in range(frames):
                await asyncio.sleep(max(0, next_tick - loop.time()))
                lags.append(loop.time() - next_tick)
                start = offset + f * chunk
                t0 = time.perf_counter_ns()
                await filt.filter(audio[start : start + chunk].tobytes())
                frame_ns.append(time.perf_counter_ns() - t0)
                next_tick += 0.02

        cpu0, wall0 = time.process_time(), time.perf_counter()
        await asyncio.gather(*(call(i, f) for i, f in enumerate(filters)))
        cpu, wall = time.process_time() - cpu0, time.perf_counter() - wall0
        tripped = sum(1 for f in filters if not f.active)
        for filt in filters:
            await filt.stop()
        lag_ms = np.array(lags) * 1000
        rows.append(
            {
                "calls": calls,
                "process_cpu_pct": cpu / wall * 100,
                "cpu_pct_per_call": cpu / wall * 100 / calls,
                "frame_p99_ms": float(np.percentile(np.array(frame_ns) / 1e6, 99)),
                "loop_lag_p50_ms": float(np.percentile(lag_ms, 50)),
                "loop_lag_p99_ms": float(np.percentile(lag_ms, 99)),
                "filters_tripped": tripped,
                "realtime_ratio": wall / seconds,
            }
        )
    return rows


async def bench_quality(snrs=(0, 5, 10, 20)) -> list[dict]:
    """Noisy speech over a simulated u-law phone line at 8 kHz, plus 16 kHz WebRTC."""
    from pesq import pesq
    from pystoi import stoi

    rows = []
    for sample_rate, phone_line in ((8000, True), (16000, False)):
        clean, regions = build_speech(sample_rate)
        speech_power = active_speech_power(clean, sample_rate)
        clean_line = mulaw_roundtrip(clean) if phone_line else clean
        mode = "nb" if sample_rate == 8000 else "wb"
        vad = VadCounter(sample_rate)

        clean_out = await denoise(clean_line, sample_rate)
        delay = measure_delay(clean_line, clean_out, sample_rate)
        clean_out = align(clean_out, delay, len(clean))
        rows.append(
            {
                "sample_rate": sample_rate,
                "noise": "none (clean speech)",
                "snr_db": None,
                "pesq_in": pesq(sample_rate, clean, clean_line, mode),
                "pesq_out": pesq(sample_rate, clean, clean_out, mode),
                "stoi_in": stoi(clean, clean_line, sample_rate),
                "stoi_out": stoi(clean, clean_out, sample_rate),
                "si_sdr_in": si_sdr(clean, clean_line),
                "si_sdr_out": si_sdr(clean, clean_out),
                "speech_level_change_db": 20 * np.log10(rms(clean_out) / rms(clean_line)),
                **vad_scores(vad, clean_line, clean_out, regions, sample_rate),
            }
        )

        for kind in ("white", "pink", "office", "babble", "keyboard", "hum", "music"):
            for snr in snrs:
                noise = make_noise(kind, sample_rate, len(clean), seed=snr)
                noise *= np.sqrt(speech_power / 10 ** (snr / 10))
                noisy = clean + noise
                noise_only = make_noise(kind, sample_rate, int(NOISE_ONLY_SECS * sample_rate), 99)
                noise_only *= np.sqrt(speech_power / 10 ** (snr / 10))
                if phone_line:
                    noisy, noise_only = mulaw_roundtrip(noisy), mulaw_roundtrip(noise_only)
                out = align(await denoise(noisy, sample_rate), delay, len(clean))
                noise_out = align(await denoise(noise_only, sample_rate), delay, len(noise_only))
                settled = sample_rate  # ignore the first second while state adapts
                vad_in = speech_starts(vad.speaking_mask(noise_only))
                vad_out = speech_starts(vad.speaking_mask(noise_out))
                rows.append(
                    {
                        "sample_rate": sample_rate,
                        "noise": kind,
                        "snr_db": snr,
                        "pesq_in": pesq(sample_rate, clean, noisy, mode),
                        "pesq_out": pesq(sample_rate, clean, out, mode),
                        "stoi_in": stoi(clean, noisy, sample_rate),
                        "stoi_out": stoi(clean, out, sample_rate),
                        "si_sdr_in": si_sdr(clean, noisy),
                        "si_sdr_out": si_sdr(clean, out),
                        "noise_reduction_db": 20
                        * np.log10(rms(noise_only[settled:]) / rms(noise_out[settled:])),
                        "false_starts_per_min_in": vad_in / NOISE_ONLY_SECS * 60,
                        "false_starts_per_min_out": vad_out / NOISE_ONLY_SECS * 60,
                        **vad_scores(vad, noisy, out, regions, sample_rate),
                    }
                )
                print(
                    f"  quality {sample_rate} Hz {kind:8s} {snr:>2} dB done",
                    file=sys.stderr,
                )
    return rows


def vad_scores(vad, before, after, regions, sample_rate) -> dict:
    """How many speech clips the VAD still catches, before and after denoising."""

    def detected(audio):
        mask = vad.speaking_mask(audio)
        frame = sample_rate // 50
        # Allow the VAD's start/stop hysteresis to spill 300 ms past the clip.
        spill = int(0.3 * 50)
        return sum(
            bool(mask[s // frame : min(len(mask), e // frame + spill)].any()) for s, e in regions
        )

    return {
        "clips": len(regions),
        "clips_detected_in": detected(before),
        "clips_detected_out": detected(after),
    }


async def bench_dtmf() -> list[dict]:
    """Do keypad tones survive? Tone power at the DTMF pair, before and after."""
    rows_freq = {"1": (697, 1209), "5": (770, 1336), "9": (852, 1477), "0": (941, 1336)}
    rows = []
    for key, freqs in rows_freq.items():
        tone = load_audio(f"{DTMF_DIR}/dtmf-{key}.wav", 8000)
        tone = np.concatenate([np.zeros(1600, np.float32), tone, np.zeros(1600, np.float32)])
        out = await denoise(tone, 8000)

        def pair_power(x, freqs=freqs):
            spectrum = np.abs(np.fft.rfft(x)) ** 2
            bins = np.fft.rfftfreq(len(x), 1 / 8000)
            return sum(spectrum[np.argmin(np.abs(bins - f))] for f in freqs)

        n = min(len(tone), len(out))
        rows.append(
            {
                "key": key,
                "tone_level_change_db": 10 * np.log10(pair_power(out[:n]) / pair_power(tone[:n])),
            }
        )
    return rows


# --- reporting -------------------------------------------------------------


def table(rows: list[dict], columns: list[tuple[str, str, str]]) -> str:
    header = "| " + " | ".join(title for _, title, _ in columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, sep]
    for row in rows:
        cells = []
        for key, _, fmt in columns:
            value = row.get(key)
            cells.append("—" if value is None else format(value, fmt))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def report(results: dict) -> str:
    out = [f"# Noise reduction benchmark — {results['when']}", "", results["machine"], ""]
    if "startup" in results:
        s = results["startup"]
        out += [
            "## Startup",
            "",
            f"- Library load (once per worker process): {s['library_load_ms']:.2f} ms",
            f"- Per-call filter start: {s['per_call_start_ms']:.3f} ms",
            "",
        ]
    if "speed" in results:
        out += [
            "## Processing time per frame (60 s of noisy speech)",
            "",
            table(
                results["speed"],
                [
                    ("engine", "Engine", "s"),
                    ("sample_rate", "Rate (Hz)", "d"),
                    ("frame_ms", "Frame (ms)", "d"),
                    ("p50", "p50 ms", ".3f"),
                    ("p95", "p95 ms", ".3f"),
                    ("p99", "p99 ms", ".3f"),
                    ("max", "max ms", ".3f"),
                    ("cpu_pct_of_one_core", "CPU % of 1 core per call", ".2f"),
                ],
            ),
            "",
        ]
    if "latency" in results:
        out += [
            "## Added audio delay",
            "",
            table(
                results["latency"],
                [
                    ("sample_rate", "Rate (Hz)", "d"),
                    ("added_delay_ms", "Delay (ms)", ".2f"),
                    ("empty_frames_at_start", "Empty 20 ms frames at call start", "d"),
                ],
            ),
            "",
        ]
    if "load" in results:
        out += [
            "## Concurrent calls on one event loop (8 kHz, real-time pacing)",
            "",
            table(
                results["load"],
                [
                    ("calls", "Calls", "d"),
                    ("process_cpu_pct", "Process CPU %", ".1f"),
                    ("cpu_pct_per_call", "CPU % per call", ".2f"),
                    ("frame_p99_ms", "Frame p99 ms", ".3f"),
                    ("loop_lag_p50_ms", "Loop lag p50 ms", ".2f"),
                    ("loop_lag_p99_ms", "Loop lag p99 ms", ".2f"),
                    ("realtime_ratio", "Wall / audio time", ".2f"),
                    ("filters_tripped", "Breakers tripped", "d"),
                ],
            ),
            "",
        ]
    if "quality" in results:
        out += [
            "## Quality",
            "",
            "8 kHz rows go through a simulated u-law phone line. PESQ: 1–4.5, higher is better"
            " (narrowband at 8 kHz, wideband at 16 kHz). STOI: intelligibility 0–1. False starts:"
            " Silero VAD (production settings) deciding someone started talking during 30 s of"
            " noise only. Clips: of the speech clips, how many the VAD detects.",
            "",
            table(
                results["quality"],
                [
                    ("sample_rate", "Rate", "d"),
                    ("noise", "Noise", "s"),
                    ("snr_db", "SNR dB", "d"),
                    ("pesq_in", "PESQ in", ".2f"),
                    ("pesq_out", "PESQ out", ".2f"),
                    ("stoi_in", "STOI in", ".3f"),
                    ("stoi_out", "STOI out", ".3f"),
                    ("si_sdr_in", "SI-SDR in", ".1f"),
                    ("si_sdr_out", "SI-SDR out", ".1f"),
                    ("noise_reduction_db", "Noise removed dB", ".1f"),
                    ("false_starts_per_min_in", "False starts/min in", ".1f"),
                    ("false_starts_per_min_out", "False starts/min out", ".1f"),
                    ("clips_detected_in", "Clips found in", "d"),
                    ("clips_detected_out", "Clips found out", "d"),
                ],
            ),
            "",
        ]
    if "dtmf" in results:
        out += [
            "## DTMF tones (8 kHz)",
            "",
            table(
                results["dtmf"],
                [("key", "Key", "s"), ("tone_level_change_db", "Tone level change dB", ".1f")],
            ),
            "",
        ]
    return "\n".join(out)


def machine() -> str:
    try:
        cpu = next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        )
    except (OSError, StopIteration):
        cpu = "unknown CPU"
    import os

    return f"Machine: {cpu}, {os.cpu_count()} vCPU, Python {sys.version.split()[0]}"


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default="startup,speed,latency,load,quality,dtmf",
        help="comma-separated sections to run",
    )
    args = parser.parse_args()
    sections = set(args.only.split(","))

    results = {"when": datetime.now().strftime("%Y-%m-%d %H:%M"), "machine": machine()}
    for name, fn in (
        ("startup", bench_startup),
        ("speed", bench_speed),
        ("latency", bench_latency),
        ("load", bench_load),
        ("quality", bench_quality),
        ("dtmf", bench_dtmf),
    ):
        if name in sections:
            print(f"running {name}...", file=sys.stderr)
            started = time.perf_counter()
            results[name] = await fn()
            print(f"  {name} took {time.perf_counter() - started:.1f} s", file=sys.stderr)

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / f"{stamp}.json").write_text(json.dumps(results, indent=2, default=float))
    markdown = report(results)
    (RESULTS_DIR / f"{stamp}.md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    asyncio.run(main())
