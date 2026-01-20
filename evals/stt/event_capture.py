#!/usr/bin/env python3
"""STT Event Capture Runner.

Streams audio to STT providers and captures raw WebSocket events with timestamps
for visualization in the web UI.

Usage:
    python -m evals.stt.event_capture audio/multi_speaker.m4a --provider deepgram
    python -m evals.stt.event_capture audio/multi_speaker.m4a --provider speechmatics
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from evals.stt.audio_streamer import AudioStreamer
from evals.stt.providers import (
    DeepgramFluxProvider,
    DeepgramProvider,
    SpeechmaticsProvider,
    STTProvider,
)


@dataclass
class CapturedEvent:
    """A captured WebSocket event with timestamp."""

    timestamp: float  # Time since stream start (seconds)
    event_type: str  # e.g., "Results", "TurnInfo", "AddTranscript"
    data: dict[str, Any]  # Raw event payload

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "data": self.data,
        }


@dataclass
class EventCaptureResult:
    """Result from event capture session."""

    audio_file: str
    audio_path: str  # Relative path to audio from results dir
    provider: str
    duration: float
    created_at: str
    events: list[CapturedEvent] = field(default_factory=list)
    transcript: str = ""  # Final transcript for reference

    def to_dict(self) -> dict[str, Any]:
        return {
            "audio_file": self.audio_file,
            "audio_path": self.audio_path,
            "provider": self.provider,
            "duration": self.duration,
            "created_at": self.created_at,
            "events": [e.to_dict() for e in self.events],
            "transcript": self.transcript,
        }


EventCallback = Callable[[str, dict[str, Any]], None]


def get_provider(name: str) -> STTProvider:
    """Get provider instance by name."""
    providers = {
        "deepgram": DeepgramProvider,
        "deepgram-flux": DeepgramFluxProvider,
        "speechmatics": SpeechmaticsProvider,
    }
    if name not in providers:
        raise ValueError(f"Unknown provider: {name}. Available: {list(providers.keys())}")
    return providers[name]()


async def capture_events(
    provider: STTProvider,
    audio_path: Path,
    sample_rate: int = 8000,
    **kwargs: Any,
) -> EventCaptureResult:
    """Capture WebSocket events from a provider.

    Args:
        provider: The STT provider to use
        audio_path: Path to the audio file
        sample_rate: Audio sample rate
        **kwargs: Additional provider parameters

    Returns:
        EventCaptureResult with all captured events
    """
    # Get audio duration
    streamer = AudioStreamer()
    duration = streamer.get_duration(audio_path)

    # Event list and start time
    events: list[CapturedEvent] = []
    start_time: float | None = None

    def on_event(event_type: str, data: dict[str, Any]) -> None:
        """Callback for capturing events."""
        nonlocal start_time
        if start_time is None:
            start_time = asyncio.get_event_loop().time()

        timestamp = asyncio.get_event_loop().time() - start_time
        events.append(CapturedEvent(timestamp=timestamp, event_type=event_type, data=data))

    # Run transcription with event callback
    result = await provider.transcribe(
        audio_path,
        sample_rate=sample_rate,
        on_event=on_event,
        **kwargs,
    )

    return EventCaptureResult(
        audio_file=audio_path.name,
        audio_path=f"../audio/{audio_path.name}",
        provider=provider.name,
        duration=duration,
        created_at=datetime.now().isoformat(),
        events=events,
        transcript=result.transcript,
    )


def save_result(result: EventCaptureResult, output_dir: Path) -> Path:
    """Save capture result to JSON file.

    Args:
        result: The capture result to save
        output_dir: Directory to save results

    Returns:
        Path to the saved file
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Format: {audio_name}-{provider}.json
    audio_name = Path(result.audio_file).stem
    output_file = output_dir / f"{audio_name}-{result.provider}.json"

    with open(output_file, "w") as f:
        json.dump(result.to_dict(), f, indent=2)

    return output_file


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="STT Event Capture - Capture WebSocket events for visualization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m evals.stt.event_capture audio/multi_speaker.m4a --provider deepgram
  python -m evals.stt.event_capture audio/multi_speaker.m4a --provider speechmatics --diarize
        """,
    )
    parser.add_argument(
        "audio_file",
        type=str,
        help="Path to audio file (relative to evals/stt/ or absolute)",
    )
    parser.add_argument(
        "--provider",
        required=True,
        choices=["deepgram", "deepgram-flux", "speechmatics"],
        help="STT provider to use",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=8000,
        help="Audio sample rate for streaming (default: 8000)",
    )
    parser.add_argument(
        "--diarize",
        action="store_true",
        help="Enable speaker diarization",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results",
        help="Output directory for results (default: results)",
    )

    args = parser.parse_args()

    # Resolve audio path
    script_dir = Path(__file__).parent
    audio_path = Path(args.audio_file)
    if not audio_path.is_absolute():
        audio_path = script_dir / audio_path

    if not audio_path.exists():
        print(f"Error: Audio file not found: {audio_path}")
        return 1

    print(f"Audio file: {audio_path}")
    print(f"Provider: {args.provider}")
    print(f"Sample rate: {args.sample_rate} Hz")
    print(f"Diarization: {args.diarize}")

    try:
        provider = get_provider(args.provider)
        print(f"\nCapturing events from {provider.name}...")

        result = await capture_events(
            provider,
            audio_path,
            sample_rate=args.sample_rate,
            diarize=args.diarize,
        )

        output_dir = script_dir / args.output_dir
        output_file = save_result(result, output_dir)

        print(f"\nCapture complete!")
        print(f"  Duration: {result.duration:.2f}s")
        print(f"  Events: {len(result.events)}")
        print(f"  Saved to: {output_file}")

        # Show first few events
        print(f"\nFirst 5 events:")
        for event in result.events[:5]:
            print(f"  [{event.timestamp:.2f}s] {event.event_type}")

        return 0

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
