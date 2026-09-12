# Answer Supervisor — outbound call handling

**Status:** implemented as the replacement for the old detector; live-carrier validation pending.
**Scope:** outbound, non-realtime. Updated 2026-09-10.
**Supersedes:** `api/voicemail_handling.md` (the PRD this elaborates).
**Historical code line numbers in §§1–2 reference `main` at `f729684e7`.**
The measurements below describe the original detector and were not independently
reproduced during implementation. The executable contracts and test checkpoints are in §§3–8. Existing workflows
with voicemail handling enabled automatically use the new supervisor after deployment.
The old detector and its prompt configuration have been removed from Dograh runtime.

In the original implementation, the agent started talking roughly half a second before the answering machine
did, and could not hear it when it started. This document describes the replacement: a
supervisor that watches the first two seconds of an outbound call and decides
*whether* to open, then *what* answered.

---

## 1. Why the agent is deaf

Three independent choices compound. None is a bug on its own.

**1. The greeting fires unconditionally.** Both readiness events resolve and we
open immediately (`event_handlers.py:110-121, 167-174`). Nothing consults voice
activity; nothing asks whether the far end is mid-utterance.

**2. The gate that would hold it is not installed.** We substituted `llm_gate()`
for `gate()` to avoid dead air (`pipeline_builder.py:76-91`). But the greeting is
a `TTSSpeakFrame` queued straight to the task (`pipecat_engine.py:888-896`), so it
never passes an LLM. And the LLM-generated opening uses
`engine.llm.queue_frame(...)` (`pipecat_engine.py:907`), which injects *at the LLM
service*, below the gate. **The substituted gate could gate neither of the two ways
this system starts talking.** Its only reachable input was aggregator-produced
frames — and during the greeting the aggregator is muted and produces none.

**3. Classification waits for a final transcript.** The 8 s early-detection timer
was meant to cover this. In production it fired with no transcript 188 times
against 10 successes: Deepgram Flux `Update` events never become frames
(`flux/stt_base.py:822, 832-851`), so the monitor has nothing to read.

Two further defects keep that timer dead regardless: `_on_timeout` sets
`_triggered` before the empty-transcript check and permanently disarms itself, and
`_schedule_timer()` starts on `StartFrame` rather than on far-end speech onset.

### The mechanism

`MuteUntilFirstBotCompleteUserMuteStrategy` returns `not self._first_speech_handled`
— muted from t=0, before any bot audio exists, until the first
`BotStoppedSpeakingFrame`. It drops `InterruptionFrame`, both VAD frames, both
proposed-turn frames, `UserStarted`/`StoppedSpeakingFrame`, and both transcription
frames. So during the greeting no turn resolves, no interruption fires, and
nothing the far end says enters the context.

That window is exactly where the machine is speaking: **98 of 114** voicemail
onsets land during our own utterance.

---

## 2. The measurements

All timings anchored on `on_pipeline_started` (`event_handlers.py:212`), which
fires when `StartFrame` reaches the task. This is t=0 throughout. The greeting
today is triggered by `maybe_trigger_initial_response()`, which fires on whichever
of `pipeline_started` / `client_connected` lands second — and since
`client_connected → pipeline_started` is p50 +82 ms, `pipeline_started` is usually
the later one. So t=0 is, in most runs, the instant we currently start speaking.

| Interval | p50 | p90 |
|---|---|---|
| WS accept (≈ answer) → `client_connected` | ~270 ms | — |
| `client_connected` → `pipeline_started` | 82 ms | 376 ms |
| **`pipeline_started` → far-end onset (VOICEMAIL)** | **681 ms** | 1184 ms |

We reach the transport around 430 ms after t=0. The machine's median onset is
681 ms. That ~250 ms head start is the whole failure.

### Choosing the window length

Fraction of VOICEMAIL runs (n=114) where the far end is already speaking:

