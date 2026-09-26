# CALMOS / Sakinah Roadmap

This fork of Dograh powers the Sakinah clinical conversation platform
(CALMOS). Fork-specific work lives alongside the upstream codebase; see
`SAKINAH.md` for how to build, run, deploy, and test.

## Stage 1 — Scenario console and AI-to-AI simulation ✅ (complete)

Delivered:

- **Scenario Console** (`/sakinah`): browser voice sessions against a seeded
  "Sakinah Scenario Console" workflow, with live transcript and JSON session
  persistence under `data/sakinah-sessions/`.
- **AI-to-AI simulation** (`/sakinah/sim`): a simulated **Service User AI**
  converses with the **Sakinah AI** through two real Dograh workflow
  pipelines connected by an in-memory audio transport (revived LoopTalk
  internals). Start/Stop controls, live labelled transcript over WebSocket,
  max-duration watchdog, mutual teardown on failure, merged transcript
  persistence.
- **Clean turn-taking**: Sakinah always opens; the service user starts as a
  listener (`suppress_initial_greeting`) and replies only after hearing her.
- **Local voice via TURN**: the `local-turn` compose profile (coturn) relays
  WebRTC media between the browser and the API container on Docker Desktop.
- **CALMOS branding** and white-label adjustments (onboarding modal gated
  off).
- **Tests**: `api/tests/test_sakinah_simulation.py` — simulation lifecycle,
  turn aggregation, turn-taking gating, peer-failure teardown, watchdog,
  run authorization, org scoping.

Known limitations (tracked):

- Simulation state is in-process; multi-worker API deployments need
  worker-sync or sticky routing —
  [#4](https://github.com/applied-biosciences/dograh/issues/4).

## Stage 2 — Evaluation and scoring (planned)

- CALM scoring of Sakinah's conversational performance
- Risk scoring and suicide evaluation flags on transcripts
- Clinical evaluation rubrics and automated test reports

## Stage 3 — Scenario and persona management (planned)

- Scenario/persona database (replacing free-text scenarios)
- Hidden patient information the service-user simulator withholds until
  elicited
- Supabase-backed storage for scenarios, sessions, and evaluations

## Stage 4 — Scale-out simulation and regression testing (planned)

- Batch simulation runs (N scenarios × M repetitions)
- Regression testing across prompt/workflow versions
- Model comparisons (STT/LLM/TTS matrices)
- Depends on resolving #4 (multi-worker simulation state)

## Stage 5 — Production deployment (planned)

- AWS deployment and the `voice.calmos.io` environment
- S3 recording storage
- Production authentication changes
- Avatar/video front-end

## Ongoing

- Track upstream `dograh-hq/dograh` and merge regularly (the fork carries
  upstream commits plus the Sakinah/CALMOS work on top).
