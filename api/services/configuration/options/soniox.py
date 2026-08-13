"""Soniox real-time STT options (models and language hints).

Soniox exposes a single real-time transcription API over WebSocket that supports
60+ languages with automatic language identification. See
https://soniox.com/docs/stt/rt/real-time-transcription for the model list and
language coverage.
"""

# Real-time (rt) models only — Dograh transcribes live call audio, so the async
# models are intentionally excluded. stt-rt-v5 is the recommended current model.
SONIOX_STT_MODELS = (
    "stt-rt-v5",
    "stt-rt-preview",
    "stt-rt-v4",
    "stt-rt-v3",
)

# "auto" enables Soniox's automatic language identification (no language hint).
# The remaining entries are language *hints* (ISO 639-1). Soniox supports many
# more codes than listed here; this is a curated set covering Dograh's common
# languages plus South Asian languages. Any code Soniox accepts can be used.
SONIOX_STT_LANGUAGES = (
    "auto",
    "bn",  # Bengali / Bangla
    "en",
    "hi",
    "ur",
    "ta",
    "te",
    "ar",
    "es",
    "fr",
    "de",
    "pt",
    "it",
    "nl",
    "ru",
    "tr",
    "zh",
    "ja",
    "ko",
    "id",
    "vi",
)