| Check delay | Machines speaking | Humans speaking | Reading |
|---|---|---|---|
| 300 ms | 14.0% | 1.7% | Useless — 86% of machines still silent |
| 500 ms | 30.7% | 3.9% | Still misses two thirds |
| 800 ms | 66.7% | 13.9% | Climbing steeply |
| **1200 ms** | **90.4%** | **27.2%** | **The knee — adopt this** |
| 1500 ms | 93.0% | 30.0% | +2.6 pts for +300 ms |
| 2000 ms | 94.7% | 32.8% | Flat — pure latency cost |

Onset is anchored on Deepgram Flux `StartOfTurn`, biased late by 150-350 ms, and
misses the first 300-600 ms of audio entirely (`if not self.is_usable: return`,
plus a median 302 ms of provider media flushed from the ASGI buffer at connect).
A Silero-based gate sees onsets *earlier*, so every figure is a floor.

### Choosing the human/machine threshold

| First-utterance duration | Conversation (n=203) | Voicemail (n=108) |
|---|---|---|
| p10 | — | 3.75 s |
| p50 | 244 ms | 12.5 s |
| p90 | 1.21 s | — |
| **Cut at 2.0 s** | **6 false holds** | **103/108 recall** |

The conversation p90 is a third of the voicemail p10. Independently corroborated:
`first_seg_ms` and `first_onset_ms` are two of the top three features in the
published VAD-classification work, together carrying 31% of total importance.

**This was measured on turn duration, not VAD segment duration.** That matters —
see §3, listening and sorting.

---

## 3. Runtime behavior

There is one answer-handling implementation: `AnswerSupervisor`. It replaces the
native `VoicemailDetector` integration, including its classifier branch, context
gate, and immediate voicemail hangup callback. Neither pipeline builder accepts
an old detector. The unused Dograh tests for that third-party detector are replaced
by supervisor, classification, engine action, and production pipeline tests.

Every voicemail-enabled outbound call using separate STT/LLM/TTS services uses the
supervisor, including saved workflows with only the previous configuration fields.
There is no legacy fallback, shadow/listen mode, or rollout selector. Disabled
handling remains disabled. Inbound, realtime, and unknown-direction calls do not
run answer handling; they do not fall back to the old detector.

### Listening and sorting

The gate starts closed. The listening clock starts when
both `pipeline_started` and `client_connected` have fired, before any pre-call
fetch wait. Only `UserStartedSpeakingFrame` establishes the speech onset and
arms the utterance timer. VAD activity alone does not suppress the initial
silence fallback or defer the screening deadline. Turn completion comes from
the aggregator's `on_user_turn_stopped` event.

- No speech during the window: prepare a `release` verdict.
- A first resolved, nonempty turn shorter than `human_utterance_max_ms`: prepare a
  human `release` verdict, unless it matches a screener prompt, a screening-wait
  announcement or, while screening, another recognized machine prompt.
- A longer resolved turn, or further speech before a verdict is committed:
  classify the combined text of the completed turns.
- A started user turn that never resolves with usable text: disconnect with
  `machine_timeout` at `machine_utterance_cap_ms`, including during screening.
  The classifier has its own `classify_budget_ms` bound.

A prepared verdict does not open the context gate. The engine consumes the
current verdict only after pre-call fetch and `set_node`. New speech before that
consumption revokes unused permission and cancels stale classification while
retaining its text for the next classification. A short continuation cannot skip
classification of that accumulated text. The text resets when held turns are
discarded during screening. A silent window timer cannot overwrite a later
machine verdict or classifier wait.

### Classification

`answer_classification.py` contains pure pattern matching with this precedence:
`NO_MESSAGE`, `SCREENER`, `IVR`, `VOICEMAIL`, `SCREENING_WAIT`, then `UNKNOWN`.
Actionable prompts take precedence over a wait announcement in the same transcript.
Screener patterns include the clipped greeting “for calling. I'll see if this
person is available.” even when the name/reason request is missing from STT.
Unknown text is sent to a private `LLMContext` without workflow tools. The LLM can also return
`CONVERSATION`, so a long human answer is not automatically treated as voicemail.
Malformed/empty model output becomes `UNKNOWN`; failures and timeouts release the
initial opening. No classifier is invoked on empty speech/music evidence.

