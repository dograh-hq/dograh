SIXTYDB_API_BASE_URL = "https://api.60db.ai"
SIXTYDB_LLM_BASE_URL = "https://api.60db.ai/v1"

SIXTYDB_LLM_MODELS = ("60db-tiny",)
SIXTYDB_TTS_DEFAULT_VOICE = "84982e79-c596-4883-b6fa-9af334767aef"
SIXTYDB_STT_MODELS = ("60db-stt-v01",)

# The language catalog is served by GET /stt/languages. Keep the list aligned
# with the documented v1 set so the configuration UI does not offer codes the
# streaming endpoint rejects.
SIXTYDB_STT_LANGUAGES = (
    "auto",
    "as",
    "bn",
    "gu",
    "hi",
    "kn",
    "ml",
    "mr",
    "ne",
    "or",
    "pa",
    "ta",
    "te",
    "ar",
    "bg",
    "cs",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "fi",
    "fr",
    "he",
    "hr",
    "hu",
    "it",
    "ja",
    "ko",
    "lt",
    "lv",
    "mt",
    "nb",
    "nl",
    "pl",
    "pt",
    "ro",
    "ru",
    "sk",
    "sl",
    "sv",
    "tr",
    "uk",
    "vi",
    "zh",
)
