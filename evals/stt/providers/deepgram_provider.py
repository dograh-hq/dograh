"""Deepgram STT provider with WebSocket streaming."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..audio_streamer import AudioConfig, AudioStreamer
from .base import EventCallback, STTProvider, TranscriptionResult, Word
from loguru import logger

try:
    from websockets.asyncio.client import connect as websocket_connect
except ImportError:
    raise ImportError("websockets required: pip install websockets")


class DeepgramProvider(STTProvider):
    """Deepgram Nova Speech-to-Text provider with WebSocket streaming.

    API Docs: https://developers.deepgram.com/docs/

    Supports:
    - Speaker diarization via `diarize=true`
    - Keyterm boosting via `keyterm` parameter
    - Real-time streaming via WebSocket
    - Multiple languages
    - Punctuation

    For Flux models, use DeepgramFluxProvider instead.
    """

    WS_URL = "wss://api.deepgram.com/v1/listen"

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
        on_event: EventCallback | None = None,
        model: str = "nova-3-general",
        language: str = "en",
        sample_rate: int = 8000,
        punctuate: bool = True,
        trailing_silence_seconds: float = 3.0,
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Transcribe audio using Deepgram Nova WebSocket streaming.

        Args:
            audio_path: Path to audio file
            diarize: Enable speaker diarization
            keyterms: List of keywords to boost recognition
            on_event: Optional callback for raw WebSocket events
            model: Deepgram Nova model (nova-3, nova-2, etc.)
            language: Language code
            sample_rate: Audio sample rate for streaming
            punctuate: Add punctuation
            trailing_silence_seconds: Seconds of silence after audio to capture pending events
            **kwargs: Additional Deepgram parameters

        Returns:
            TranscriptionResult with transcript and speaker info
        """
        # Build query params
        params: dict[str, Any] = {
            "model": model,
            "language": language,
            "punctuate": str(punctuate).lower(),
            "encoding": "linear16",
            "sample_rate": sample_rate,
            "channels": 1,
            "interim_results": "true",
            "smart_format": "true",
            "profanity_filter": "true",
            "vad_events": "true",
            "utterance_end_ms": "1000"
        }

        if diarize:
            params["diarize"] = "true"

        # Build URL with params
        url_parts = [f"{k}={v}" for k, v in params.items()]

        # Add keyterms (repeated params)
        if keyterms:
            for term in keyterms:
                url_parts.append(urlencode({"keyterm": term}))

        # Add extra kwargs
        for k, v in kwargs.items():
            url_parts.append(f"{k}={v}")

        ws_url = f"{self.WS_URL}?{'&'.join(url_parts)}"
        logger.debug(f"Deepgram WebSocket URL: {ws_url}")

        # Setup audio streamer
        audio_config = AudioConfig(sample_rate=sample_rate)
        streamer = AudioStreamer(audio_config)

        # Collect results
        all_words: list[dict[str, Any]] = []
        final_transcript = ""
        duration = 0.0

        try:
            async with websocket_connect(
                ws_url,
                additional_headers={"Authorization": f"Token {self.api_key}"},
            ) as ws:
                # Create tasks for sending and receiving
                send_complete = asyncio.Event()

                async def send_audio():
                    """Send audio chunks to Deepgram."""
                    chunk_no = 0
                    async for chunk in streamer.stream_file(
                        audio_path, trailing_silence_seconds=trailing_silence_seconds
                    ):
                        logger.trace(f"[deepgram] Sent audio chunk {chunk_no}")
                        await ws.send(chunk)
                        chunk_no += 1
                    # Send close message
                    logger.debug(f"[deepgram] Sending CloseStream after {chunk_no} chunks")
                    await ws.send(json.dumps({"type": "CloseStream"}))
                    send_complete.set()

                async def receive_transcripts():
                    """Receive and collect transcription results."""
                    nonlocal all_words, final_transcript, duration

                    async for message in ws:
                        if isinstance(message, str):
                            data = json.loads(message)
                            msg_type = data.get("type")
                            logger.debug(f"[deepgram] Received {msg_type}: {data}")

                            # Emit event via callback if provided
                            if on_event and msg_type:
                                on_event(msg_type, data)

                            if msg_type == "Results":
                                # Nova-style response
                                channel = data.get("channel", {})
                                alternatives = channel.get("alternatives", [])
                                if alternatives:
                                    alt = alternatives[0]
                                    words = alt.get("words", [])
                                    all_words.extend(words)

                                    # Check if final
                                    if data.get("is_final"):
                                        final_transcript += alt.get("transcript", "") + " "
                                        duration = max(
                                            duration, data.get("duration", 0) + data.get("start", 0)
                                        )

                            elif msg_type == "Metadata":
                                # Get duration from metadata
                                duration = data.get("duration", duration)

                            elif msg_type == "Error":
                                raise Exception(f"Deepgram error: {data}")

                # Run send and receive concurrently
                send_task = asyncio.create_task(send_audio())
                receive_task = asyncio.create_task(receive_transcripts())

                # Wait for send to complete, then wait a bit for final results
                await send_task
                try:
                    await asyncio.wait_for(receive_task, timeout=5.0)
                except asyncio.TimeoutError:
                    pass  # Normal - websocket closes after final results
        except Exception as e:
            logger.exception(e)

        return self._parse_results(
            all_words, final_transcript.strip(), duration, params, keyterms
        )

    def _parse_results(
        self,
        raw_words: list[dict[str, Any]],
        transcript: str,
        duration: float,
        params: dict[str, Any],
        keyterms: list[str] | None,
    ) -> TranscriptionResult:
        """Parse collected results into TranscriptionResult."""
        words = []
        speakers_set: set[str] = set()

        for w in raw_words:
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

        stored_params = dict(params)
        if keyterms:
            stored_params["keyterms"] = keyterms

        return TranscriptionResult(
            provider=self.name,
            transcript=transcript,
            words=words,
            speakers=sorted(speakers_set),
            duration=duration,
            raw_response={"words": raw_words},
            params=stored_params,
        )