Private LLM calls emit `llm-answer-classifier` spans under the current call's turn
or conversation in Langfuse. Each span includes the classifier prompt, transcript,
model, raw response, and parsed subtype. The span covers inference latency and
keeps request metadata if inference fails or is cancelled. Pattern-only decisions
do not invoke the LLM and therefore do not create an LLM span.

The implemented patterns are conservative **English seed patterns**, covered by
synthetic examples in `test_answer_classification.py`. The original production
fixture corpus is not present. The earlier proposed 98.2% regex coverage and 1–2%
LLM call rate are **not verified properties of this implementation**. Measure both
on each carrier/language during carrier validation.

### Greeting completion

Classification waits for a resolved turn using the workflow's existing
endpointing. Terminal phrases are classification evidence after turn completion;
they do not bypass the turn boundary or prove that recording has started.

This remains a heuristic: a pause can look like the end of a greeting, and a
mailbox may not yet be recording. Listening tests do not establish successful
voicemail delivery. Verify the actual saved message during carrier testing.
Twilio `DetectMessageEnd` and beep detection are not part of this implementation.

## 4. The gate race and its regression tests

The original proposed rule, “open the gate on upstream `UserStoppedSpeakingFrame`
before resolving permission,” is unsafe. Pipecat uses separate queues for system
frames and context frames. A stop can reach the sensor while the completed turn's
`LLMContextFrame` is still queued; opening a shared boolean then admits a second
workflow generation alongside the greeting.

The implementation uses two ordering guarantees:

1. `AnswerContextGate` uses Pipecat's `enable_direct_mode=True`. The aggregator's
   `push_context_frame()` synchronously reaches the gate, which drops the trigger
   and records the acknowledgement before returning. It never awaits a timer,
   classifier, or engine action.
2. The supervisor observes the aggregator's `on_user_turn_stopped` **event**, after
   its context push. Raw upstream stop frames never grant opening permission.

Dropping the trigger preserves the already-committed user text. The gate stays
closed while the engine emits and waits for its opening, then opens permanently.
The engine's queued-speech mute protects the interval before bot audio starts as
well as playback, including the real greeting after a screening announcement.

The three opening paths still work: text uses `TTSSpeakFrame` from the pipeline
head, recorded audio goes directly to the output transport, and an LLM opening
injects directly at the LLM. The readiness decision covers all three.

Required regressions:

- `test_permission_waits_for_gate_acknowledgement_under_queue_backlog`: artificially
  delays the gate's context processing. Permission must remain pending until the
  trigger is dropped. Temporarily switching the gate back to queued mode makes
  this test fail with “Permission escaped before the old trigger was consumed”;
  the direct-mode implementation passes.
- `test_short_hello_is_committed_and_trigger_dropped_before_release`: the completed
  “Hello?” is in context and its trigger is already dropped when permission is
  returned; no duplicate generation appears, and the next user turn works.
- `test_hello_produces_exactly_one_opening`: exercises text, audio, and LLM openings
  with the production pipeline builder, real aggregators, engine opening methods,
  and Pipecat's output sender. Uses local mock LLM/TTS providers.
- `test_speech_during_pre_call_fetch_revokes_unused_silent_permission`: stale
  silent permission cannot bypass new speech.
- `test_readiness_arms_before_fetch_and_opens_only_after_permission`: checks both
  readiness events, fetch ordering, node setup, and exactly-once dispatch.

## 5. Engine actions and screening

The supervisor resolves `voicemail_action` into a `drop` or `leave_message`
verdict after classification. Logs and recorded actions use that verdict; the
engine executes it without reinterpreting the voicemail policy.

