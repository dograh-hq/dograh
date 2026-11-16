"""Gnani Prisma and Timbre choices and save-time checks."""

from gnani.stt._prisma import (
    INDIC_AUTO_LANGUAGE_CODE,
    parse_candidates,
    parse_keywords,
    require_prisma_v3,
)
from gnani.stt.client import STREAM_SUPPORTED_LANGUAGES, _validate_language_code
from gnani.tts import SUPPORTED_TTS_LANGUAGES, TIMBRE_V25_VOICES, TIMBRE_V30_VOICES

# The picker selects the first voice after a model change.
GNANI_TTS_VOICES = {
    "timbre-v2.5": ["Pranav", *sorted(TIMBRE_V25_VOICES - {"Pranav"})],
    "timbre-v3.0": ["Avinash", *sorted(TIMBRE_V30_VOICES - {"Avinash"})],
}
GNANI_TTS_LANGUAGES = ["auto", *sorted(SUPPORTED_TTS_LANGUAGES - {"auto"})]

GNANI_STT_MODELS = ("prisma-v2.5", "prisma-v3.0")
GNANI_STT_LANGUAGES = (
    "hi-IN",
    "en-IN",
    "bn-IN",
    "gu-IN",
    "kn-IN",
    "ml-IN",
    "mr-IN",
    "pa-IN",
    "ta-IN",
    "te-IN",
)
GNANI_STT_LANGUAGES_V3 = GNANI_STT_LANGUAGES + (INDIC_AUTO_LANGUAGE_CODE,)


def validate_gnani_stt_settings(
    model: str,
    language: str,
    language_candidates: list[str] | None,
    keywords: list[str] | None,
) -> None:
    """Reject combinations the streaming Prisma API will not accept."""
    if model not in GNANI_STT_MODELS:
        raise ValueError(
            f"Unsupported Gnani STT model {model!r}. Choose prisma-v2.5 or prisma-v3.0."
        )
    _validate_language_code(language, model, STREAM_SUPPORTED_LANGUAGES)
    if language_candidates:
        require_prisma_v3(model, language_candidates=language_candidates)
        if language != INDIC_AUTO_LANGUAGE_CODE:
            raise ValueError(
                "language_candidates only apply with language="
                f"{INDIC_AUTO_LANGUAGE_CODE!r}"
            )
        parse_candidates(language_candidates, STREAM_SUPPORTED_LANGUAGES)
    if keywords:
        require_prisma_v3(model, keywords=keywords)
        parse_keywords(keywords)
