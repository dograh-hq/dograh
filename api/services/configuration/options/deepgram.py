# Deepgram serves the same APIs from several regional hosts, and the host is what
# decides where audio is processed - and therefore which jurisdiction the audio
# lands in. api.eu.deepgram.com keeps /v1/listen, /v2/listen and /v1/speak inside
# EU infrastructure, which is what a European deployment needs to avoid an
# undeclared transfer. Regional hosts accept the same API keys as the default.
# https://developers.deepgram.com/reference/custom-endpoints
DEEPGRAM_DEFAULT_BASE_URL = "https://api.deepgram.com"
DEEPGRAM_BASE_URLS = (
    DEEPGRAM_DEFAULT_BASE_URL,
    "https://api.eu.deepgram.com",
    "https://api.au.deepgram.com",
)

DEEPGRAM_FLUX_MODELS = ("flux-general-en", "flux-general-multi")

# Deepgram has no separate TTS model parameter: a voice id carries its family
# as a prefix, and the family decides the endpoint. Aura voices are served from
# /v1/speak and Flux voices from /v2/speak.
DEEPGRAM_TTS_MODELS = ("aura-2", "aura-1", "flux")
DEEPGRAM_TTS_DEFAULT_VOICES = {
    "aura-2": "aura-2-helena-en",
    "aura-1": "aura-asteria-en",
    "flux": "flux-heather-en",
}
_DEEPGRAM_TTS_VOICE_PREFIXES = (
    ("flux-", "flux"),
    ("aura-2-", "aura-2"),
    ("aura-", "aura-1"),
)


def deepgram_tts_model_for_voice(voice: str | None) -> str | None:
    """The voice family a Deepgram voice id names, or None for an unknown shape."""
    for prefix, model in _DEEPGRAM_TTS_VOICE_PREFIXES:
        if isinstance(voice, str) and voice.startswith(prefix):
            return model
    return None


DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGES = (
    "de",
    "en",
    "es",
    "fr",
    "hi",
    "it",
    "ja",
    "nl",
    "pt",
    "ru",
)
DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGE_OPTIONS = (
    "multi",
    *DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGES,
)
DEEPGRAM_STT_MODELS = ("nova-3-general", "nova-3-medical", *DEEPGRAM_FLUX_MODELS)
DEEPGRAM_LANGUAGES = (
    "multi",
    "ar",
    "ar-AE",
    "ar-SA",
    "ar-QA",
    "ar-KW",
    "ar-SY",
    "ar-LB",
    "ar-PS",
    "ar-JO",
    "ar-EG",
    "ar-SD",
    "ar-TD",
    "ar-MA",
    "ar-DZ",
    "ar-TN",
    "ar-IQ",
    "ar-IR",
    "be",
    "bn",
    "bs",
    "bg",
    "ca",
    "cs",
    "da",
    "da-DK",
    "de",
    "de-CH",
    "el",
    "en",
    "en-US",
    "en-AU",
    "en-GB",
    "en-IN",
    "en-NZ",
    "es",
    "es-419",
    "et",
    "fa",
    "fi",
    "fr",
    "fr-CA",
    "he",
    "hi",
    "hr",
    "hu",
    "id",
    "it",
    "ja",
    "kn",
    "ko",
    "ko-KR",
    "lt",
    "lv",
    "mk",
    "mr",
    "ms",
    "nl",
    "nl-BE",
    "no",
    "pl",
    "pt",
    "pt-BR",
    "pt-PT",
    "ro",
    "ru",
    "sk",
    "sl",
    "sr",
    "sv",
    "sv-SE",
    "ta",
    "te",
    "th",
    "tl",
    "tr",
    "uk",
    "ur",
    "vi",
    "zh-CN",
    "zh-TW",
)