| Verdict | Action |
|---|---|
| `release` | Emit one opening, await bounded playback, open the context gate, restore idle timeout. |
| `leave_message` | Play the configured text or recording and wait for playback before ending with `voicemail_detected`. Missing or failed playback ends with `answer_message_failed`. |
| `drop` / `VOICEMAIL` | End without speech using `voicemail_detected`. |
| `drop` / `NO_MESSAGE` | End without speech using `voicemail_no_message`. |
| `drop` / `IVR` | End without speech using `ivr_detected`. Navigation is outside this implementation. |
| `drop` / `machine_timeout` | End without speech when a started user turn exceeds the utterance cap. |
| `screen_then_rearm` | Disable idle escalation, speak the screening message, then enter a dedicated screening wait. |

Recordings use the existing organization-scoped fetcher. UI selections store the
immutable `recording_pk`; programmatic configuration can use `recording_id`.
Messages are template-rendered text or prerecorded audio. Playback/fetch failures
are bounded and recorded as `answer_message_failed`; a missing screening message
ends with `screening_message_missing`. These reasons are in the disposition
catalog and skip final business-outcome extraction on machine-only calls.

### Screening wait is a separate state

The initial listening state releases after 1200 ms of silence. Screening must not do that.
The dedicated screening wait has its own `screening_wait_ms` deadline:

- A short, nonempty human turn releases the opening.
- A machine turn is classified again, allowing voicemail after screening.
- Empty audio evidence and unknown classification remain silent until the deadline.
- A resolved user turn start defers deadline expiry through usable turn completion
  and classification. The utterance cap disconnects unresolved turns, and
  classification retains its own budget. A human or machine decision takes
  precedence over the expired deadline; another wait announcement or unknown
  result resumes the original deadline without resetting it.
- Deadline expiry while idle ends with `screening_timeout`, never an idle escalation.
- At most two screening announcements/re-arms are allowed; another screener ends
  with `screening_limit`.

**Keep the user aggregator live while waiting.** A full user mute would suppress
resolved turns and make the eventual human answer undetectable. The context gate
blocks inference instead. Before re-arming, held machine user messages are removed
from the shared context by object identity; the subsequent human words remain.
This cleanup is required behavior, not an optional follow-up.

`UserIdleTimeoutUpdateFrame(timeout=0)` disables the timer. `UserIdleHandler` also
checks whether supervision blocks the workflow, covering idle callbacks already
scheduled before the timeout update. Normal idle handling is restored only when
the workflow takes over. Muting is limited to our own queued/playing speech.

The action task races pipeline closure. Hangup/cancellation cancels pending waits
and playback actions promptly, so a disconnected caller cannot receive a late
opening or keep the readiness handler alive until the playback timeout.

### Post-screening acknowledgment: `SCREENING_WAIT`

After our screening message finishes, the service may say **“Thanks. Please stay
on the line.”**, then ring the subscriber. This is classified as `SCREENING_WAIT`
and logged as `event=screening_wait subtype=SCREENING_WAIT action='wait'`. The
supervisor stays silent with inference gated and the user aggregator listening.
It does not replay the screening message or consume another screening attempt.

Recognized screener prompts and wait phrases bypass the short-human-turn shortcut,
including when the transcript takes less than `human_utterance_max_ms`. The private classifier
also supports `SCREENING_WAIT` for other wording. Known machine patterns likewise
take precedence over the duration shortcut during screening, so a short voicemail
prompt can trigger voicemail handling.

The acknowledgment is removed from held workflow context and classification text.
The next answer is evaluated afresh: a human releases the opening, and voicemail
uses the configured voicemail policy. Ringback with no transcript keeps waiting;
VAD activity alone is insufficient evidence of a human answer.

The existing `screening_wait_ms` deadline starts after our screening playback and
is not reset by acknowledgments. Repeated hold announcements therefore cannot
extend the call indefinitely. If a wait announcement is the first thing detected,
the supervisor starts a bounded screening wait without playing an introduction.

Required regressions:

