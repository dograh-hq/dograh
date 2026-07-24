# Phase 0 load test — finding K (safe concurrent calls per web pod)

Standalone tooling for the call-based autoscaling work. See `AUTOSCALING_PLAN.md`
at the repo root for the full methodology. **Not shipped in the app image.**

`webrtc_caller.py` ramps N concurrent synthetic WebRTC calls against ONE pinned
`web` pod and, per step, records the pod's saturation signals so you can locate
the knee and compute `K = floor(0.75 × knee)`.

## Why it needs the dev server
Every synthetic call runs the real in-pod pipeline (Silero VAD + STT/LLM/TTS +
turn analyzer) against **real provider keys** — there is no mock provider. It
also needs TURN if `FORCE_TURN_RELAY` is set. It cannot be run on a laptop.

## Prerequisites (operational — see the plan §3)
1. **Loop-lag gauge deployed** — the pod must expose `loop_lag_p95_ms` on
   `GET /api/v1/health/active-calls` (added in `api/services/pipecat/loop_lag.py`).
2. **Test org:** raise `CONCURRENT_CALL_LIMIT` (e.g. 500) so the concurrency gate
   never trips before the pod knee; ensure it has quota; point it at the **real
   prod provider config**.
3. **Pin one pod:** scale `web` to `replicas=1`, `autoscaling.web.enabled=false`,
   resources = the prod pod size under test.
4. **Test WAV:** a loopable clip = one spoken utterance + ~0.7s silence, so VAD
   fires a real end-of-turn each loop (the turn cadence lives in the WAV, not code).

## Run
```bash
python -m venv /tmp/lt && /tmp/lt/bin/pip install -r requirements.txt
/tmp/lt/bin/python webrtc_caller.py \
  --base-url https://dev.dograh.com \
  --api-key  <test-org-api-key> \
  --workflow-id <id> \
  --wav ./speech_loop.wav \
  --devops-secret <X-Dograh-Devops-Secret> \
  --ice-server "turn:turn.dograh.com:3478,<user>,<cred>" \
  --ramp 1,2,5,10,15,20,25,30 --hold 240 --drain 60
```
Smoke-test with `--ramp 1` first: confirm `active_calls` goes 0→1→0 and the step
report shows received bot audio (non-null `underrun_rate_mean`).

## Reading the output
One JSON line per step. Knee = lowest `target_n` where ≥2 of these agree:
- `loop_lag_p95_ms_max` crosses ~50–100 ms sustained (primary signal),
- process CPU pinned near ONE core (measure separately: `pidstat -p <pid> 1`
  inside the pod — **not** `kubectl top`, which reads against the 2-core limit),
- `underrun_rate_mean` climbing,
- `turn_latency_p95` inflating **above the N=1 baseline** (never the absolute).

Run the full ramp once per profile (standard STT+TTS, then realtime) and publish
`K = min(K_standard, K_realtime)` as the Helm value.

## Verify against the live API before trusting a ramp
The run-creation response field and the exact `realtime_feedback` frame shape can
vary by version — the driver has fallbacks, but confirm on the `--ramp 1` smoke
run that `turn_latency_p95`/`ttfb_p95` come back non-null.
