"""How much real speech does speech-mode denoising remove?

Speech regions come from the clean clip, so they are known exactly. Each clip
is mixed with noise, run through the same path as
``POST /api/v1/audio/denoise`` in speech mode (``gate_speech``), and the output
is compared with the clean original to separate three kinds of loss:

- gate misses: speech the VAD never let through (whole words dropped);
- cut edges: speech before the gate opened or after it closed;
- filter damage: speech the gate passed but RNNoise attenuated.

Only regions Silero itself scores as speech on the clean clip (confidence
>= 0.5) count as words, so clicks and breaths at -15 to -25 dB do not count as
lost speech. Gate variants are compared side by side, including how long the
gate stays open on noise alone.

Run from the project root:

    set -a && source api/.env.test && set +a
    python -m evals.noise_reduction.speech_loss
"""

import asyncio
import sys
from pathlib import Path

import numpy as np
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from api.services.pipecat.noise_reduction.file_denoise import gate_speech
from evals.noise_reduction.benchmark import (
    SPEECH_FILES,
    load_audio,
    make_noise,
    mulaw_roundtrip,
    rms,
    to_int16,
)

FRAME_SECS = 0.02
SPEECH_LEVEL = 0.05  # ~-26 dBFS, a typical phone level
LEAD_SECS = 1.0
# A clean frame counts as speech when it is within 25 dB of the clip's
# loudest 10% of frames; quieter frames are breaths and room tail.
SPEECH_FLOOR_DB = -25
WORD_GAP_SECS = 0.15
MIN_WORD_SECS = 0.1
HEAVY_ATTENUATION_DB = -10
REAL_WORD_CONFIDENCE = 0.5
NOISE_ONLY_SECS = 30

CONDITIONS = [("clean", None)] + [
    (kind, snr)
    for kind, snrs in (
        ("office", (0, 5, 10)),
        ("white", (5,)),
        ("keyboard", (5,)),
        ("babble", (10,)),
    )
    for snr in snrs
]

VARIANTS = {
    "default (sensitive VAD + 0.3 s hold + 0.3 speech floor)": {},
    "agent VAD, no hold, no floor": {
        "vad_params": VADParams(stop_secs=0.2),
        "post_roll_secs": 0,
        "speech_floor": 0,
    },
    "agent VAD + 0.3 s hold, no floor": {
        "vad_params": VADParams(stop_secs=0.2),
        "speech_floor": 0,
    },
    "sensitive VAD + 0.3 s hold, no floor": {"speech_floor": 0},
}