- `test_screening_silence_does_not_release_at_ordinary_window`
- `test_screening_keeps_human_turns_live_and_removes_machine_context`
- `test_screening_can_reach_voicemail`
- `test_screening_acknowledgment_waits_for_human_or_voicemail`
- `test_llm_screening_wait_discards_acknowledgment_before_next_answer`
- `test_repeated_screening_acknowledgments_keep_original_deadline`
- `test_initial_wait_announcement_enters_bounded_screening_wait`
- `test_empty_speech_during_screening_waits_without_calling_classifier`
- `test_already_dispatched_idle_event_cannot_prompt_main_llm_while_supervised`
- `test_screening_then_human_restores_idle_and_opens_once`
- `test_voicemail_verdict_waits_for_playback_before_hangup`
- `test_repeated_screeners_have_a_finite_budget`
- `test_disconnect_during_playback_cancels_action_promptly`
- `test_opening_error_cannot_leave_gate_and_idle_detection_disabled`

## 6. Configuration and migration

Settings remain inside the existing `workflow_configurations.voicemail_detection`
JSON object, so enabled saved workflows require no migration or re-save to use the
replacement. `enabled` is the on/off control. Existing provider/model/key and
`use_workflow_llm` fields continue to select the private classifier.

`system_prompt`, `long_speech_timeout`, and the earlier proposed `supervisor_mode`
are ignored when reading saved JSON and removed when saving either UI form. The
classifier uses internal subtype instructions; an old binary-detection prompt
cannot override them. Customer messages are speech content, not classifier prompts.

| Field | Default |
|---|---|
| `enabled` | false for unconfigured workflows; existing value retained |
| `listening_window_ms` | Enabled Start-node Delayed Start duration × 1000 takes precedence. Otherwise use a saved window or the 1200 ms default. An enabled node without a duration uses the same 1.2-second default. |
| `human_utterance_max_ms` | 2500 |
| `machine_utterance_cap_ms` | 25000 |
| `classify_budget_ms` | 3000 |
| `screening_wait_ms` | 30000; provisional, not derived from the original three screener runs |
| `max_screening_rearms` | 2 |
| `voicemail_action` | `hangup`; select `leave_message` to play the voicemail message before ending |
| `voicemail_message` | `{ "text": "" }`; optional `recording_pk` or `recording_id`; used only with `voicemail_action=leave_message` |
| `screening_message` | `{ "text": "" }`; optional `recording_pk` or `recording_id` |

`_handle_start_node` never sleeps. It sets up the LLM context immediately. The
supervisor owns the listening timer and starts it at the readiness barrier, before
pre-call fetch. For example, **Delayed Start = 2.5 seconds** produces a **2500 ms**
listening window, even if a saved `listening_window_ms` differs. A brief human turn
can release earlier; ongoing machine speech holds the opening beyond the window.
This duration does not change the separate screening wait or classifier deadline.
`DEFAULT_LISTENING_WINDOW_SECONDS = 1.2` supplies both the supervisor default and
the Start node UI's default duration.

The Start node is the sole UI control for the initial window; voicemail settings
no longer contain a second listening-window editor. Calls outside the supervisor's
scope (inbound, realtime, disabled handling) have no start-node sleep. The UI help
states the applicable scope. The resolver validates once after applying the Start
node duration. Omitted fields receive their schema defaults. Invalid values,
including an explicit null listening window without a Start node override, raise
a validation error. No fields are removed or silently repaired.

Both the workflow dialog and **Workflow Settings → Voicemail & Screening** show:

- A toggle to enable voicemail and screening handling.
- **Voicemail message**, as text or a saved recording. Blank means hang up without
  leaving a message, including on previously enabled workflows without new messages.
- **Screening message**, as text or a saved recording, to state the caller's name
  and reason. Blank means end screened calls.
- A separate screening wait and classification-model settings, plus a pointer to
  the Start node’s Delayed Start control for initial listening.

There is no classifier prompt editor or stage selection. Both forms preserve the
supported timing, message, recording, and model settings when saving. Save and
publish to apply edited messages to the active workflow; this is not required to
activate the replacement for workflows that already enabled voicemail handling.

## 7. TDD and automated validation

Implementation was split into sensor/gate/config, engine actions, runtime wiring,
and UI controls. Tests for each boundary were added before its implementation and
run to observe failure. The initial sensor/classification tests failed on missing
modules, then exercised real Pipecat frame flow. A later timeout test caught a real
bug: the silent-window timer could release during residual classification. The
fix limits that timer to answers with no observed onset.
An opening-preparation failure regression was also observed red before adding the
gate/idle recovery path.

