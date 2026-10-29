# Noise Reduction Benchmark

Measures the caller-audio noise filter (`api/services/pipecat/noise_reduction/`):

| Section | What it measures |
|---|---|
| `startup` | Library load (once per worker) and per-call filter start time |
| `speed` | Per-frame processing time (p50/p95/p99/max) and CPU share per call, for 10/20/40 ms frames at 8 and 16 kHz, against pipecat's `RNNoiseFilter` if `pyrnnoise` is installed |
| `latency` | Delay the filter adds to the caller's audio, measured by cross-correlation |
| `load` | 1–200 simultaneous calls on one event loop at real-time pace: CPU, event-loop lag, whether any call's breaker trips |
| `quality` | Real speech mixed with white, pink, office, babble, keyboard, hum and music noise at 0/5/10/20 dB SNR. 8 kHz runs go through a simulated u-law phone line. Reports PESQ, STOI, SI-SDR, noise removed, and Silero VAD false speech starts on noise alone (the false-interruption rate) |
| `dtmf` | How much keypad tones are attenuated |

Inputs come from files already in the repo: the speech clips in `evals/stt/audio/` and
pipecat's assets, `api/assets/office-ambience-16000-mono.wav`, pipecat's hold music
and DTMF tones.

## Run

From the project root, with the API venv active:

```bash
pip install pesq pystoi          # quality metrics only; not API dependencies
set -a && source api/.env.test && set +a
python -m evals.noise_reduction.benchmark                    # everything (~5 min)
python -m evals.noise_reduction.benchmark --only speed,latency
```

Requires `ffmpeg`. Results print as Markdown and are saved to `results/<timestamp>.{md,json}`.

The `load` section runs every call on one asyncio loop, as one API worker does. Its
"Calls" ceiling is per worker process, so multiply by `FASTAPI_WORKERS` for a host.