def frame_rms(x: np.ndarray, n: int) -> np.ndarray:
    frames = x[: len(x) // n * n].astype(np.float64).reshape(-1, n)
    return np.sqrt(np.mean(frames**2, axis=1))


def speech_frames(clean: np.ndarray, n: int) -> np.ndarray:
    level = frame_rms(clean, n)
    loud = np.percentile(level[level > 0], 90) if np.any(level > 0) else 0
    return level > loud * 10 ** (SPEECH_FLOOR_DB / 20)


def real_words(clean: np.ndarray, sample_rate: int) -> list[tuple[int, int]]:
    """Word regions (frame indices) that Silero scores as speech on clean audio."""
    n = int(sample_rate * FRAME_SECS)
    window = 512 if sample_rate == 16000 else 256
    vad = SileroVADAnalyzer(sample_rate=sample_rate)
    vad.set_sample_rate(sample_rate)
    found = []
    for ws, we in words(speech_frames(clean, n)):
        seg = to_int16(clean[ws * n : we * n])
        confs = [
            float(np.max(vad.voice_confidence(seg[i : i + window].tobytes())))
            for i in range(0, len(seg) - window + 1, window)
        ]
        if confs and max(confs) >= REAL_WORD_CONFIDENCE:
            found.append((ws, we))
    return found


def words(mask: np.ndarray) -> list[tuple[int, int]]:
    """Group speech frames into word-like regions (frame indices)."""
    gap = int(WORD_GAP_SECS / FRAME_SECS)
    regions, start, last = [], None, None
    for i in np.flatnonzero(mask):
        if start is None:
            start = last = i
        elif i - last > gap:
            regions.append((start, last + 1))
            start = i
        last = i
    if start is not None:
        regions.append((start, last + 1))
    return [r for r in regions if (r[1] - r[0]) * FRAME_SECS >= MIN_WORD_SECS]


async def evaluate(sample_rate: int, gate_options: dict) -> list[dict]:
    n = int(sample_rate * FRAME_SECS)
    lead = np.zeros(int(LEAD_SECS * sample_rate), np.float32)
    clips = []
    for path in SPEECH_FILES:
        speech = load_audio(path, sample_rate)
        speech = speech / rms(speech) * SPEECH_LEVEL
        clean = np.concatenate([lead, speech, lead])
        clips.append((Path(path).name, clean, real_words(clean, sample_rate)))

    rows = []
    for kind, snr in CONDITIONS:
        totals = dict(
            speech_frames=0,
            gated_frames=0,
            words=0,
            words_dropped=0,
            onset_ms=[],
            tail_ms=[],
            level_db=[],
        )
        per_clip = []
        for c_index, (name, clean, clip_words) in enumerate(clips):
            noisy = clean.copy()
            if kind != "clean":
                noise = make_noise(kind, sample_rate, len(clean), seed=c_index)
                frames = speech_frames(clean, n).repeat(n)
                active = rms(clean[: len(frames)][frames])
                noisy = clean + noise * active * 10 ** (-snr / 20)
            if sample_rate == 8000:
                noisy = mulaw_roundtrip(noisy)
            out, spans = await gate_speech(to_int16(noisy), sample_rate, **gate_options)
            out = out.astype(np.float32) / 32768

            is_speech = np.zeros(len(speech_frames(clean, n)), bool)
            for ws, we in clip_words:
                is_speech[ws:we] = speech_frames(clean, n)[ws:we]
            gate = np.zeros(len(is_speech), bool)
            for start, end in spans:
                gate[start // n : -(-end // n)] = True
            gate = gate[: len(is_speech)]

            dropped = 0
            for ws, we in clip_words:
                covered = gate[ws:we].mean()
                if covered < 0.5:
                    dropped += 1
                    continue
                first_open = ws + int(np.argmax(gate[ws:we]))
                last_open = we - 1 - int(np.argmax(gate[ws:we][::-1]))
                totals["onset_ms"].append((first_open - ws) * FRAME_SECS * 1000)
                totals["tail_ms"].append((we - 1 - last_open) * FRAME_SECS * 1000)

            inside = is_speech & gate
            clean_level = frame_rms(clean, n)[: len(is_speech)]
            out_level = frame_rms(out, n)[: len(is_speech)]
            level_db = 20 * np.log10((out_level[inside] + 1e-9) / (clean_level[inside] + 1e-9))
            totals["speech_frames"] += int(is_speech.sum())
            totals["gated_frames"] += int(inside.sum())
            totals["words"] += len(clip_words)
            totals["words_dropped"] += dropped
            totals["level_db"].extend(level_db.tolist())
            per_clip.append((name, dropped, len(clip_words)))

        level = np.array(totals["level_db"])
        rows.append(
            {
                "rate": sample_rate,
                "noise": kind,
                "snr": snr,
                "speech_passed_pct": 100 * totals["gated_frames"] / totals["speech_frames"],
                "words": totals["words"],
                "words_dropped": totals["words_dropped"],
                "onset_cut_ms_p50": float(np.median(totals["onset_ms"])),
                "onset_cut_ms_p90": float(np.percentile(totals["onset_ms"], 90)),
                "tail_cut_ms_p90": float(np.percentile(totals["tail_ms"], 90)),
                "level_change_db_p50": float(np.median(level)),
                "heavily_attenuated_pct": float(100 * np.mean(level < HEAVY_ATTENUATION_DB)),
                "per_clip": per_clip,
            }
        )
        print(f"  {sample_rate} Hz {kind} {snr} done", file=sys.stderr)
    return rows


async def false_open(sample_rate: int, gate_options: dict) -> dict[str, float]:
    """Seconds per minute the gate opens on noise alone, at speech level."""
    result = {}
    for kind in ("office", "white", "keyboard", "babble"):
        noise = make_noise(kind, sample_rate, NOISE_ONLY_SECS * sample_rate, 7)
        noise = noise * SPEECH_LEVEL
        if sample_rate == 8000:
            noise = mulaw_roundtrip(noise)
        _, spans = await gate_speech(to_int16(noise), sample_rate, **gate_options)
        open_secs = sum(end - start for start, end in spans) / sample_rate
        result[kind] = open_secs / NOISE_ONLY_SECS * 60
    return result


def report(rows: list[dict]) -> str:
    lines = [
        "| Rate | Noise | SNR dB | Speech passed by gate | Words dropped | "
        "Onset cut p50 / p90 ms | Tail cut p90 ms | Speech level change p50 | "
        f"Speech frames < {HEAVY_ATTENUATION_DB} dB |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['rate']} | {r['noise']} | {'—' if r['snr'] is None else r['snr']} | "
            f"{r['speech_passed_pct']:.1f}% | {r['words_dropped']}/{r['words']} | "
            f"{r['onset_cut_ms_p50']:.0f} / {r['onset_cut_ms_p90']:.0f} | "
            f"{r['tail_cut_ms_p90']:.0f} | {r['level_change_db_p50']:+.1f} dB | "
            f"{r['heavily_attenuated_pct']:.1f}% |"
        )
    lines += ["", "Dropped words per clip (dropped/total), worst conditions:"]
    for r in sorted(rows, key=lambda r: -r["words_dropped"])[:4]:
        detail = ", ".join(f"{name} {d}/{t}" for name, d, t in r["per_clip"] if d)
        lines.append(f"- {r['rate']} Hz {r['noise']} {r['snr']}: {detail or 'none'}")
    return "\n".join(lines)


async def main():
    rate = int(sys.argv[1]) if len(sys.argv) > 1 else 16000
    summary = []
    for name, options in VARIANTS.items():
        rows = await evaluate(rate, options)
        opens = await false_open(rate, options)
        print(f"\n### {name} ({rate} Hz)\n")
        print(report(rows))
        print(
            "\nGate open on noise alone (s per min): "
            + ", ".join(f"{k} {v:.1f}" for k, v in opens.items())
        )
        words_total = sum(r["words"] for r in rows)
        dropped = sum(r["words_dropped"] for r in rows)
        summary.append((name, rows, opens, dropped, words_total))

    print(f"\n## Summary ({rate} Hz, all conditions)\n")
    print(
        "| Gate | Real words dropped | Dropped at 0 dB office | Speech passed (clean) "
        "| Tail cut p90 (clean) | Open on noise alone (s/min, office/white/keyboard/babble) |"
    )
    print("|---|---|---|---|---|---|")
    for name, rows, opens, dropped, total in summary:
        clean = next(r for r in rows if r["noise"] == "clean")
        office0 = next(r for r in rows if r["noise"] == "office" and r["snr"] == 0)
        print(
            f"| {name} | {dropped}/{total} | {office0['words_dropped']}/{office0['words']} "
            f"| {clean['speech_passed_pct']:.1f}% | {clean['tail_cut_ms_p90']:.0f} ms | "
            + " / ".join(f"{v:.1f}" for v in opens.values())
            + " |"
        )


if __name__ == "__main__":
    asyncio.run(main())