For the replacement change, tests first failed on enabled saved workflows bypassing
the supervisor, obsolete modes changing behavior, a zero window disabling it, and
message editors being hidden. They now require automatic replacement and saving
both messages while removing old detector configuration. Factory tests additionally
cover the fixed internal prompt, private context, retained model selection, and
absence of classifier initialization outside supported calls.

Validation on 2026-09-10: **159 backend regression tests and 5 UI tests passed**,
including the ordering and screening regressions above. TypeScript, ESLint, Python
static checks, and `git diff --check` passed. Backend warnings were two existing
Pipecat deprecations. No live carrier calls were made.

The Delayed Start follow-up first reproduced the remaining engine sleep, incorrect
precedence over the UI duration, zero-duration fallback, and duplicate UI control.
After the fixes, 237 backend cases passed; the two SDK synchronization checks also
passed after regenerating the Start node help in both SDKs. All 6 UI tests,
TypeScript, ESLint, Python static checks, and whitespace validation passed.
`test_start_node_sets_up_context_without_sleeping` verifies both with and without
a supervisor. Configuration tests cover UI-delay precedence, seconds-to-ms
conversion, the 1.2-second default, and an enabled older node with no saved duration.

| Test module | Contract |
|---|---|
| `api/tests/test_answer_supervisor.py` | Real queues/aggregator: ordered gate handoff, speech/turn deadlines, cancellation, classifier timeout, screening silence and human recovery. |
| `api/tests/test_answer_classification.py` | Synthetic pattern examples and precedence; does not certify production accuracy. |
| `api/tests/test_answer_classification_tracing.py` | In-memory span export: call/turn parentage, LLM request/response metadata, failures and cancellation. |
| `api/tests/test_answer_supervisor_config.py` | Automatic replacement of saved configurations, call scope, zero window, UI delay precedence and seconds-to-ms conversion, default window, rejection of invalid values and explicit nulls. |
| `api/tests/test_answer_handling.py` | Engine actions: playback before hangup, text/recording paths, screening budgets, cancellation. |
| `api/tests/test_answer_supervisor_wiring.py` | Fixed classifier instructions/model selection, scope, readiness/fetch ordering, initial listening mute, no start-node sleep, idle suppression. |
| `api/tests/test_answer_supervisor_playback.py` | Text/audio/LLM opening paths through the real output sender, exactly one opening. |
| `AnswerSupervisorFields.test.tsx`, `VoicemailDetectionDialog.test.tsx` | Messages visible by default, independent text/recording edits and screening deadline, obsolete fields removed on save, no duplicate listening-window editor. |

Run the new backend tests from the repo root, always using the test environment:

```bash
source venv/bin/activate
set -a
source api/.env.test
set +a
python -m pytest api/tests/test_answer_supervisor.py api/tests/test_answer_classification.py api/tests/test_answer_classification_tracing.py api/tests/test_answer_supervisor_config.py api/tests/test_answer_handling.py api/tests/test_answer_supervisor_wiring.py api/tests/test_answer_supervisor_playback.py
```

UI checks from `ui/`:

```bash
npm test -- 'src/app/workflow/[workflowId]/components/AnswerSupervisorFields.test.tsx' 'src/app/workflow/[workflowId]/components/VoicemailDetectionDialog.test.tsx'
./node_modules/.bin/tsc --noEmit --incremental false
```

These tests make no carrier calls. Provider responses/audio are synthetic; pipeline
queues, turn aggregation, and output playback machinery are real.

## 8. Manual test checkpoints

These are verification stages, not selectable runtime modes. Use an outbound
workflow and test numbers you control. The replacement applies automatically to
existing voicemail-enabled workflows after deployment.

### Stage 1 — Settings and saved-workflow replacement

