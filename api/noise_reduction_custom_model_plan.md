# Dograh Denoise — Building a Krisp-class Noise & Background-Voice Remover

Companion to `noise_reduction_plan.md`. That plan wires noise reduction into the
pipeline with off-the-shelf engines. This one covers building **our own model**, so
Dograh owns the quality, cost and licensing instead of paying Krisp per minute.

---

## 1. What "like Krisp" means

Krisp sells three capabilities. We target them in this order:

| # | Capability | What it removes | Difficulty |
|---|---|---|---|
| 1 | **Noise Cancellation (NC)** | Non-speech sounds: traffic, fans, keyboard, dogs, music, street | Medium. A well-studied problem with good open-source baselines. |
| 2 | **Background Voice Cancellation (BVC)** | *Other people talking* near the caller (call-center babble, TV, family) while keeping the main speaker | Hard. This is Krisp's real moat. |
| 3 | **Voice-agent tuning** | Output optimized for STT and VAD rather than human ears | Medium. This is our differentiator, because we can train directly for transcription error rate (WER) and false barge-ins. |

Hard requirements, because this runs inside live calls:
- **Streaming and causal.** No look-ahead beyond one frame.
- **Algorithmic latency ≤ 20 ms.**
- **8 kHz (PSTN) and 16 kHz (WebRTC)** input.
- **CPU only**, under 5% of one core per call, so that a worker handling 50+ calls
  doesn't need a GPU.
- **Must not hurt STT.** WER on clean audio must not get worse.

---

## 2. Strategy: don't start from zero

Training a state-of-the-art model from scratch is months of work. Instead, start
from a permissively licensed open model, then specialize it for telephony and voice
agents, where Krisp is generic.

