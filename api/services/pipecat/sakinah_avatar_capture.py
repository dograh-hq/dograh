"""Capture assistant TTS turns for the optional Sakinah video hook."""

from __future__ import annotations

import asyncio
import io
import os
import urllib.error
import urllib.request
import wave

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class SakinahAvatarCaptureProcessor(FrameProcessor):
    """Forward assistant TTS turns without blocking the live audio pipeline."""

    def __init__(
        self,
        *,
        workflow_run_id: str,
        endpoint_url: str,
        hook_secret: str = "",
        timeout_seconds: float = 4.0,
    ):
        super().__init__()
        self._workflow_run_id = str(workflow_run_id)
        self._endpoint_url = endpoint_url.rstrip("/")
        self._hook_secret = hook_secret
        self._timeout_seconds = timeout_seconds
        self._turn_index = 0
        self._chunks: list[bytes] = []
        self._sample_rate: int | None = None
        self._channels: int | None = None
        self._tasks: set[asyncio.Task] = set()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, TTSStartedFrame):
                self._turn_index += 1
                self._chunks = []
                self._sample_rate = None
                self._channels = None
            elif isinstance(frame, TTSAudioRawFrame):
                audio = getattr(frame, "audio", None)
                if audio:
                    self._chunks.append(bytes(audio))
                if self._sample_rate is None:
                    self._sample_rate = int(getattr(frame, "sample_rate", 0) or 0)
                if self._channels is None:
                    self._channels = int(getattr(frame, "num_channels", 0) or 0)
            elif isinstance(frame, TTSStoppedFrame):
                if self._chunks:
                    wav_bytes = self._make_wav(
                        b"".join(self._chunks),
                        sample_rate=self._sample_rate or 16000,
                        channels=self._channels or 1,
                    )
                    task = asyncio.create_task(
                        self._post_turn(self._turn_index, wav_bytes)
                    )
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                self._chunks = []
                self._sample_rate = None
                self._channels = None

        await self.push_frame(frame, direction)

    @staticmethod
    def _make_wav(pcm: bytes, *, sample_rate: int, channels: int) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(max(1, channels))
            wav_file.setsampwidth(2)
            wav_file.setframerate(max(8000, sample_rate))
            wav_file.writeframes(pcm)
        return output.getvalue()

    async def _post_turn(self, turn_index: int, wav_bytes: bytes) -> None:
        try:
            await asyncio.to_thread(self._post_turn_sync, turn_index, wav_bytes)
        except Exception as exc:  # noqa: BLE001 - optional hook is best effort
            logger.warning(
                "Sakinah avatar capture failed run={} turn={}: {}",
                self._workflow_run_id,
                turn_index,
                type(exc).__name__,
            )

    def _post_turn_sync(self, turn_index: int, wav_bytes: bytes) -> None:
        boundary = "----SakinahDograhBoundary"
        body = self._multipart_body(
            boundary, self._workflow_run_id, turn_index, wav_bytes
        )
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        if self._hook_secret:
            headers["X-Sakinah-Hook-Secret"] = self._hook_secret
        request = urllib.request.Request(
            self._endpoint_url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                if getattr(response, "status", 200) >= 300:
                    raise RuntimeError(f"HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc

    @staticmethod
    def _multipart_body(
        boundary: str, run_id: str, turn_index: int, wav_bytes: bytes
    ) -> bytes:
        delimiter = boundary.encode()
        parts: list[bytes] = []

        def field(name: str, value: object) -> None:
            parts.extend(
                [
                    b"--" + delimiter,
                    f'Content-Disposition: form-data; name="{name}"'.encode(),
                    b"",
                    str(value).encode(),
                ]
            )

        field("run_id", run_id)
        field("turn_index", turn_index)
        parts.extend(
            [
                b"--" + delimiter,
                b'Content-Disposition: form-data; name="audio"; filename="assistant-turn.wav"',
                b"Content-Type: audio/wav",
                b"",
                wav_bytes,
                b"--" + delimiter + b"--",
                b"",
            ]
        )
        return b"\r\n".join(parts)


def create_sakinah_avatar_capture(workflow_run_id: str):
    if os.getenv("SAKINAH_AVATAR_CAPTURE_ENABLED", "false").lower() != "true":
        return None
    endpoint = os.getenv("SAKINAH_VIDEO_HOOK_URL", "").strip()
    if not endpoint:
        logger.error(
            "SAKINAH_AVATAR_CAPTURE_ENABLED=true but SAKINAH_VIDEO_HOOK_URL is empty"
        )
        return None
    return SakinahAvatarCaptureProcessor(
        workflow_run_id=workflow_run_id,
        endpoint_url=endpoint,
        hook_secret=os.getenv("SAKINAH_VIDEO_HOOK_SECRET", ""),
        timeout_seconds=float(os.getenv("SAKINAH_VIDEO_HOOK_TIMEOUT", "4.0")),
    )
