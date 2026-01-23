"""Base classes for STT providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Event callback type: (event_type, data) -> None
EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class Word:
    """Represents a transcribed word with metadata."""

    word: str
    start: float
    end: float
    confidence: float
    speaker: str | None = None
    speaker_confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence,
            "speaker": self.speaker,
            "speaker_confidence": self.speaker_confidence,
        }


@dataclass
class TranscriptionResult:
    """Result from STT transcription."""

    provider: str
    transcript: str
    words: list[Word]
    speakers: list[str]
    duration: float
    raw_response: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "transcript": self.transcript,
            "words": [w.to_dict() for w in self.words],
            "speakers": self.speakers,
            "duration": self.duration,
            "params": self.params,
        }

    def get_speaker_segments(self) -> list[dict[str, Any]]:
        """Get transcript segmented by speaker."""
        if not self.words:
            return []

        segments = []
        current_speaker = None
        current_text = []
        segment_start = 0.0

        for word in self.words:
            if word.speaker != current_speaker:
                if current_text:
                    segments.append(
                        {
                            "speaker": current_speaker,
                            "text": " ".join(current_text),
                            "start": segment_start,
                            "end": self.words[len(segments) - 1].end
                            if segments
                            else word.start,
                        }
                    )
                current_speaker = word.speaker
                current_text = [word.word]
                segment_start = word.start
            else:
                current_text.append(word.word)

        if current_text:
            segments.append(
                {
                    "speaker": current_speaker,
                    "text": " ".join(current_text),
                    "start": segment_start,
                    "end": self.words[-1].end if self.words else 0.0,
                }
            )

        return segments


class STTProvider(ABC):
    """Abstract base class for STT providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""
        pass

    @abstractmethod
    async def transcribe(
        self,
        audio_path: Path,
        diarize: bool = False,
        keyterms: list[str] | None = None,
        on_event: EventCallback | None = None,
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Transcribe audio file.

        Args:
            audio_path: Path to the audio file
            diarize: Enable speaker diarization
            keyterms: List of keywords to boost (if supported)
            on_event: Optional callback for raw WebSocket events (event_type, data)
            **kwargs: Provider-specific parameters

        Returns:
            TranscriptionResult with transcript and metadata
        """
        pass
