"""Speechmatics STT provider with WebSocket streaming."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from ..audio_streamer import AudioConfig, AudioStreamer
from .base import EventCallback, STTProvider, TranscriptionResult, Word

try:
    from websockets.asyncio.client import connect as websocket_connect
except ImportError:
    raise ImportError("websockets required: pip install websockets")


class SpeechmaticsProvider(STTProvider):
    """Speechmatics Speech-to-Text provider with WebSocket streaming.

    API Docs: https://docs.speechmatics.com/

    Supports:
    - Speaker diarization via `diarization: "speaker"` config
    - Speaker sensitivity tuning
    - Real-time streaming via WebSocket
    """

    def __init__(self, api_key: str | None = None, region: str = "eu2"):
        self.api_key = api_key or os.getenv("SPEECHMATICS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Speechmatics API key required. Set SPEECHMATICS_API_KEY env var or pass api_key."
            )
        # Set region-specific endpoint
        self.ws_url = f"wss://{region}.rt.speechmatics.com/v2"

    @property
    def name(self) -> str:
        return "speechmatics"

    async def transcribe(
        self,
        audio_path: Path,
        diarize: bool = False,
        keyterms: list[str] | None = None,
        on_event: EventCallback | None = None,
        language: str = "en",
        operating_point: str = "enhanced",
        sample_rate: int = 8000,
        speaker_sensitivity: float | None = None,
        max_speakers: int | None = None,
        trailing_silence_seconds: float = 3.0,
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Transcribe audio using Speechmatics WebSocket streaming.

        Args:
            audio_path: Path to audio file
            diarize: Enable speaker diarization
            keyterms: Additional vocabulary (limited support)
            on_event: Optional callback for raw WebSocket events
            language: Language code
            operating_point: "standard" or "enhanced"
            sample_rate: Audio sample rate for streaming
            speaker_sensitivity: 0.0-1.0, higher = more speakers detected
            max_speakers: Maximum number of speakers to detect
            trailing_silence_seconds: Seconds of silence after audio to capture pending events
            **kwargs: Additional config parameters

        Returns:
            TranscriptionResult with transcript and speaker info
        """
        # Build transcription config for StartRecognition message
        transcription_config: dict[str, Any] = {
            "language": language,
            "operating_point": operating_point,
            "enable_partials": False,
        }

        if diarize:
            transcription_config["diarization"] = "speaker"
            if speaker_sensitivity is not None:
                transcription_config["speaker_diarization_config"] = {
                    "speaker_sensitivity": speaker_sensitivity
                }
            if max_speakers is not None:
                if "speaker_diarization_config" not in transcription_config:
                    transcription_config["speaker_diarization_config"] = {}
                transcription_config["speaker_diarization_config"]["max_speakers"] = max_speakers

        # Add additional vocabulary if provided
        if keyterms:
            transcription_config["additional_vocab"] = [{"content": term} for term in keyterms]

        # Audio format config
        audio_format = {
            "type": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": sample_rate,
        }

        # Store params for result
        params = {
            "diarize": diarize,
            "language": language,
            "operating_point": operating_point,
            "sample_rate": sample_rate,
            "speaker_sensitivity": speaker_sensitivity,
            "max_speakers": max_speakers,
        }

        # Setup audio streamer
        audio_config = AudioConfig(sample_rate=sample_rate)
        streamer = AudioStreamer(audio_config)

        # Collect results
        all_results: list[dict[str, Any]] = []
        recognition_started = asyncio.Event()
        transcription_complete = asyncio.Event()

        async with websocket_connect(
            self.ws_url,
            additional_headers={"Authorization": f"Bearer {self.api_key}"},
        ) as ws:
            # Send StartRecognition message
            start_msg = {
                "message": "StartRecognition",
                "transcription_config": transcription_config,
                "audio_format": audio_format,
            }
            await ws.send(json.dumps(start_msg))

            async def send_audio():
                """Send audio chunks after recognition starts."""
                await recognition_started.wait()

                chunk_no = 0
                async for chunk in streamer.stream_file(
                    audio_path, trailing_silence_seconds=trailing_silence_seconds
                ):
                    logger.debug(f"[speechmatics] Sent audio chunk {chunk_no}")
                    await ws.send(chunk)
                    chunk_no += 1

                # Signal end of audio with last sequence number
                logger.debug(f"[speechmatics] Sending EndOfStream after {chunk_no} chunks")
                await ws.send(json.dumps({"message": "EndOfStream", "last_seq_no": chunk_no}))

            async def receive_messages():
                """Receive and process messages."""
                nonlocal all_results

                async for message in ws:
                    if isinstance(message, str):
                        data = json.loads(message)
                        msg_type = data.get("message")
                        logger.debug(f"[speechmatics] Received {msg_type}: {data}")

                        # Emit event via callback if provided
                        if on_event and msg_type:
                            on_event(msg_type, data)

                        if msg_type == "RecognitionStarted":
                            logger.info("[speechmatics] Connected")
                            recognition_started.set()

                        elif msg_type == "AddTranscript":
                            # Final transcript segment
                            results = data.get("results", [])
                            all_results.extend(results)

                        elif msg_type == "EndOfTranscript":
                            transcription_complete.set()
                            return

                        elif msg_type == "Error":
                            raise Exception(f"Speechmatics error: {data}")

                        elif msg_type == "Warning":
                            logger.warning(f"[speechmatics] Warning: {data.get('reason')}")

            # Run send and receive concurrently
            send_task = asyncio.create_task(send_audio())
            receive_task = asyncio.create_task(receive_messages())

            # Wait for completion
            await send_task
            try:
                await asyncio.wait_for(transcription_complete.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                pass

            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass

        return self._parse_results(all_results, params)

    def _parse_results(
        self,
        results: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> TranscriptionResult:
        """Parse Speechmatics results."""
        words = []
        speakers_set: set[str] = set()
        transcript_parts = []
        duration = 0.0

        for item in results:
            item_type = item.get("type")
            alternatives = item.get("alternatives", [])

            if not alternatives:
                continue

            alt = alternatives[0]
            content = alt.get("content", "")
            speaker = alt.get("speaker")

            if speaker:
                speakers_set.add(speaker)

            end_time = item.get("end_time", 0.0)
            duration = max(duration, end_time)

            if item_type == "word":
                words.append(
                    Word(
                        word=content,
                        start=item.get("start_time", 0.0),
                        end=end_time,
                        confidence=alt.get("confidence", 0.0),
                        speaker=speaker,
                        speaker_confidence=None,
                    )
                )
                transcript_parts.append(content)
            elif item_type == "punctuation":
                if transcript_parts:
                    transcript_parts[-1] += content

        transcript = " ".join(transcript_parts)

        return TranscriptionResult(
            provider=self.name,
            transcript=transcript,
            words=words,
            speakers=sorted(speakers_set),
            duration=duration,
            raw_response={"results": results},
            params=params,
        )
