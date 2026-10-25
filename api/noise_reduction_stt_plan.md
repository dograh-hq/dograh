# Noise Reduction for STT — Improvement Plan

Goal: the live agent's STT gets cleaner speech, so it makes fewer recognition errors,
reacts to fewer false barge-ins and misses fewer turns. Better audio *for people* is
not the goal: what counts is fewer STT errors and fewer turn-taking mistakes.

## 1. Where we are (from `evals/noise_reduction/` results)

| | Today (RNNoise, Phase 1) |
|---|---|
| Steady noise (fans, hum, typing, traffic, office) | Removed well (30–90 dB in pauses) |
| Speech level | **Drops**, by up to ~6.6 dB on a real noisy recording. Some speech frames are cut. |
| Clean calls | **Damaged**. PESQ 4.2 → 2.0. Applying the filter to a clean call can only hurt it. |
| Background voices (call centers, TV) | **Worse**, and false speech starts go up. RNNoise keeps every voice. |
| STT error rate | **Unknown**. Never measured with a real STT provider. |
| CPU | ~4% of a core per call, on the event loop. One worker saturates at ~25 calls. |

Two facts shape this plan:

1. **Modern STT is trained on noisy audio.** Deepgram, our main provider, often
   copes with noise better than with the artifacts a denoiser leaves behind (clipped
   word onsets, gaps, robotic tone). Removing noise and getting lower WER are not the
   same thing, so every change must be judged by WER, not by how it sounds.
2. **Most calls don't need it.** Running the filter on a clean call is all cost.
   It should turn on only when a call is actually noisy.

## 2. Step 0 — Measure STT accuracy first (blocks everything else)

Without this we are guessing. Build it before any tuning.

**Dataset (real calls, not synthetic):**
- Recordings already store the caller's track separately (`user_audio_wav` in
  `api/services/pipecat/event_handlers.py`). Take 200–500 consented,
  de-identified caller tracks, sorted into quiet, noisy and background-voices buckets.
- **Reference transcripts:** human-corrected for a core set of ~100. For the rest,
  use a strong offline model (e.g. Whisper large) as pseudo-ground truth.
- Add a synthetic set (clean speech plus office, street and babble noise at 0–20 dB
  over μ-law), reusing `evals/noise_reduction/benchmark.py`.

**Harness** (`evals/noise_reduction/stt_eval.py`, reusing `evals/stt/`):
- Stream each clip to **Deepgram** and every other STT provider we support: once raw,
  and once through each noise-reduction variant, all at 8 kHz in 20 ms chunks, the
  way live calls send it.
- Metrics:
  - **WER**, plus **WER on keywords/entities** (names, numbers, yes/no). Errors on
    these are what break agents.
  - **Missed-utterance rate**: words spoken but nothing transcribed.
  - **Hallucination rate**: text produced during noise-only stretches.
  - **First-word latency**: time from speech onset to first interim transcript.
  - **VAD false starts per minute** and **turns missed** (existing benchmark).

**Gate for any variant to ship:** it must not make WER worse on quiet calls
(±0.5 pt), and it must cut WER on noisy calls by at least 10% relative.

## 3. Quick wins (1–2 weeks), each checked against the Step 0 gate

### 3.1 Auto-enable only on noisy calls
- **Measure the line:** in the first ~2 s of a call, and every few seconds after,
  estimate the noise floor from frames RNNoise scores as non-speech. RNNoise already
  returns a per-frame speech probability that we currently ignore.
- **Switch on:** only when the estimated SNR drops below a threshold (start around
  15 dB, then tune with Step 0 data), using hysteresis so it doesn't flap.
- **Why:** clean calls stay untouched, which removes the largest measured damage.
- **Config:** a new value, `enabled: "auto"`, alongside `true`/`false`. Make it the
  recommended setting.

### 3.2 Protect speech: speech-aware wet/dry mix
- Blend denoised and original audio per frame, driven by RNNoise's speech probability:
  - speech probable: mostly original (e.g. 30% denoised);
  - pauses: mostly denoised (e.g. 95%).
- **Result:** noise drops in the gaps, where it causes hallucinations and false
  barge-ins, while words keep their natural level and onsets.
