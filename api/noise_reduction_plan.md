# Noise Reduction (Background Noise Removal) — Implementation Plan

Goal: strip background noise (traffic, TV, office chatter, fans, street noise) from
the **caller's** audio before it reaches VAD, turn detection and STT. That cuts
false barge-ins and phantom turns and improves transcription accuracy. The work is a
self-contained package that every transport (Twilio, Plivo, Vonage, Telnyx, Exotel,
Vobiz, Cloudonix, ARI, WebRTC) picks up through one hook.

---

## 0. Status: Phase 1 shipped

Built in `api/services/pipecat/noise_reduction/`. It lives under `services/pipecat/`
next to the ambient-noise mixer it mirrors, not in a new `audio_processing/` tree.

| Piece | File | Notes |
|---|---|---|
| RNNoise engine | `rnnoise.py` | Calls the vendored `api/native/rnnoise/librnnoise.so` over ctypes and resamples with soxr. It does **not** use pipecat's `RNNoiseFilter`/`pyrnnoise` (see below). |
| Fail-open wrapper | `safe_filter.py` | Passthrough if start fails. Trips after 5 errors or 50 consecutive frames over 10 ms. Logs a per-call timing summary on stop. |
| Factory | `factory.py` | `build_audio_in_filter(config)` returns a fresh filter per call, or `None`. |
| Kill switch | `api/constants.py` | `NOISE_REDUCTION_ENABLED=false` forces passthrough everywhere. |
| Schema | `api/schemas/workflow_configurations.py` | `noise_reduction_configuration: {enabled: false, engine: "rnnoise"}` |
| Wiring | every `telephony/providers/*/transport.py`, `pipecat/transport_setup.py`, `pipecat/run_pipeline.py` | `audio_in_filter=build_audio_in_filter(noise_reduction_config)` |
| UI | `WorkflowSettingsSections.tsx` | "Noise Reduction" on/off switch below Ambient Noise. Engine picker comes in Phase 3. |
| Tests | `api/tests/test_noise_reduction.py` | Factory, kill switch, schema, engine pacing at 8/16/24/48 kHz, suppression, per-call isolation, breaker. |

Findings that changed the plan:

* **pipecat's `RNNoiseFilter` is too slow.** It costs ~3.4 ms per 20 ms frame at 8 kHz.
  `pyrnnoise`'s bundled library build is ~3.5x slower than the vendored one, and the
  package adds libav graph overhead on top. The direct engine costs ~0.7 ms per frame
  in a real pipeline, about 3.5% of one core per call, measured on a 2.8 GHz Xeon.
  So `api/native/rnnoise/` is now **used**, not dead code, and the Dockerfile needs
  no `rnnoise` extra. Only the unused `librnnoise_path` variable was removed.
* **The resampler quality matters for latency.** soxr "HQ" buffers ~220 ms at
  8 kHz, and "MQ"/"LQ" buffer 36–48 ms. "QQ" adds ~0.25 ms, so it is the default.
* **arm64:** the vendored library is x86-64 only. On arm64 images the engine falls back
  to `pyrnnoise`'s library if that package is installed, or to `RNNOISE_LIBRARY_PATH`.
  Otherwise `SafeAudioFilter` passes audio through and logs a warning. Follow-up:
  build librnnoise per architecture in the Dockerfile, ideally with x86 RTCD/AVX2
  for a further speed-up.

---

## 1. What exists today

| Piece | Where | Relevance |
|---|---|---|
| `TransportParams.audio_in_filter` | `pipecat/src/pipecat/transports/base_transport.py:91` | Pipecat's hook for input audio filters. It runs in `BaseInputTransport._audio_task_handler` (`base_input.py:284`) **before** audio is pushed downstream, so VAD (in the user aggregator), STT and the recorder all see filtered audio. |
| Built-in filters | `pipecat/src/pipecat/audio/filters/` | `RNNoiseFilter` (open source, `pyrnnoise`), `KrispVivaFilter` (commercial SDK), `AICFilter` (ai-coustics, commercial), `KoalaFilter` (Picovoice, commercial). All implement `BaseAudioFilter` (`start/stop/filter/process_frame`). |
| Ambient-noise **output** mixer | `api/services/pipecat/audio_mixer.py` | Exact pattern to copy: workflow config → `build_audio_out_mixer()` → passed into each transport factory. |
| `api/native/rnnoise/librnnoise.so` + `librnnoise_path` | `api/services/pipecat/audio_mixer.py:12` | Dead code: nothing uses it. `pyrnnoise` ships its own binary. Remove it in this work. |
| Workflow config schema | `api/schemas/workflow_configurations.py` (`AmbientNoiseConfigurationDefaults`) | Add a sibling `NoiseReductionConfiguration`. |
| UI settings | `ui/src/app/workflow/[workflowId]/settings/WorkflowSettingsSections.tsx` | Add a section next to Ambient Noise. |
| Docker pipecat extras | `api/Dockerfile:37` | `rnnoise` extra is **not** installed today. |
| STT benchmark | `evals/stt/benchmark.py` | Reuse for the WER part of the quality eval. |

