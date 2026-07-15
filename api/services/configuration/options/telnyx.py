# Telnyx TTS voices (NaturalHD family)
TELNYX_TTS_VOICES = [
    "Telnyx.NaturalHD.astra",
    "Telnyx.NaturalHD.luna",
    "Telnyx.NaturalHD.atlas",
]

# Telnyx TTS models (model_id component of the voice string)
TELNYX_TTS_MODELS = ["natural-hd"]

# Telnyx STT transcription engines (used as transcription_engine query param)
TELNYX_STT_MODELS = ["Telnyx", "Deepgram", "Google", "Azure"]

# Telnyx STT input formats (raw PCM is the pipecat default)
TELNYX_STT_INPUT_FORMATS = [
    "linear16",
    "linear32",
    "mulaw",
    "alaw",
    "mp3",
    "wav",
    "flac",
    "webm",
    "ogg",
]

# Common language codes
TELNYX_TTS_LANGUAGES = [
    "en",
    "es",
    "fr",
    "de",
    "it",
    "pt",
    "hi",
    "ja",
    "ko",
    "zh",
]
TELNYX_STT_LANGUAGES = [
    "en",
    "es",
    "fr",
    "de",
    "it",
    "pt",
    "hi",
    "ja",
    "ko",
    "zh",
]
