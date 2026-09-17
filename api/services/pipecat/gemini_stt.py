"""Dograh configuration for Gemini Live transcription."""

from typing import Literal

from google.genai.types import AudioTranscriptionConfig, LiveConnectConfig, Modality
from pipecat.services.google.gemini_live.stt import GeminiSTTService

GeminiTranscriptionMode = Literal["VERBATIM", "SMART"]


class DograhGeminiSTTService(GeminiSTTService):
    """Gemini STT using the current public Live transcription configuration."""

    def __init__(
        self,
        *,
        transcription_mode: GeminiTranscriptionMode = "VERBATIM",
        custom_vocabulary: list[str] | None = None,
        **kwargs,
    ):
        self._transcription_mode = transcription_mode
        self._custom_vocabulary = list(custom_vocabulary or [])
        super().__init__(**kwargs)

    def _build_live_config(self) -> LiveConnectConfig:
        transcription_kwargs = {
            "language_codes": self._get_language_codes(),
            "mode": self._transcription_mode,
        }
        if self._custom_vocabulary:
            transcription_kwargs["custom_vocabulary"] = self._custom_vocabulary

        return LiveConnectConfig(
            response_modalities=[Modality.TEXT],
            input_audio_transcription=AudioTranscriptionConfig(**transcription_kwargs),
        )
