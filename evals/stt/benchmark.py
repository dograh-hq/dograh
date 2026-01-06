#!/usr/bin/env python3
"""STT Benchmark Runner.

Compare speech-to-text transcription across providers with focus on:
- Speaker diarization accuracy
- Keyword/keyterm recognition
- Transcription quality

Usage:
    python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize
    python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize --providers deepgram
    python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize --keyterms "Dograh" "Pipecat"
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from evals.stt.providers import DeepgramProvider, SpeechmaticsProvider, STTProvider, TranscriptionResult


def get_provider(name: str) -> STTProvider:
    """Get provider instance by name."""
    providers = {
        "deepgram": DeepgramProvider,
        "speechmatics": SpeechmaticsProvider,
    }
    if name not in providers:
        raise ValueError(f"Unknown provider: {name}. Available: {list(providers.keys())}")
    return providers[name]()


async def run_transcription(
    provider: STTProvider,
    audio_path: Path,
    diarize: bool = False,
    keyterms: list[str] | None = None,
    **kwargs: Any,
) -> TranscriptionResult:
    """Run transcription with a provider."""
    print(f"\n{'='*60}")
    print(f"Provider: {provider.name.upper()}")
    print(f"{'='*60}")

    try:
        result = await provider.transcribe(
            audio_path,
            diarize=diarize,
            keyterms=keyterms,
            **kwargs,
        )
        return result
    except Exception as e:
        print(f"Error with {provider.name}: {e}")
        raise


def print_result(result: TranscriptionResult, show_words: bool = False) -> None:
    """Print transcription result."""
    print(f"\nDuration: {result.duration:.2f}s")
    print(f"Speakers detected: {len(result.speakers)} - {result.speakers}")
    print(f"\nTranscript:\n{result.transcript}")

    if result.speakers:
        print(f"\n--- Speaker Segments ---")
        for segment in result.get_speaker_segments():
            speaker = segment["speaker"] or "?"
            text = segment["text"]
            start = segment["start"]
            print(f"[{start:.1f}s] Speaker {speaker}: {text}")

    if show_words:
        print(f"\n--- Words ---")
        for word in result.words[:50]:  # First 50 words
            speaker_info = f" (S{word.speaker})" if word.speaker else ""
            print(f"  {word.start:.2f}s: {word.word}{speaker_info} [{word.confidence:.2f}]")
        if len(result.words) > 50:
            print(f"  ... and {len(result.words) - 50} more words")


def save_results(
    results: list[TranscriptionResult],
    output_dir: Path,
    audio_name: str,
) -> Path:
    """Save results to JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"{audio_name}_{timestamp}.json"

    output_data = {
        "timestamp": timestamp,
        "audio_file": audio_name,
        "results": [r.to_dict() for r in results],
    }

    with open(output_file, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\nResults saved to: {output_file}")
    return output_file


def compare_results(results: list[TranscriptionResult]) -> None:
    """Compare results across providers."""
    if len(results) < 2:
        return

    print(f"\n{'='*60}")
    print("COMPARISON SUMMARY")
    print(f"{'='*60}")

    print(f"\n{'Provider':<15} {'Duration':<10} {'Speakers':<10} {'Words':<10}")
    print("-" * 45)
    for r in results:
        print(f"{r.provider:<15} {r.duration:<10.2f} {len(r.speakers):<10} {len(r.words):<10}")

    # Compare speaker counts
    speaker_counts = {r.provider: len(r.speakers) for r in results}
    if len(set(speaker_counts.values())) > 1:
        print(f"\nNote: Providers detected different speaker counts: {speaker_counts}")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="STT Benchmark - Compare transcription providers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize
  python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize --providers deepgram
  python -m evals.stt.benchmark audio/multi_speaker.m4a --keyterms "Dograh" "API"
        """,
    )
    parser.add_argument(
        "audio_file",
        type=str,
        help="Path to audio file (relative to evals/stt/ or absolute)",
    )
    parser.add_argument(
        "--providers",
        nargs="+",
        default=["deepgram", "speechmatics"],
        choices=["deepgram", "speechmatics"],
        help="Providers to test (default: all)",
    )
    parser.add_argument(
        "--diarize",
        action="store_true",
        help="Enable speaker diarization",
    )
    parser.add_argument(
        "--keyterms",
        nargs="+",
        help="Keywords to boost (Deepgram only)",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language code (default: en)",
    )
    parser.add_argument(
        "--show-words",
        action="store_true",
        help="Show individual word timings",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save results to JSON file",
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
    print(f"Providers: {args.providers}")
    print(f"Diarization: {args.diarize}")
    if args.keyterms:
        print(f"Keyterms: {args.keyterms}")

    results: list[TranscriptionResult] = []

    for provider_name in args.providers:
        try:
            provider = get_provider(provider_name)
            result = await run_transcription(
                provider,
                audio_path,
                diarize=args.diarize,
                keyterms=args.keyterms,
                language=args.language,
            )
            print_result(result, show_words=args.show_words)
            results.append(result)
        except Exception as e:
            print(f"\nFailed to run {provider_name}: {e}")
            continue

    if len(results) > 1:
        compare_results(results)

    if args.save and results:
        output_dir = script_dir / args.output_dir
        save_results(results, output_dir, audio_path.stem)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
