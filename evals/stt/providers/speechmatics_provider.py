"""Speechmatics STT provider."""

import os
from pathlib import Path
from typing import Any

import httpx

from .base import STTProvider, TranscriptionResult, Word


class SpeechmaticsProvider(STTProvider):
    """Speechmatics Speech-to-Text provider.

    API Docs: https://docs.speechmatics.com/

    Supports:
    - Speaker diarization via `diarization: "speaker"` config
    - Speaker sensitivity tuning
    """

    # EU and US endpoints available
    API_URL = "https://asr.api.speechmatics.com/v2/jobs"

    def __init__(self, api_key: str | None = None, region: str = "eu1"):
        self.api_key = api_key or os.getenv("SPEECHMATICS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Speechmatics API key required. Set SPEECHMATICS_API_KEY env var or pass api_key."
            )
        # Set region-specific endpoint
        if region == "eu1":
            self.api_url = "https://eu1.asr.api.speechmatics.com/v2/jobs"
        else:
            self.api_url = "https://asr.api.speechmatics.com/v2/jobs"

    @property
    def name(self) -> str:
        return "speechmatics"

    async def transcribe(
        self,
        audio_path: Path,
        diarize: bool = False,
        keyterms: list[str] | None = None,
        language: str = "en",
        operating_point: str = "enhanced",
        speaker_sensitivity: float | None = None,
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Transcribe audio using Speechmatics API.

        Args:
            audio_path: Path to audio file
            diarize: Enable speaker diarization
            keyterms: Not directly supported by Speechmatics (ignored)
            language: Language code
            operating_point: "standard" or "enhanced"
            speaker_sensitivity: 0.0-1.0, higher = more speakers detected
            **kwargs: Additional config parameters

        Returns:
            TranscriptionResult with transcript and speaker info
        """
        # Build transcription config
        transcription_config: dict[str, Any] = {
            "language": language,
            "operating_point": operating_point,
        }

        if diarize:
            transcription_config["diarization"] = "speaker"
            if speaker_sensitivity is not None:
                transcription_config["speaker_diarization_config"] = {
                    "speaker_sensitivity": speaker_sensitivity
                }

        # Add any extra config
        transcription_config.update(kwargs)

        config = {
            "type": "transcription",
            "transcription_config": transcription_config,
        }

        # Store params for result
        params = {
            "diarize": diarize,
            "language": language,
            "operating_point": operating_point,
            "speaker_sensitivity": speaker_sensitivity,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }

        # Create job with multipart form
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Submit job
            with open(audio_path, "rb") as f:
                files = {
                    "data_file": (audio_path.name, f, "audio/mpeg"),
                    "config": (None, str(config).replace("'", '"'), "application/json"),
                }
                response = await client.post(
                    self.api_url,
                    headers=headers,
                    files=files,
                )
                response.raise_for_status()
                job_data = response.json()

            job_id = job_data.get("id")
            if not job_id:
                raise ValueError(f"No job ID in response: {job_data}")

            # Poll for completion
            result_data = await self._wait_for_job(client, job_id, headers)

        return self._parse_response(result_data, params)

    async def _wait_for_job(
        self, client: httpx.AsyncClient, job_id: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Poll job status until complete."""
        import asyncio

        job_url = f"{self.api_url}/{job_id}"
        transcript_url = f"{job_url}/transcript?format=json-v2"

        max_attempts = 120  # 10 minutes with 5s intervals
        for _ in range(max_attempts):
            # Check job status
            status_response = await client.get(job_url, headers=headers)
            status_response.raise_for_status()
            status_data = status_response.json()

            job_status = status_data.get("job", {}).get("status")

            if job_status == "done":
                # Get transcript
                transcript_response = await client.get(transcript_url, headers=headers)
                transcript_response.raise_for_status()
                return transcript_response.json()
            elif job_status == "rejected":
                raise ValueError(f"Job rejected: {status_data}")
            elif job_status == "deleted":
                raise ValueError(f"Job deleted: {status_data}")

            await asyncio.sleep(5)

        raise TimeoutError(f"Job {job_id} did not complete in time")

    def _parse_response(
        self, data: dict[str, Any], params: dict[str, Any]
    ) -> TranscriptionResult:
        """Parse Speechmatics API response."""
        results = data.get("results", [])

        words = []
        speakers_set: set[str] = set()
        transcript_parts = []

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

            if item_type == "word":
                words.append(
                    Word(
                        word=content,
                        start=item.get("start_time", 0.0),
                        end=item.get("end_time", 0.0),
                        confidence=alt.get("confidence", 0.0),
                        speaker=speaker,
                        speaker_confidence=None,  # Not provided by Speechmatics
                    )
                )
                transcript_parts.append(content)
            elif item_type == "punctuation":
                # Append punctuation to last word in transcript
                if transcript_parts:
                    transcript_parts[-1] += content

        # Get metadata
        metadata = data.get("metadata", {})
        duration = metadata.get("duration", 0.0)

        transcript = " ".join(transcript_parts)

        return TranscriptionResult(
            provider=self.name,
            transcript=transcript,
            words=words,
            speakers=sorted(speakers_set),
            duration=duration,
            raw_response=data,
            params=params,
        )
