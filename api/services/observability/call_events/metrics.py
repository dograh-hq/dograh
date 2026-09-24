def ttfb_kind(processor: str | None) -> str | None:
    """Classify a TTFB metric by the service that produced it: "llm" | "tts" | "stt".

    The `processor` field carries the Pipecat class name plus the instance index
    (``<Vendor>STTService#3``). The naming convention is ``<Vendor><ACRONYM>Service``,
    so the acronym is matched as a SUFFIX, not as a bare substring.

    That distinction is the whole point of this helper: every ``...STTSERVICE``
    *contains* the substring "TTS" (S-TTS-ERVICE), so a naive ``"TTS" in processor``
    check files transcription latency under speech synthesis — two wrong buckets at
    once, silently, with values that still look plausible. The substring branch is only
    a fallback for class names outside the convention, and it tries STT before TTS for
    the same reason.

    Speech-to-speech services are ``...LLMService`` subclasses and are reported as
    ``llm``: in S2S the model *is* the generation latency. Returns ``None`` for anything
    unrecognised — the caller drops the event rather than filing it under the wrong
    kind.
    """
    name = (processor or "").split("#", 1)[0].strip().upper()
    if not name:
        return None
    for acronym, kind in (("STT", "stt"), ("TTS", "tts"), ("LLM", "llm")):
        if name.endswith(acronym + "SERVICE"):
            return kind
    for acronym, kind in (("STT", "stt"), ("TTS", "tts"), ("LLM", "llm")):
        if acronym in name:
            return kind
    return None
