"""Deepgram STT provider."""

import os
from pathlib import Path
from typing import Any

import httpx

from .base import STTProvider, TranscriptionResult, Word


class DeepgramProvider(STTProvider):
    """Deepgram Speech-to-Text provider.

    API Docs: https://developers.deepgram.com/docs/

    Supports:
    - Speaker diarization via `diarize=true`
    - Keyterm boosting via `keyterm` parameter (Nova-3 and Flux models)
    """

    API_URL = "https://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Deepgram API key required. Set DEEPGRAM_API_KEY env var or pass api_key."
            )

    @property
    def name(self) -> str:
        return "deepgram"

    async def transcribe(
        self,
        audio_path: Path,
        diarize: bool = False,
        keyterms: list[str] | None = None,
        model: str = "nova-3",
        language: str = "en",
        punctuate: bool = True,
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Transcribe audio using Deepgram API.

        Args:
            audio_path: Path to audio file
            diarize: Enable speaker diarization
            keyterms: List of keywords to boost recognition
            model: Deepgram model (nova-3, nova-2, etc.)
            language: Language code
            punctuate: Add punctuation
            **kwargs: Additional Deepgram parameters

        Returns:
            TranscriptionResult with transcript and speaker info
        """
        params: dict[str, Any] = {
            "model": model,
            "language": language,
            "punctuate": str(punctuate).lower(),
        }

        if diarize:
            params["diarize"] = "true"

        # Add keyterms (Deepgram uses repeated keyterm params)
        if keyterms:
            params["keyterm"] = keyterms

        # Add any extra kwargs
        params.update(kwargs)

        # Read audio file
        audio_data = audio_path.read_bytes()

        # Determine content type
        suffix = audio_path.suffix.lower()
        content_types = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".flac": "audio/flac",
            ".ogg": "audio/ogg",
            ".webm": "audio/webm",
        }
        content_type = content_types.get(suffix, "audio/wav")

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": content_type,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                self.API_URL,
                params=params,
                headers=headers,
                content=audio_data,
            )
            response.raise_for_status()
            data = response.json()

        return self._parse_response(data, params)

    def _parse_response(
        self, data: dict[str, Any], params: dict[str, Any]
    ) -> TranscriptionResult:
        """Parse Deepgram API response."""
        results = data.get("results", {})
        channels = results.get("channels", [])

        if not channels:
            return TranscriptionResult(
                provider=self.name,
                transcript="",
                words=[],
                speakers=[],
                duration=0.0,
                raw_response=data,
                params=params,
            )

        # Get first channel, first alternative
        channel = channels[0]
        alternatives = channel.get("alternatives", [])
        if not alternatives:
            return TranscriptionResult(
                provider=self.name,
                transcript="",
                words=[],
                speakers=[],
                duration=0.0,
                raw_response=data,
                params=params,
            )

        alt = alternatives[0]
        transcript = alt.get("transcript", "")

        # Parse words with speaker info
        words = []
        speakers_set: set[str] = set()

        for w in alt.get("words", []):
            speaker = str(w.get("speaker", "")) if "speaker" in w else None
            if speaker:
                speakers_set.add(speaker)

            words.append(
                Word(
                    word=w.get("word", ""),
                    start=w.get("start", 0.0),
                    end=w.get("end", 0.0),
                    confidence=w.get("confidence", 0.0),
                    speaker=speaker,
                    speaker_confidence=w.get("speaker_confidence"),
                )
            )

        # Get duration from metadata
        metadata = results.get("metadata", {})
        duration = metadata.get("duration", 0.0)

        return TranscriptionResult(
            provider=self.name,
            transcript=transcript,
            words=words,
            speakers=sorted(speakers_set),
            duration=duration,
            raw_response=data,
            params=params,
        )
