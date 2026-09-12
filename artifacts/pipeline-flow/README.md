# Audio to conversation

Open [index.html](index.html) directly in a browser. It is a self-contained,
offline animation of the Dograh voice pipeline with no dependencies or service
startup. Press **Play**, select a chapter, or scrub the timeline.

- Compare the standard STT → LLM → TTS path with six realtime provider choices.
- Follow PCM audio, VAD/proposed speech frames, accepted user-turn frames,
  interruptions, context/text, and bot audio.
- Inspect local VAD ownership, its callback and self-queue, or provider-owned
  speech detection.
- Toggle standard-mode voicemail detection and the recording router.
- Click processors or **View source** to inspect embedded local source excerpts.

Playback is a schematic explanation of an established, unmuted voice session,
not a recording of a live execution. Real asynchronous branches overlap and
provider latency varies. The standard animation uses default local VAD start
and speech-timeout stop strategies. Other turn policies are described under
**Details that change the flow**.

The footer identifies the Dograh and Pipecat revisions used for the source
snapshot. To refresh excerpts after reviewing any code changes:

```sh
python3 artifacts/pipeline-flow/refresh_sources.py
```

The refresh script uses explicit line ranges. Update those ranges and the
animation's explanatory model together when the underlying code changes.
