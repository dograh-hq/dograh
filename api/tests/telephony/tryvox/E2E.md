# TryVox end-to-end test flow

## Prerequisites

- A publicly reachable Dograh HTTPS endpoint and matching WSS endpoint.
- A running TryVox deployment with the Voice, Nucleus, FreeSWITCH, and gateway services.
- A TryVox Auth ID, Auth Token, per-account webhook secret, Voice Application ID, and active E.164 phone number.
- A published Dograh workflow with working STT, model, and TTS configuration.

## Automated provider tests

From the Dograh repository:

```bash
source venv/bin/activate
set -a
source api/.env.test
set +a
PYTHONPATH=pipecat/src python -m pytest api/tests/telephony/tryvox -q
```

These tests cover the outbound API request, exact raw-body HMAC verification,
replay rejection, VoxML response, binary PCM input, `playAudio` output, Answer
route, status route, and call/run correlation.

## Configure Dograh

1. Open **Settings → Telephony** and create a **TryVox** configuration.
2. Enter the Auth ID, Auth Token, webhook secret, and Voice Application ID.
3. Keep the API base URL at `https://api.tryvox.io` unless testing a private deployment.
4. Add the TryVox E.164 number under the configuration's phone numbers.
5. Make the number the default caller ID and make the configuration the default outbound provider.
6. Attach a published workflow to the number if inbound calls will be tested.

Attaching the inbound workflow updates the configured TryVox Voice Application
to use Dograh's `/api/v1/telephony/inbound/run` endpoint and assigns the number
to that application.

## Outbound call

1. From the published workflow, place a test call to a reachable phone.
2. Answer the phone and speak after the workflow greeting.
3. Confirm the assistant receives speech and its response is audible.
4. Allow the response to finish, then continue the conversation.
5. End the workflow or hang up the phone.

Expected sequence:

1. Dograh sends `POST /v1/voice/accounts/{auth_id}/calls`.
2. TryVox sends a signed POST to `/api/v1/telephony/tryvox/answer`.
3. Dograh returns VoxML containing an `inbound_track` Stream.
4. TryVox opens Dograh's TryVox WSS route with its short-lived, single-use
   capability and the `audio.drachtio.org` subprotocol, then sends metadata
   followed by binary PCM16 at 8 kHz.
5. Dograh sends `playAudio` JSON messages containing Base64 PCM16.
6. TryVox plays those messages on the call leg.
7. Signed status callbacks update the Dograh workflow run through ringing, answered, and hangup states.

TryVox does not currently expose a public clear-playback command, so this flow
does not assert immediate barge-in cancellation of audio already queued for
playback.

## Inbound call

1. Call the TryVox number attached to the published workflow.
2. Confirm `/api/v1/telephony/inbound/run` detects the TryVox request.
3. Confirm the HMAC signature and Auth ID select the correct organization configuration.
4. Confirm Dograh creates one workflow run and returns VoxML Stream instructions.
5. Complete a two-way conversation and hang up.
6. Confirm the workflow run completes and releases its concurrency slot.

## Verification

Dograh logs should contain the run ID, TryVox provider selection, WebSocket
connection, pipeline start, and processed status callbacks. TryVox Nucleus logs
must not contain:

```text
invalid audio stream playback event
stream playback failed
stream playback queue full
```

FreeSWITCH must report `mod_audio_fork` as loaded:

```bash
docker exec tryvox-freeswitch fs_cli -x "module_exists mod_audio_fork"
```

Expected output:

```text
true
```

## Failure checks

- Change one byte of a captured Answer or status body without changing its signature: Dograh must return `401`.
- Reuse a signed request after five minutes: Dograh must return `401`.
- Replay the same valid status callback inside five minutes: Dograh must
  acknowledge it without processing the status twice.
- Send a valid callback for a different call ID: Dograh must return `403`.
- Connect to the media WebSocket without its capability, with the wrong
  capability, or reuse a redeemed capability: Dograh must reject the socket
  before starting the workflow run.
- Connect the media WebSocket without metadata for ten seconds: Dograh must close it with code `4408`.
- Send metadata containing another workflow run ID: Dograh must close it with code `4403`.