Sample rates: telephony runs at 8 kHz (most providers) or 16 kHz, and WebRTC at 16 kHz
(`api/services/pipecat/audio_config.py`). RNNoise works at 48 kHz internally, and the
pipecat filter resamples in and out with soxr.

---

## 2. Engine choice

| Engine | Quality | CPU / call | Cost | License | Recommendation |
|---|---|---|---|---|---|
| **RNNoise** | Good on stationary noise (fans, hum, street), weaker on babble/TV | Very low (~1–3% of a core) | Free | BSD | **Default engine.** Works fully offline and fits OSS self-hosting. |
| **Krisp VIVA** | Best-in-class, including voice isolation (removes background *speakers*) | Medium | Paid licence + SDK wheel | Proprietary | Premium option, BYO licence. |
| **ai-coustics (AIC)** | Very good, tuned for STT/voice agents | Medium | Paid API key | Proprietary | Premium option, BYO key. |
| **Koala** | Good | Low–medium | Paid key | Proprietary | Optional. Lowest priority. |
| DeepFilterNet 3 | Better than RNNoise on non-stationary noise | High (≈10× RNNoise) | Free | MIT/Apache | **Phase 3 candidate only.** Needs a custom `BaseAudioFilter` with ONNX runtime, plus capacity testing. |

Ship RNNoise first because it is free and needs no keys. Put the commercial engines
behind the same interface so an org can switch per workflow.

---

## 3. Architecture

### 3.1 New package

```
api/services/audio_processing/noise_reduction/
├── __init__.py            # public API: build_audio_in_filter, NoiseReductionEngine
├── config.py              # NoiseReductionConfig (pydantic), engine enum, defaults
├── factory.py             # build_audio_in_filter(sample_rate, config, org_id) -> BaseAudioFilter | None
├── engines/
│   ├── rnnoise.py         # thin wrapper over pipecat RNNoiseFilter (resampler quality, warm-up)
│   ├── krisp.py           # wraps KrispVivaFilter, reads licence from env / org credentials
│   ├── aic.py             # wraps AICFilter, reads key from env / org credentials
│   └── koala.py
├── safe_filter.py         # SafeAudioFilter: fail-open + circuit breaker + timing metrics
├── mix_filter.py          # WetDryMixFilter: blends denoised with original (strength 0..1)
├── vad_denoise.py         # DenoisedVADAnalyzer: denoise only what VAD sees (see 3.3)
└── metrics.py             # per-call stats -> CallEventRecorder + logs
```

### 3.2 Wiring (same pattern as the ambient-noise mixer)

1. `run_pipeline.py` reads `workflow_configurations["noise_reduction_configuration"]`
   next to `ambient_noise_configuration` (around line 398, plus the WebRTC path).
2. Every transport factory (`api/services/telephony/providers/*/transport.py` and
   `api/services/pipecat/transport_setup.py`) takes `noise_reduction_config` and
   passes `audio_in_filter=build_audio_in_filter(noise_reduction_config)` into its
   transport params.
   To avoid editing ten files every time a knob is added, put both the mixer and the
   filter behind one helper: `build_audio_params(audio_config, ambient_cfg, nr_cfg)`.
   It returns a kwargs dict that is splatted into `TransportParams`, like
   `realtime_param_overrides`.
3. **One filter instance per call.** Filters hold DSP state, so they must never be
   shared. Process-wide caching is only for model weights (the AIC model manager
   already does this). Krisp SDK global init goes through `krisp_instance.py`.
4. Realtime (speech-to-speech) runs: the same transport hook applies. The audio
   still goes through `transport.input()`.

### 3.3 Modes (`apply_to`)

