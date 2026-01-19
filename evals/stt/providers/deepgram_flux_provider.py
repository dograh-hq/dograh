"""Deepgram Flux STT provider with WebSocket streaming.

Flux is Deepgram's conversational AI model with built-in turn detection.
It has a different API than Nova models - no language/punctuate/diarize params.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from loguru import logger

from ..audio_streamer import AudioConfig, AudioStreamer
from .base import STTProvider, TranscriptionResult, Word

try:
    from websockets.asyncio.client import connect as websocket_connect
except ImportError:
    raise ImportError("websockets required: pip install websockets")


class DeepgramFluxProvider(STTProvider):
    """Deepgram Flux Speech-to-Text provider with WebSocket streaming.

    Flux is optimized for conversational AI with built-in turn detection.

    Key differences from Nova:
    - Uses v2 API endpoint
    - Only supports English (flux-general-en)
    - No punctuate, diarize, or language params
    - Has turn detection events (StartOfTurn, EndOfTurn, EagerEndOfTurn)
    - Supports keyterm boosting

    API Docs: https://developers.deepgram.com/docs/
    """

    WS_URL = "wss://api.deepgram.com/v2/listen"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Deepgram API key required. Set DEEPGRAM_API_KEY env var or pass api_key."
            )

    @property
    def name(self) -> str:
        return "deepgram-flux"

    async def transcribe(
        self,
        audio_path: Path,
        diarize: bool = False,  # Ignored - Flux doesn't support diarization
        keyterms: list[str] | None = None,
        model: str = "flux-general-en",
        sample_rate: int = 16000,
        eot_threshold: float | None = None,
        eot_timeout_ms: int | None = None,
        eager_eot_threshold: float | None = None,
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Transcribe audio using Deepgram Flux WebSocket streaming.

        Args:
            audio_path: Path to audio file
            diarize: IGNORED - Flux does not support diarization
            keyterms: List of keywords to boost recognition
            model: Flux model (default: flux-general-en)
            sample_rate: Audio sample rate (default: 16000 for Flux)
            eot_threshold: End-of-turn confidence threshold (0-1, default 0.7)
            eot_timeout_ms: Timeout in ms to force end of turn (default 5000)
            eager_eot_threshold: Threshold for eager end-of-turn events
            **kwargs: Additional Flux parameters

        Returns:
            TranscriptionResult with transcript (no speaker info - Flux doesn't support diarization)
        """
        if diarize:
            logger.warning("Flux does not support diarization - ignoring diarize=True")

        # Build query params - Flux only supports specific params
        params: dict[str, Any] = {
            "model": model,
            "encoding": "linear16",
            "sample_rate": sample_rate,
        }

        # Flux-specific turn detection params
        if eot_threshold is not None:
            params["eot_threshold"] = eot_threshold
        if eot_timeout_ms is not None:
            params["eot_timeout_ms"] = eot_timeout_ms
        if eager_eot_threshold is not None:
            params["eager_eot_threshold"] = eager_eot_threshold

        # Build URL with params
        url_parts = [f"{k}={v}" for k, v in params.items()]

        # Add keyterms (repeated params)
        if keyterms:
            for term in keyterms:
                url_parts.append(urlencode({"keyterm": term}))

        ws_url = f"{self.WS_URL}?{'&'.join(url_parts)}"
        logger.debug(f"Flux WebSocket URL: {ws_url}")

        # Setup audio streamer
        audio_config = AudioConfig(sample_rate=sample_rate)
        streamer = AudioStreamer(audio_config)

        # Collect results
        all_transcripts: list[dict[str, Any]] = []
        final_transcript = ""
        duration = 0.0
        connected = asyncio.Event()

        async with websocket_connect(
            ws_url,
            additional_headers={"Authorization": f"Token {self.api_key}"},
        ) as ws:

            async def send_audio():
                """Send audio chunks to Deepgram Flux."""
                await connected.wait()

                chunk_no = 0
                async for chunk in streamer.stream_file(audio_path):
                    logger.debug(f"[deepgram-flux] Sent audio chunk {chunk_no}")
                    await ws.send(chunk)
                    chunk_no += 1

            async def receive_messages():
                """Receive and collect Flux messages."""
                nonlocal all_transcripts, final_transcript, duration

                async for message in ws:
                    if isinstance(message, str):
                        data = json.loads(message)
                        msg_type = data.get("type")
                        logger.debug(f"[deepgram-flux] Received {msg_type}: {data}")

                        if msg_type == "Connected":
                            logger.info("[deepgram-flux] Connected")
                            connected.set()

                        elif msg_type == "TurnInfo":
                            event = data.get("event")
                            transcript = data.get("transcript", "")
                            words = data.get("words", [])

                            if event == "EndOfTurn":
                                if transcript:
                                    final_transcript += transcript + " "
                                if words:
                                    all_transcripts.append({
                                        "transcript": transcript,
                                        "words": words,
                                    })
                                    # Get duration from last word
                                    if words:
                                        last_word = words[-1]
                                        duration = max(duration, last_word.get("end", 0))

                            elif event == "TurnResumed":
                                logger.debug("TurnResumed")

                        elif msg_type == "Error":
                            raise Exception(f"Deepgram Flux error: {data}")

            # Run send and receive concurrently
            send_task = asyncio.create_task(send_audio())
            receive_task = asyncio.create_task(receive_messages())

            await send_task
            
            logger.debug("[deepgram-flux] Send task done")
            try:
                await asyncio.wait_for(receive_task, timeout=10.0)
            except asyncio.TimeoutError:
                pass

        return self._parse_results(
            all_transcripts, final_transcript.strip(), duration, params, keyterms
        )

    def _parse_results(
        self,
        transcripts: list[dict[str, Any]],
        final_transcript: str,
        duration: float,
        params: dict[str, Any],
        keyterms: list[str] | None,
    ) -> TranscriptionResult:
        """Parse collected Flux results into TranscriptionResult."""
        words = []

        for turn in transcripts:
            for w in turn.get("words", []):
                words.append(
                    Word(
                        word=w.get("word", ""),
                        start=w.get("start", 0.0),
                        end=w.get("end", 0.0),
                        confidence=w.get("confidence", 0.0),
                        speaker=None,  # Flux doesn't support diarization
                        speaker_confidence=None,
                    )
                )

        stored_params = dict(params)
        if keyterms:
            stored_params["keyterms"] = keyterms

        return TranscriptionResult(
            provider=self.name,
            transcript=final_transcript,
            words=words,
            speakers=[],  # Flux doesn't support diarization
            duration=duration,
            raw_response={"transcripts": transcripts},
            params=stored_params,
        )
