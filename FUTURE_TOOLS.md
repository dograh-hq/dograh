# Future Tools Roadmap

Tools planned for future implementation in Dograh.

---

## 1. `get_current_time` / `convert_time`

**Status:** Backend fully implemented in `api/services/workflow/tools/timezone.py` — just needs wiring up.

**What it does:**
- `get_current_time(timezone)` — Agent tells the caller the current time in any timezone
- `convert_time(source_timezone, time, target_timezone)` — Agent converts a time between two timezones

**Use case:**
Scheduling agents, appointment bots, international call centers.
> *"What time is it in New York right now?"* → Agent calls `get_current_time("America/New_York")` and responds accurately.

**What needs to be done:**
- Add `TIME = "time"` to `ToolCategory` in `api/enums.py`
- Add a new Alembic migration for the enum
- Register `get_current_time` and `convert_time` handlers in `pipecat_engine_custom_tools.py`
- Add the tool category config in `ui/src/app/tools/config.tsx`
- Add `WaitToolDefinition` equivalent for time tool in `ui/src/client/types.gen.ts`

---

## 2. `play_audio_clip`

**Status:** Not implemented. Internal `play_audio` utility exists in `api/services/pipecat/audio_playback.py` but is not exposed as an LLM-callable tool.

**What it does:**
- LLM dynamically decides mid-call to play a specific pre-recorded audio clip
- Different from current usage where audio is always pre-configured and fixed (e.g. hold music during wait, goodbye on end call)

**Use case:**
Regulated industries — finance, insurance, healthcare — where specific legal disclaimers must be read **verbatim** from a pre-recorded clip, triggered based on conversation context.
> *User asks about refund policy* → LLM calls `play_audio_clip(clip_id="refund_disclaimer")` → exact approved recording plays.

Also useful for: brand voice recordings, non-verbal audio (beeps/tones), multilingual clips mid-call.

**What needs to be done:**
- Add `AUDIO_CLIP = "audio_clip"` to `ToolCategory` in `api/enums.py`
- Add a new Alembic migration for the enum
- Implement the tool handler in `pipecat_engine_custom_tools.py` using existing `play_audio()`
- UI config in `config.tsx` with a clip picker (select from existing recordings)
- The clip library would use the existing recordings/uploads system in Dograh

---

## 3. `send_dtmf`

**Status:** Not implemented. Dograh has inbound DTMF (receive user keypresses) but NOT outbound DTMF (agent sends keypresses).

**What it does:**
Agent programmatically sends DTMF tones through an outbound call.

**Use case:**
Agent makes an outbound call and the other end is an IVR system.
> *Clinic IVR: "Press 1 for appointments, press 2 for billing"*
> Agent calls `send_dtmf(digit="1")` → tone is sent → IVR proceeds to appointments menu.

**What needs to be done:**
- Implement per-provider: Twilio (`calls.update(dtmf=...)`) , Plivo (`send_digits`), Telnyx, etc.
- Each telephony provider has its own API for sending DTMF tones mid-call
- Register handler in `pipecat_engine_custom_tools.py`
- Expose in UI as a built-in tool

---

## 4. `send_sms`

**Status:** Not implemented. Telephony integrations for Twilio/Plivo exist in Dograh, but there is no SMS sending capability exposed.

**What it does:**
Agent programmatically sends a text message to the caller (or another number) during or after the call.

**Use case:**
Agent finishes an action and sends confirmation details.
> *Agent: "I've booked your appointment. I'll text you the confirmation details right now."*
> Agent calls `send_sms(message="Your appointment is confirmed for tomorrow at 3pm.")` → SMS is delivered via the telephony provider.

**What needs to be done:**
- Implement SMS sending for each provider (Twilio Programmable SMS, Plivo SMS API).
- Register `send_sms` handler in `pipecat_engine_custom_tools.py`.
- Expose in UI as a built-in tool. (Note: May require handling regulatory constraints like DLT in India).