Denoising is not free for STT. Providers like Deepgram and Google are trained on noisy
audio, and aggressive suppression can clip word onsets and *raise* WER. The config
therefore offers two modes:

* `all` (default for RNNoise): the filter goes into `audio_in_filter`, so VAD, STT and
  the recording all get clean audio.
* `vad_only`: the transport filter is off. `DenoisedVADAnalyzer` wraps
  `SileroVADAnalyzer` and denoises a copy of the buffer inside
  `voice_confidence()`/`analyze_audio()`. VAD stops firing on background noise
  (fewer false interruptions) while STT still gets the original audio. This needs
  **no pipecat fork change**. It plugs in where `SileroVADAnalyzer(...)` is built in
  `run_pipeline.py` (lines 181, 297, 973).

The `strength` setting (0.0–1.0) is applied by `WetDryMixFilter`:
`out = s*denoised + (1-s)*original`. Values around 0.8 keep a little "air", which
reduces musical-noise artifacts and STT degradation. Mixing needs both signals
time-aligned, so the wrapper has to compensate for the filter's buffering delay.
RNNoise emits in 10 ms frames, and the wrapper keeps a matching delay line for the
original audio.

### 3.4 Production hardening (`SafeAudioFilter`)

* **Fail open.** If engine init fails (missing wheel, bad licence, model download
  failure), log once, record a call event, and pass audio through unchanged. A call
  must never drop because of noise reduction.
* **Circuit breaker.** After N exceptions, or after M consecutive frames over the time
  budget, disable the filter for the rest of the call and pass audio through.
* **Latency budget.** Telephony frames are 20 ms. The target is **p99 filter time
  < 2 ms per frame** and **< 10 ms of added algorithmic delay**. Time each call
  with `time.perf_counter_ns()`.
* **Event loop safety.** Filters run inline on the asyncio loop. RNNoise is cheap
  enough for that. If benchmarks show Krisp, AIC or DeepFilterNet over budget at
  target concurrency, run `filter()` in a dedicated single-thread executor per
  worker (`loop.run_in_executor`). Never use the default pool, which other code
  shares.
* **Empty returns.** The pipecat RNNoise filter returns `b""` while it buffers.
  `base_input.py:288` already skips empty frames, so this is safe. Add a test so a
  pipecat bump cannot regress it.
* **Runtime toggle.** Support `FilterEnableFrame` so a future feature (for example,
  turning NR off during DTMF or hold music) can switch it mid-call.
* **DTMF.** Inband DTMF tones can be suppressed as noise. Providers deliver DTMF
  out-of-band (`dtmf` events), but document the limitation and test with ARI.

### 3.5 Recording

`AudioBufferProcessor` sits downstream of the filter, so in `all` mode **recordings
contain denoised audio**. That is usually what customers want. It also hides what the
caller actually heard, which matters for QA. v1 records denoised audio and states
this in the UI copy. Phase 3 optionally keeps a raw copy.

---

## 4. Configuration

Pydantic model in `api/schemas/workflow_configurations.py`:

```python
class NoiseReductionConfiguration(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    engine: Literal["rnnoise", "krisp", "aic", "koala"] = "rnnoise"
    apply_to: Literal["all", "vad_only"] = "all"
    strength: float = Field(default=1.0, ge=0.0, le=1.0)
```

Add it to `WorkflowConfigurationDefaults` as `noise_reduction_configuration`.
Duplication already copies `workflow_configurations` wholesale
(`api/services/workflow/duplicate.py`), so check that the new key is covered.

Credentials for commercial engines:
* Self-hosted: env vars (`KRISP_VIVA_MODEL_PATH`, `AIC_SDK_LICENSE`,
  `KOALA_ACCESS_KEY`). Document them in `docs/` and add them to `api/.env.example`
  if one exists.
* Hosted SaaS: per-org credentials through the existing model-connections /
  credentials store. Never put them in workflow JSON.

A global kill switch, env var `NOISE_REDUCTION_ENABLED=false`, forces passthrough
for incident response.

UI (`WorkflowSettingsSections.tsx` + `ConfigurationsDialog.tsx`): toggle, engine
select (commercial engines disabled with a tooltip when no credential is
configured), "Apply to: All audio / Voice detection only", strength slider.
Regenerate `ui/src/client/types.gen.ts` and `ui/src/types/workflow-configurations.ts`
with the repo's SDK/OpenAPI generation scripts.

---

## 5. Dependencies and build

