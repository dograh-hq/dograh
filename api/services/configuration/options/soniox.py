SONIOX_STT_MODELS = ("stt-rt-v5",)

SONIOX_STT_LANGUAGES = (
    "multi",
    "af",
    "ar",
    "az",
    "be",
    "bg",
    "bn",
    "bs",
    "ca",
    "cs",
    "cy",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "eu",
    "fa",
    "fi",
    "fr",
    "gl",
    "gu",
    "he",
    "hi",
    "hr",
    "hu",
    "id",
    "it",
    "ja",
    "kk",
    "kn",
    "ko",
    "lt",
    "lv",
    "mk",
    "ml",
    "mr",
    "ms",
    "nl",
    "no",
    "pa",
    "pl",
    "pt",
    "ro",
    "ru",
    "sk",
    "sl",
    "sq",
    "sr",
    "sv",
    "sw",
    "ta",
    "te",
    "th",
    "tl",
    "tr",
    "uk",
    "ur",
    "vi",
    "zh",
)

# Soniox runs each region on its own hosts, and an API key works only in the
# region of the project that issued it, so the region must match the key.
# https://soniox.com/docs/data-residency
SONIOX_REGION_HOST_SUFFIXES = {
    "us": "",
    "eu": ".eu",
    "jp": ".jp",
    "in": ".in",
}
SONIOX_REGIONS = tuple(SONIOX_REGION_HOST_SUFFIXES)


def soniox_host(service: str, region: str | None) -> str:
    """Return the Soniox host for ``service`` ("api", "stt-rt" or "tts-rt") in ``region``."""
    suffix = SONIOX_REGION_HOST_SUFFIXES.get((region or "us").strip().lower())
    if suffix is None:
        raise ValueError(
            f"Unknown Soniox region {region!r}; expected one of {', '.join(SONIOX_REGIONS)}."
        )
    return f"{service}{suffix}.soniox.com"


SONIOX_TTS_MODELS = ("tts-rt-v2", "tts-rt-v1")

# Every Soniox voice speaks every supported language. A selection of the shared
# voices returned by GET /v1/tts-models; any other voice id or cloned-voice UUID
# can be typed in.
SONIOX_TTS_VOICES = (
    "Daniel",
    "Nina",
    "Emma",
    "Adrian",
    "Grace",
    "Owen",
    "Mina",
    "Oliver",
    "Ruby",
    "Priya",
    "Arjun",
    "Karan",
    "Kavya",
    "Ishita",
    "Nisha",
    "Madhura",
)

# https://soniox.com/docs/tts/concepts/languages
SONIOX_TTS_LANGUAGES = tuple(code for code in SONIOX_STT_LANGUAGES if code != "multi")
