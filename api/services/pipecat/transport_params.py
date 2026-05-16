"""Shared helpers for tuning pipecat ``TransportParams`` per run mode.

These live outside ``transport_setup.py`` (which is non-telephony only) so
that both the WebRTC factory there and the telephony provider factories
under ``api.services.telephony.providers/<name>/transport.py`` can call
into the same place.
"""

# Realtime (speech-to-speech) LLMs don't emit ``TTSStoppedFrame``, so the
# bot-stopped-speaking signal relies on the output-queue-drained fallback.
# The default 3s tail leaves a long gap before the assistant aggregator
# closes its turn; 0.5s keeps the conversation snappy without cutting into
# the bot's own audio (audio chunks arrive far more frequently than this).
REALTIME_BOT_VAD_STOP_SECS = 0.5


def realtime_param_overrides(is_realtime: bool) -> dict:
    """Return kwargs to splat into ``TransportParams`` for the given run mode.

    Currently this only tunes ``bot_vad_stop_secs``; new realtime-specific
    knobs should be added here so each transport stays a thin shim.
    """
    if not is_realtime:
        return {}
    return {"bot_vad_stop_secs": REALTIME_BOT_VAD_STOP_SECS}