| Baseline | Licence | Size / cost | Notes |
|---|---|---|---|
| **DeepFilterNet 3** | MIT / Apache-2.0 | ~2M params, 48 kHz, real-time on 1 core | Best open quality. Its architecture (ERB gains + deep filtering) is the template for our model. |
| **GTCRN** | MIT | ~50K params, 16 kHz, very cheap | Ultra-light. Good fallback for high-density workers. |
| **DTLN** | MIT | ~1M params, 16 kHz | Simple, stateful, easy to export to ONNX. |
| RNNoise | BSD | Tiny | Already integrated (plan #1). This is the floor we must beat. |

Check each repo's licence for both the **code** and the **pretrained weights**
before shipping, since the two can differ.

**Phase A** (weeks 1–3): benchmark the pretrained DeepFilterNet3 and GTCRN as
pipeline engines on our own call audio. If one is already good enough, ship it while
our own model trains.

---

## 3. Model design (Dograh Denoise v1)

- **Front end:** STFT at 16 kHz, 20 ms window, 10 ms hop, sqrt-Hann window. For
  8 kHz calls, upsample to 16 kHz in the wrapper so one model serves both rates.
- **Architecture:** DeepFilterNet-style two-stage design.
  1. ERB-band encoder → causal GRU → per-band gain mask (removes the spectral
     envelope of the noise).
  2. Deep-filter stage on the low bands (0–4 kHz) using complex multi-frame filter
     taps (recovers speech harmonics, which matter most for STT).
- **Size target:** 1–2M params, **< 1 ms per 10 ms frame** on one x86 core after
  INT8 quantization.
- **Streaming:** all recurrent and conv states are explicit model inputs and
  outputs, so the runtime keeps per-call state and processes frame by frame.
- **BVC head (v2):** an additional input of a **speaker embedding** that the model
  builds from the call itself (see section 5).

---

## 4. Training data (the part that decides quality)

| Type | Sources (check licences) | Telephony-specific processing |
|---|---|---|
| Clean speech | DNS Challenge clean set, LibriTTS-R, VCTK, Common Voice (filtered for quality), multilingual for our markets (Hindi, Spanish, etc.) | — |
| Noise | DNS Challenge noise, MUSAN, DEMAND, FSD50K, plus **recorded call-center / street / vehicle noise** of our own | — |
| Room impulse responses | DNS RIRs, simulated rooms (pyroomacoustics) | Keep mild early reflections in the *target*, so the model doesn't sound "dry" or robotic. |
| Interfering speech (for BVC) | Other speakers from the clean set, made far-field: reverbed, lower level, lowpassed | — |
| **Real call audio** | Our own recordings, **with customer consent and per the data agreement**, de-identified | Used only for eval and fine-tuning, never shipped. |

On-the-fly augmentation, so each epoch sees new mixtures:
- SNR from −5 to +30 dB
- Gain and clipping
- **Codec simulation:** G.711 μ-law/A-law, AMR-NB, Opus at low bitrate
- **8 kHz band-limiting and resampling**
- Packet loss and jitter concealment artefacts
- Carrier AGC and comfort noise

This telephony realism is what generic models miss. It is where we can beat Krisp on
phone calls specifically.

---

## 5. Background Voice Cancellation approach

There is no enrollment on a phone call, so the model must decide who the "main"
speaker is by itself:

1. **Implicit (v2a):** train the model to keep the **near-field, dominant** speaker
   and suppress far-field, reverberant, quieter voices. Do this by mixing the target
   (close-mic) with interferers (reverbed, −5 to −20 dB below the target). This needs
   no extra runtime cost.
2. **Self-enrolled (v2b):** after the first few seconds of confident caller speech
   (VAD + RNNoise speech probability), compute a speaker embedding (small ECAPA-TDNN,
   ~1M params, ONNX). Feed it to the model as conditioning
   (personalized-DNS style). Refresh it slowly during the call. Until the embedding
   is ready, fall back to v2a.

Guardrail: if the embedding confidence is low (for example, the phone is handed to
someone else), drop conditioning rather than suppress the new legitimate speaker.

---

## 6. Loss functions (optimize for agents, not just ears)

- **Base:** multi-resolution STFT loss + SI-SDR.
- **Over-suppression penalty:** asymmetric loss that penalizes removing speech more
  than leaving residual noise. Clipped word onsets are worse for STT than faint noise.
- **ASR-aware feature loss:** match frozen speech-encoder features (e.g. a
  wav2vec2/Whisper encoder) of the enhanced audio to those of the clean audio. This
  directly targets the WER goal and is not something Krisp's generic NC optimizes for.
- **VAD-aware term (optional):** penalize noise-only segments that a frozen Silero
  VAD still scores as speech. This targets false barge-ins.

---

## 7. Evaluation

Every candidate model goes through the same harness (`evals/noise_reduction/`,
shared with plan #1):

| Metric | Measures | Gate (vs RNNoise and Krisp if we can license a test build) |
|---|---|---|
| DNSMOS P.835 (SIG / BAK / OVRL) | Perceived quality | BAK ≥ Krisp − 0.1, SIG must not degrade |
| PESQ, STOI, SI-SDR | Objective quality | Beat RNNoise everywhere |
| **WER** (via `evals/stt/benchmark.py`) per STT provider | Agent accuracy | No regression on clean audio, ≥ 15% relative improvement at 0–5 dB SNR |
| **VAD false-trigger rate** on noise-only and babble clips | False barge-ins | ≥ 50% lower than raw |
| BVC: interferer suppression (dB), target retention | Background voices | Interferer ≥ 15 dB down, target SI-SDR loss < 1 dB |
| RTF and p99 ms per frame on the production CPU type | Cost | < 1 ms per 10 ms frame |
| **Blind listening test** on real calls (internal raters, MUSHRA-style) | Real-world quality | Preferred over RNNoise in more than 70% of trials |

The **real-call test set** (a few hundred consented, de-identified snippets across
markets and noise types) is the most important asset. Build it in week 1.

---

## 8. Inference and integration

```
ml/denoise/                          # training repo area (own requirements, not in API image)
├── data/       # manifests, mixing and augmentation
├── models/     # DograhDenoise, ECAPA embedder
├── train.py, eval.py, export_onnx.py, quantize.py
└── configs/

api/services/audio_processing/noise_reduction/engines/dograh.py   # runtime engine
```

- **Export:** ONNX with explicit state tensors, then **INT8 dynamic quantization**.
  Verify numerical parity against PyTorch in CI (max abs error threshold).
- **Runtime:** ONNX Runtime CPU with `intra_op_num_threads=1` and one session per
  worker process, shared read-only. Per-call state lives in the
  `DograhDenoiseFilter(BaseAudioFilter)` instance. The filter buffers to 10 ms hops
  and returns the same number of samples it receives (no drift).
- **Model distribution:**
  - Store versioned artefacts (`dograh-denoise-v1.0.0.onnx` + sha256) in
    MinIO/S3.
  - Download and cache at worker startup.
  - Pin the version in config so it can be rolled back instantly.
- **Hooks into plan #1:**
  - Becomes `engine: "dograh"` in `NoiseReductionConfiguration`.
  - Inherits `SafeAudioFilter` (fail open, circuit breaker), the `vad_only` mode,
    the strength mix, metrics and the kill switch.
- **Later, if needed:** port the runtime to Rust (`tract` or `ort`) behind a small
  Python extension if Python overhead shows up in profiles.

---

## 9. Roadmap

| Phase | Weeks | Deliverable |
|---|---|---|
| 0 | 1 | Real-call eval set, eval harness, baseline numbers (raw, RNNoise, Krisp trial if available) |
| A | 1–3 | DeepFilterNet3 / GTCRN as pipeline engines plus benchmark. Ship the best as interim. |
| B | 3–8 | Data pipeline with telephony augmentation. Train Dograh Denoise v1 (NC). ONNX + INT8. Engine integration. Beat the gates. |
| C | 8–10 | Shadow mode in production: run the model, log metrics, don't use its output. Then a 5% → 50% → 100% opt-in rollout. |
| D | 10–16 | BVC v2a (implicit dominant speaker) |
| E | 16–22 | BVC v2b (self-enrolled speaker embedding), multilingual fine-tunes |

**People:** 1 ML engineer (audio/DSP experience strongly preferred) plus part of a
backend engineer for integration.
**Compute:** 1–4 × A100/H100-class GPUs, with about 1–2 weeks per full training
run. Data prep is CPU-heavy, so budget storage of about 1–2 TB.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Model hurts STT on clean audio | ASR-aware loss, over-suppression penalty, WER gate, `vad_only` mode, strength mix |
| BVC removes the real caller | Conservative training ratios, embedding-confidence fallback, real-call blind test |
| CPU cost too high at scale | INT8, GTCRN-size fallback model, per-worker capacity limits from load tests |
| Dataset licensing | Licence review per dataset. Commercial-safe subsets only for shipped weights. |
| Privacy of call recordings | Explicit consent, de-identification, access-controlled storage, never in shipped artefacts |
| Quality plateaus below Krisp | Keep Krisp/AIC as premium engines (plan #1). Our model covers the default free tier. |