* `api/Dockerfile:37`: add `rnnoise` to the pipecat extras. That pulls
  `pyrnnoise~=0.4.3` and `av<17.1.0`. Check the `av` pin against what `webrtc`
  already pulls in.
* Commercial engines are **optional extras** and not in the default image
  (`aic` → `aic-sdk`, `koala` → `pvkoala`, Krisp → private wheel). Each factory
  catches `ImportError` and returns `None` with a clear log line.
* Delete `api/native/rnnoise/` and `librnnoise_path` (unused).
* Add the same extras to `api/requirements*.txt` / `scripts/setup-worktree.sh` so
  local dev matches the image.

---

## 6. Testing and evaluation

### 6.1 Unit tests (`api/tests/services/audio_processing/test_noise_reduction.py`)
* Factory returns `None` when disabled, and the right engine type otherwise.
* RNNoise at 8k, 16k and 24k input: output length over time equals input length
  (no drift), and output is int16 mono.
* `SafeAudioFilter`: an engine that raises gives passthrough. The breaker trips after
  N errors. Init failure gives passthrough plus one warning.
* `WetDryMixFilter`: strength 0 returns the original exactly (after delay
  alignment), and strength 1 returns the denoised audio.
* Per-call isolation: two instances fed different audio do not cross-contaminate.
* `DenoisedVADAnalyzer`: a noise-only fixture produces lower `voice_confidence`
  than raw Silero does.
* Schema: old workflow JSON without the key still validates, and null is treated as
  unset.

### 6.2 Offline quality harness (`evals/noise_reduction/`)
* Dataset: clean speech (a few hundred utterances, including real 8 kHz call
  snippets with consent) mixed with noise (MUSAN / DEMAND / recorded call-center
  babble) at SNR 0, 5, 10 and 20 dB, μ-law encoded to mimic PSTN.
* Metrics per engine × mode × strength:
  * **DNSMOS / PESQ / STOI** for perceptual quality
  * **WER** through `evals/stt/benchmark.py` for each STT provider in use
    (Deepgram, Google, etc.)
  * **VAD false-trigger rate** on noise-only clips, and **missed-speech rate**
  * **Processing time**: p50 and p99 per 20 ms frame
* Acceptance gate for turning it on by default: false-trigger rate drops ≥ 50% at
  5 dB SNR, WER does not get worse by more than 1 point absolute on clean audio,
  and p99 stays < 2 ms per frame.

### 6.3 Load test
Run N concurrent simulated calls (e.g. 50 and 200) per worker and measure CPU,
event-loop lag, and audio-task queue depth with NR on and off. That tells you how much
worker capacity to plan for.

---

## 7. Observability

Per call, record one `CallEventRecorder` event (`noise_reduction_summary`) with:
engine, mode, strength, init time, frames processed, p50/p99 processing ms, whether
the breaker tripped, and errors. Add a structured log on init failure. Optionally
track the RNNoise speech probability average. A low value across the whole call often
means a noisy line, which is useful in call QA.

---

## 8. Phased rollout

| Phase | Scope | Exit criteria |
|---|---|---|
| **1. Core (≈1 week)** | Package skeleton, RNNoise engine, `SafeAudioFilter`, config schema, transport wiring through a shared helper, Dockerfile extra, unit tests, kill switch, removal of dead `librnnoise` code. Default **off**. | Unit tests green. Manual Twilio and WebRTC calls with NR on show no added latency and clean audio. |
| **2. Quality + UI (≈1 week)** | Eval harness and results, `vad_only` mode (`DenoisedVADAnalyzer`), strength/wet-dry, UI section, docs page, call-event metrics. | Eval meets the gate in 6.2. Load test sizing documented. |
| **3. Premium + extras** | Krisp / AIC / Koala engines with per-org credentials, optional raw-audio recording, executor offload if needed, DeepFilterNet spike. | Each engine passes the same eval and load tests. |
| **4. Default-on** | Enable RNNoise `vad_only` (or `all`, whichever the eval favours) by default for **new** workflows. Existing workflows stay unchanged. | Two weeks of production metrics with no regression in call completion or interruption rate. |

---

## 9. Open questions

1. Hosted product: should premium engines (Krisp/AIC) be billed by Dograh or
   BYO-licence only?
2. Should NR also apply to the **bot** side for voicemail detection? (Probably not,
   since TTS is clean.)
3. Should the eval include customer-specific noise profiles (e.g. a client's call
   center)?