Open **Workflow Settings → Voicemail & Screening** on a workflow that already had
voicemail detection enabled. Both message editors should be immediately available,
with no prompt, speech cutoff, or rollout selection. Enter different messages for
voicemail and screening, save, publish, and reload. Check both values persisted.
Repeat with saved recordings and confirm switching one message to text does not
change the other. Check the workflow dialog has the same behavior.

For an unchanged, previously enabled workflow without either message, verify the
supervisor holds the initial opening and still ends a detected voicemail without
speaking. It must not instantiate the old detector or use a saved old prompt.

### Stage 2 — Listening and one opening

Set **Delayed Start** on the Start node to **2.5 seconds**, save, and publish.
A silent answer should wait about 2.5 seconds from readiness before the opening
(plus speech preparation latency), with no second delay during node setup. Turn
Delayed Start off and repeat to verify the default 1.2-second window, assuming no
programmatically saved window. Say a brief “Hello?” to check early release. Check
that changing Delayed Start does not change the separate screening wait.

| Scenario | Expected result |
|---|---|
| Stay silent at answer | Opening starts after the configured window, plus synthesis/playback latency. |
| Say “Hello?” briefly | Opening follows the completed turn and occurs once. |
| Speak longer than two seconds | Opening stays held until the turn finishes and classification resolves, or the machine cap expires. A live human answer releases the workflow. |
| Use text, recorded audio, then no configured greeting | Each path produces one opening; the last uses the workflow LLM. |
| Make pre-call fetch slow and speak during the ringer | Current speech still holds the opening after fetch completes. |
| Make an inbound or realtime call, or disable handling | No answer supervision, old detector, or start-node sleep. |

Silent humans incur roughly one listening window of added latency. Track that
separately from early release after a brief human answer.

### Stage 3 — Message delivery and screening recovery

Configure both messages before testing, and repeat with text and recordings.

| Scenario | Expected result |
|---|---|
| Normal mailbox | Message follows the resolved greeting, finishes, then the call ends. Retrieve the saved voicemail and check its beginning and end. |
| Full/unconfigured mailbox | No message; `voicemail_no_message`. |
| Screener, then a human | One screening announcement, silence while waiting, one workflow opening after the human turn. No idle prompts and no screener user turn in workflow context. |
| Screener says “Thanks. Please stay on the line.” after our introduction | Log `screening_wait` / `SCREENING_WAIT`, remain silent through ringback, then handle the human or voicemail answer. No repeated introduction or timeout reset. |
| Screener, then voicemail | Leave the voicemail message and end. |
| Screener, nobody answers / music | Stay silent past the ordinary listening window, then `screening_timeout`. |
| Repeated screening prompts | At most two announcements, then `screening_limit`. |
| Hang up during classification or playback | Prompt cancellation, no delayed opening. |

Check `gathered_context.answer_supervisor` for the ordered list of accepted
verdicts, each containing the action, reason, subtype, and number of completed
screening re-arms at that decision. The last entry is the latest accepted verdict.
Check the call disposition for terminal machine outcomes.
Logs record the event, elapsed time, subtype, action, and decision strategy. Turn
decisions include the transcript length, turn-stop strategy, measured duration, and human
duration threshold. Pattern and LLM classification are identified separately;
classifier failures include status and time budget, and timer decisions include
their configured deadline. Operational logs omit caller transcript text.

### Limits to validate before wider deployment

- Once the engine commits to opening and playback completes, active supervision
  releases permanently. **Late-machine detection is not implemented.** The replacement
  loses the old detector's eventual late classification; measure that limitation
  during carrier validation.
- A human taking over while classification is pending can supersede an old turn.
  Overlapping human/machine speech inside one transcript, and takeover during our
  protected playback, are not reliable recovery paths in this version.
- Endpointing, language, and carrier differences affect both the two-second sort
  and the point at which a voicemail is ready to record. Do not treat the original
  single-workflow measurements as validation of this new implementation.
- `screening_wait_ms` is a tunable starting point. The existing maximum call
  duration remains an independent hard cap.
- Twilio AMD integration, beep detection, and IVR navigation remain separate work. No Pipecat submodule changes are needed here.