- This directly targets the 6.6 dB speech loss and the clipped onsets.
- **Implementation:** the original audio must be delayed 20 ms to line up with the
  engine output, and the blend must be smoothed (~10 ms ramps) to avoid clicks.
- **Expose it as `strength` (0–1) and tune the default with Step 0 data.**

### 3.3 `vad_only` mode
- **What:** VAD and turn detection get the denoised copy; STT gets the original audio.
- **Effect:** no risk to WER at all, while false barge-ins and missed turn ends
  improve wherever noise fools the VAD.
- **Implementation:** `DenoisedVADAnalyzer` wraps `SileroVADAnalyzer` (no pipecat change).
- **When to use:** the safe default where Step 0 shows denoising STT input doesn't pay off.

### 3.4 Make it cheap enough for every call
- Run the engine in a dedicated thread pool. The prototype cut event-loop lag p99
  from 63 ms to 3 ms at 25 calls.
- Build librnnoise with x86 runtime CPU detection (AVX2), expected to be several
  times faster. Build it for arm64 too, which today falls back to passthrough.
- Run one engine instance per call only while the call is noisy (3.1).

### 3.5 Tune the rest of the input chain for noisy lines
- Lower the false-start rate with VAD settings (`confidence`, `min_volume`) when the
  line is noisy, using the 3.1 estimate.
- Deepgram settings: keyterms from the workflow `dictionary`, endpointing tuned for
  noisy lines, and possibly a noise-robust model variant per workflow.
- These changes are free and can matter as much as the denoiser itself.

## 4. Better engines (2–6 weeks, run in parallel with section 3)

RNNoise can't remove voices and has a low quality ceiling. Benchmark these with the
Step 0 harness. pipecat already ships filters for the first two, so they plug into
our `ENGINES` registry plus `SafeAudioFilter` with little code:

| Engine | Why | Cost |
|---|---|---|
| **Krisp VIVA** (`KrispVivaFilter`) | Noise and **background-voice** removal, tuned for voice agents | Paid licence |
| **ai-coustics** (`AICFilter`) | Models aimed at STT and voice agents | Paid key |
| **DeepFilterNet 3** (ONNX, our own wrapper) | Best open-source quality, free | ~10× RNNoise CPU |

Pick per tier: a free tier (RNNoise auto plus speech-aware mix) and a premium tier
(Krisp/AIC) for call centers with background voices. Background voices can only be
fixed by one of these engines or by our own model
(`noise_reduction_custom_model_plan.md`).

## 5. Rollout

1. **Shadow mode on production calls.**
   - Today the recording comes after the filter, so it is denoised. Add a raw
     caller track so the raw and denoised audio can be compared on the same call.
   - Transcribe both offline and compare WER by noise bucket.
   - This needs no customer-facing change.
2. **A/B per workflow.** Compare these metrics with the filter on and off:
   - live metrics: "sorry, could you repeat" turns, bot interruptions per minute,
     empty or no-input turns;
   - STT confidence;
   - call completion and disposition.
3. **Default to `auto` with speech-aware mix** only once the gate holds on real calls.
4. **Observability:** log a per-call summary event with whether the filter switched
   on, the estimated SNR, the time spent denoising and the CPU used.

## 6. Order of work

| # | Item | Effort | Blocks |
|---|---|---|---|
| 1 | STT eval harness and real-call dataset (Step 0) | 1 week | everything |
| 2 | Thread-pool offload and AVX2/arm64 build | 2–3 days | wide enablement |
| 3 | Speech-aware wet/dry mix (`strength`) | 3–4 days | — |
| 4 | Auto-enable on noisy calls | 3–4 days | — |
| 5 | `vad_only` mode | 2 days | — |
| 6 | VAD and Deepgram tuning for noisy lines | 2–3 days | 4 |
| 7 | Krisp / AIC / DeepFilterNet benchmark and integration | 2–4 weeks | 1 |
| 8 | Shadow mode with a raw caller track, then A/B | 1 week plus run time | 1 |

Items 3–5 are each judged against the item 1 gate. Anything that doesn't lower WER
on noisy calls without raising it on clean ones doesn't ship.
