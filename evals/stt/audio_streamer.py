"""Audio file streamer - converts audio files to PCM16 streams."""

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator


@dataclass
class AudioConfig:
    """Audio streaming configuration."""

    sample_rate: int = 8000
    channels: int = 1
    sample_width: int = 2  # 16-bit = 2 bytes
    chunk_duration_ms: int = 80  # Send chunks every 80ms

    @property
    def chunk_size(self) -> int:
        """Bytes per chunk based on duration."""
        samples_per_chunk = int(self.sample_rate * self.chunk_duration_ms / 1000)
        return samples_per_chunk * self.channels * self.sample_width


class AudioStreamer:
    """Streams audio files as PCM16 chunks.

    Converts any audio format to raw PCM16 using ffmpeg and streams
    in real-time chunks to simulate live audio.
    """

    def __init__(self, config: AudioConfig | None = None):
        self.config = config or AudioConfig()

    def convert_to_pcm16(self, audio_path: Path) -> bytes:
        """Convert audio file to raw PCM16 bytes using ffmpeg.

        Args:
            audio_path: Path to input audio file

        Returns:
            Raw PCM16 audio bytes
        """
        cmd = [
            "ffmpeg",
            "-i",
            str(audio_path),
            "-f",
            "s16le",  # signed 16-bit little-endian
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(self.config.sample_rate),
            "-ac",
            str(self.config.channels),
            "-",  # output to stdout
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
        )
        return result.stdout

    async def stream_file(
        self,
        audio_path: Path,
        realtime: bool = True,
    ) -> AsyncIterator[bytes]:
        """Stream audio file as PCM16 chunks.

        Args:
            audio_path: Path to audio file
            realtime: If True, add delays to simulate real-time streaming

        Yields:
            PCM16 audio chunks
        """
        # Convert entire file to PCM16
        pcm_data = self.convert_to_pcm16(audio_path)

        chunk_size = self.config.chunk_size
        delay = self.config.chunk_duration_ms / 1000.0 if realtime else 0

        # Stream in chunks
        for i in range(0, len(pcm_data), chunk_size):
            chunk = pcm_data[i : i + chunk_size]
            if chunk:
                yield chunk
                if realtime and delay > 0:
                    await asyncio.sleep(delay)

    async def stream_file_fast(self, audio_path: Path) -> AsyncIterator[bytes]:
        """Stream audio file as fast as possible (no real-time delay).

        Args:
            audio_path: Path to audio file

        Yields:
            PCM16 audio chunks
        """
        async for chunk in self.stream_file(audio_path, realtime=False):
            yield chunk

    def get_duration(self, audio_path: Path) -> float:
        """Get audio file duration in seconds.

        Args:
            audio_path: Path to audio file

        Returns:
            Duration in seconds
        """
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
