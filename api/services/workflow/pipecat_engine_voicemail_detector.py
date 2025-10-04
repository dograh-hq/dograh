from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import wave
from typing import TYPE_CHECKING, Optional

from langfuse import get_client
from loguru import logger
from openai import AsyncOpenAI
from opentelemetry import context as otel_context

from api.db import db_client
from api.services.pipecat.tracing_config import is_tracing_enabled
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames
from pipecat.utils.enums import EndTaskReason
from pipecat.utils.tracing.context_registry import get_current_turn_context

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


DEFAULT_VOICEMAIL_PROMPT = """
You are analyzing the beginning of a phone call to determine if it's a voicemail greeting.

Common voicemail indicators:
- "You've reached the voicemail of..."
- "Please leave a message after the beep"
- "I'm not available right now"
- "Press 1 to leave a message"
- Robotic or pre-recorded voice quality mentioned
- Background music or hold music references

Transcript: {transcript}

Respond with a JSON object:
{
  "is_voicemail": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "Brief explanation"
}
"""


class VoicemailDetector:
    """
    Autonomous voicemail detection system that operates independently of the main pipeline.
    """

    def __init__(self, detection_duration: float = 15.0, workflow_run_id: int = None):
        self.detection_duration = detection_duration
        self.audio_buffer = bytearray()
        self.is_detecting = False
        self.workflow_run_id = workflow_run_id
        self._langfuse_client = get_client()

        # We will set the sample rate when we receive the audio packet
        self._sample_rate = None

        # Task management
        self._detection_task: Optional[asyncio.Task] = None
        self._is_cancelled = False
        self._engine: Optional[PipecatEngine] = None

        # Event for audio collection completion
        self._audio_collected_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _current_duration_seconds(self) -> float:
        """Return the duration (in seconds) of the audio currently in the buffer."""
        if self._sample_rate:
            return len(self.audio_buffer) / (self._sample_rate * 2)
        return 0.0

    async def handle_audio_data(
        self, processor, pcm: bytes, sample_rate: int, num_channels: int
    ):
        """Handle incoming audio data without affecting pipeline."""
        if not self.is_detecting or self._is_cancelled:
            return

        # Store the actual sample rate from the first audio packet
        if self._sample_rate is None:
            self._sample_rate = sample_rate
            logger.debug(f"Voicemail detector using sample rate: {sample_rate}")

        # Add to buffer without resampling
        self.audio_buffer.extend(pcm)

        # Check if we've collected enough audio
        current_duration = self._current_duration_seconds()
        if current_duration >= self.detection_duration:
            self._audio_collected_event.set()

    async def start_detection(self, engine: PipecatEngine):
        """Start voicemail detection process."""
        logger.info("Starting voicemail detection")
        self.is_detecting = True
        self._is_cancelled = False
        self._engine = engine
        self._audio_collected_event.clear()

        # Start detection in background
        self._detection_task = asyncio.create_task(self._run_detection_with_timeout())

    async def stop_detection(self):
        """Stop detection immediately (called on disconnect)."""
        logger.info("Stopping voicemail detection due to disconnect")
        self._is_cancelled = True
        self.is_detecting = False

        # Set the event to unblock any waiting tasks
        self._audio_collected_event.set()

        # Cancel ongoing detection task
        if self._detection_task and not self._detection_task.done():
            self._detection_task.cancel()

        # Clear audio buffer
        self.audio_buffer.clear()

        # Wait for tasks to complete cancellation
        if self._detection_task:
            try:
                await self._detection_task
            except asyncio.CancelledError:
                pass

    async def _run_detection_with_timeout(self):
        """Run detection with proper timeout and cancellation handling."""
        try:
            # Wait for audio collection or cancellation directly
            await self._wait_for_audio_collection()

            # Check if cancelled during collection
            if self._is_cancelled:
                logger.info("Detection cancelled during audio collection")
                return

            # Process detection
            await self._process_detection()

        except asyncio.CancelledError:
            logger.info("Voicemail detection task cancelled")
        except Exception as e:
            logger.error(f"Error in voicemail detection: {e}")
        finally:
            self.is_detecting = False

    async def _wait_for_audio_collection(self):
        """Wait for audio buffer to fill or timeout."""
        try:
            # Wait for either audio collection completion or timeout
            await asyncio.wait_for(
                self._audio_collected_event.wait(),
                timeout=self.detection_duration + 2.0,
            )

            if not self._is_cancelled:
                current_duration = self._current_duration_seconds()
                logger.info(
                    f"Collected {current_duration:.1f}s of audio for voicemail detection (sample rate: {self._sample_rate}Hz)"
                )
        except asyncio.TimeoutError:
            if not self._is_cancelled:
                current_duration = self._current_duration_seconds()
                logger.warning("Audio collection timeout exceeded")
                logger.info(
                    f"Proceeding with {current_duration:.1f}s of audio (sample rate: {self._sample_rate}Hz)"
                )

    async def _process_detection(self):
        """Process the collected audio to detect voicemail."""
        if not self.audio_buffer or not self._engine:
            logger.warning("No audio buffer or engine available for detection")
            return

        try:
            # Convert PCM to WAV once for both transcription and storage
            wav_data = self._create_wav_from_pcm(bytes(self.audio_buffer))

            # Transcribe audio
            logger.info("Transcribing audio for voicemail detection")
            transcript = await self._transcribe_audio(wav_data)

            if not transcript:
                logger.warning("No transcript obtained from audio")

                # Still upload the raw recording so data pipeline has it
                if self.workflow_run_id:
                    await self._save_voicemail_audio(wav_data, 0.0, False)

                return

            logger.info(
                f"Voicemail detection transcript obtained: {transcript[:100]}..."
            )

            # Analyze transcript
            result = await self._analyze_transcript(transcript)

            # Extract common fields
            confidence = result.get("confidence", 0.0)
            reasoning = result.get("reasoning", "No reasoning provided")

            # Save voicemail audio to S3 once for data pipeline (include duration in filename)
            s3_path = None
            if self.workflow_run_id:
                s3_path = await self._save_voicemail_audio(
                    wav_data, confidence, result.get("is_voicemail")
                )

            # Take action based on result
            if result.get("is_voicemail", False):
                logger.info(
                    f"Voicemail detected with confidence {confidence}: {reasoning}"
                )

                # Update workflow run with voicemail tags
                if self.workflow_run_id:
                    # Fetch the workflow run from database
                    workflow_run = await db_client.get_workflow_run_by_id(
                        self.workflow_run_id
                    )
                    if workflow_run:
                        call_tags = workflow_run.gathered_context.get("call_tags", [])
                        call_tags.extend(["voicemail_detected", "not_connected"])

                        await db_client.update_workflow_run(
                            run_id=workflow_run.id,
                            gathered_context={
                                "call_tags": call_tags,
                                "voicemail_transcript": transcript,
                                "voicemail_confidence": confidence,
                            },
                        )

                # Send end task frame with metadata (including optional S3 path)
                await self._engine.send_end_task_frame(
                    reason=EndTaskReason.VOICEMAIL_DETECTED.value,
                    abort_immediately=True,
                )
            else:
                logger.info("No voicemail detected, continuing normal conversation")

        except Exception as e:
            logger.error(f"Error processing voicemail detection: {e}")

    async def _transcribe_audio(self, wav_data: bytes) -> str:
        """Transcribe audio using OpenAI API directly.

        Args:
            wav_data: WAV formatted audio data
        """
        client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        # Direct API call - no pipeline involvement
        response = await client.audio.transcriptions.create(
            file=("audio.wav", wav_data, "audio/wav"),
            model="whisper-1",  # Using whisper-1 as it's more stable for transcription
            language="en",
            temperature=0.0,
        )

        return response.text.strip()

    def _create_wav_from_pcm(self, pcm_data: bytes) -> bytes:
        """Convert raw PCM data to WAV format."""
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(pcm_data)

        wav_buffer.seek(0)
        return wav_buffer.read()

    async def _analyze_transcript(self, transcript: str) -> dict:
        """Analyze transcript using independent OpenAI client."""
        # Capture the current turn context for proper span nesting
        parent_context = get_current_turn_context()

        client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        langfuse_prompt = None
        try:
            langfuse_prompt = self._langfuse_client.get_prompt(
                "production/voicemail_detection"
            )
            prompt = langfuse_prompt.compile(transcript=transcript)
        except Exception as e:
            logger.warning(f"Error getting prompt from Langfuse: {e}")
            prompt = DEFAULT_VOICEMAIL_PROMPT.replace("{transcript}", transcript)

        messages = [
            {
                "role": "system",
                "content": prompt,
            }
        ]

        # When we have a parent OpenTelemetry context, we need to activate it
        # so that Langfuse's OTEL tracer will automatically pick it up
        if parent_context and is_tracing_enabled():
            # Activate the parent context for this scope
            token = otel_context.attach(parent_context)
            try:
                # Start Langfuse generation - it will automatically use the active OTEL context
                langfuse_generation = None
                try:
                    langfuse_generation = self._langfuse_client.start_generation(
                        name="voicemail_detection",
                        model="gpt-4o",
                        input=messages,
                        metadata={
                            "temperature": 0.0,
                            "detection_duration": self.detection_duration,
                            "transcript_length": len(transcript),
                        },
                        prompt=langfuse_prompt,
                    )
                except Exception as e:
                    logger.warning(f"Error starting Langfuse generation: {e}")

                # Direct API call
                response = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )

                llm_response = response.choices[0].message.content

                # Update and end Langfuse generation
                if langfuse_generation:
                    try:
                        langfuse_generation.update(
                            output=llm_response,
                            usage_details={
                                "prompt_tokens": response.usage.prompt_tokens
                                if response.usage
                                else 0,
                                "completion_tokens": response.usage.completion_tokens
                                if response.usage
                                else 0,
                                "total_tokens": response.usage.total_tokens
                                if response.usage
                                else 0,
                            },
                        )
                        langfuse_generation.end()
                    except Exception as e:
                        logger.warning(f"Error updating Langfuse generation: {e}")
            finally:
                # Detach the context
                otel_context.detach(token)
        else:
            # No parent context or tracing disabled - just make the API call
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            llm_response = response.choices[0].message.content

        # Parse response
        try:
            return json.loads(llm_response)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON response from voicemail detection")
            return {
                "is_voicemail": False,
                "confidence": 0.0,
                "reasoning": "Invalid response",
            }

    async def _save_voicemail_audio(
        self, wav_data: bytes, confidence: float, is_voicemail: bool
    ) -> Optional[str]:
        """Save voicemail audio to temp file and enqueue task to upload to S3.

        Args:
            wav_data: WAV formatted audio data
            confidence: Detection confidence score
            is_voicemail: Whether it was detected as voicemail

        Returns:
            The expected S3 object key (bucket path). The actual upload happens asynchronously.
        """
        try:
            # Create filename with prediction, confidence and duration
            duration_seconds = self._current_duration_seconds()
            prediction = "voicemail" if is_voicemail else "not_voicemail"
            confidence_int = int(confidence * 100)
            duration_int = int(duration_seconds)
            s3_key = f"voicemail_detections/{self.workflow_run_id}_{prediction}_{confidence_int}_{duration_int}.wav"

            # Write WAV data to temp file - DO NOT delete it here, the async task will handle cleanup
            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                delete=False,  # Important: don't delete immediately
                prefix=f"voicemail_{self.workflow_run_id}_",
            ) as tmp_file:
                tmp_file.write(wav_data)
                tmp_file.flush()
                temp_file_path = tmp_file.name

            logger.info(f"Saved voicemail audio to temp file: {temp_file_path}")

            # Enqueue async task to upload to S3
            await enqueue_job(
                FunctionNames.UPLOAD_VOICEMAIL_AUDIO_TO_S3,
                self.workflow_run_id,
                temp_file_path,
                s3_key,
            )

            logger.info(f"Enqueued voicemail audio upload task for: {s3_key}")
            return s3_key

        except Exception as e:
            logger.error(f"Failed to save voicemail audio: {e}")
            # Clean up temp file if task enqueue failed
            if "temp_file_path" in locals() and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception as cleanup_error:
                    logger.warning(
                        f"Failed to cleanup temp file after error: {cleanup_error}"
                    )
            return None
