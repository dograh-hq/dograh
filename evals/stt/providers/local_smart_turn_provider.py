"""Local Smart Turn provider for benchmarking end-of-turn detection.

Uses the pipecat smart-turn-v3 ONNX model for local ML-based turn detection.
This is NOT an STT provider - it only detects when a speaker has finished talking.
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from ..audio_streamer import AudioConfig, AudioStreamer
from .base import EventCallback, STTProvider, TranscriptionResult, Word

try:
    import onnxruntime as ort
    from transformers import WhisperFeatureExtractor
except ImportError:
    raise ImportError(
        "onnxruntime and transformers required: pip install onnxruntime transformers"
    )


@dataclass
class TurnEvent:
    """Represents a detected turn event."""
    timestamp: float  # Time in audio when turn was detected
    probability: float  # Model confidence
    prediction: int  # 1=complete, 0=incomplete
    inference_time_ms: float


class LocalSmartTurnProvider(STTProvider):
    """Local Smart Turn provider for end-of-turn detection benchmarking.

    Uses the smart-turn-v3 ONNX model to detect when speakers finish talking.
    This is useful for comparing turn detection accuracy against cloud services
    like Deepgram Flux's built-in turn detection.

    NOTE: This provider does NOT produce transcripts - only turn detection events.
    """

    # Smart turn model requires 16kHz audio
    REQUIRED_SAMPLE_RATE = 16000
    # Model analyzes 8 seconds of audio
    WINDOW_SECONDS = 8

    def __init__(
        self,
        model_path: str | None = None,
        cpu_count: int = 1,
    ):
        """Initialize the local smart turn provider.

        Args:
            model_path: Path to ONNX model file. If None, uses bundled model.
            cpu_count: Number of CPUs for inference (default: 1)
        """
        self.model_path = model_path
        self.cpu_count = cpu_count
        self._session = None
        self._feature_extractor = None

    def _load_model(self):
        """Lazy load the ONNX model."""
        if self._session is not None:
            return

        model_path = self.model_path

        if not model_path:
            # Try to load bundled model from pipecat
            model_name = "smart-turn-v3.1-cpu.onnx"
            package_path = "pipecat.audio.turn.smart_turn.data"

            try:
                import importlib_resources as impresources
                model_path = str(impresources.files(package_path).joinpath(model_name))
            except Exception:
                from importlib import resources as impresources
                try:
                    with impresources.path(package_path, model_name) as f:
                        model_path = str(f)
                except Exception:
                    model_path = str(impresources.files(package_path).joinpath(model_name))

        logger.info(f"[local-smart-turn] Loading model from {model_path}")

        # Configure ONNX runtime
        so = ort.SessionOptions()
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = self.cpu_count
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._feature_extractor = WhisperFeatureExtractor(chunk_length=8)
        self._session = ort.InferenceSession(model_path, sess_options=so)

        logger.info("[local-smart-turn] Model loaded")

    @property
    def name(self) -> str:
        return "local-smart-turn"

    def _predict_endpoint(self, audio_array: np.ndarray) -> dict[str, Any]:
        """Predict end-of-turn using the ONNX model.

        Args:
            audio_array: Audio samples as float32 numpy array (16kHz)

        Returns:
            Dict with prediction (0/1) and probability
        """
        # Truncate to last 8 seconds or pad to 8 seconds
        max_samples = self.WINDOW_SECONDS * self.REQUIRED_SAMPLE_RATE
        if len(audio_array) > max_samples:
            audio_array = audio_array[-max_samples:]
        elif len(audio_array) < max_samples:
            padding = max_samples - len(audio_array)
            audio_array = np.pad(audio_array, (padding, 0), mode="constant", constant_values=0)

        # Process using Whisper's feature extractor
        inputs = self._feature_extractor(
            audio_array,
            sampling_rate=self.REQUIRED_SAMPLE_RATE,
            return_tensors="np",
            padding="max_length",
            max_length=self.WINDOW_SECONDS * self.REQUIRED_SAMPLE_RATE,
            truncation=True,
            do_normalize=True,
        )

        # Extract features for ONNX
        input_features = inputs.input_features.squeeze(0).astype(np.float32)
        input_features = np.expand_dims(input_features, axis=0)

        # Run inference
        start_time = time.perf_counter()
        outputs = self._session.run(None, {"input_features": input_features})
        inference_time = (time.perf_counter() - start_time) * 1000

        # Extract probability (model returns sigmoid probabilities)
        probability = outputs[0][0].item()
        prediction = 1 if probability > 0.5 else 0

        return {
            "prediction": prediction,
            "probability": probability,
            "inference_time_ms": inference_time,
        }

    async def transcribe(
        self,
        audio_path: Path,
        diarize: bool = False,  # Ignored - not applicable
        keyterms: list[str] | None = None,  # Ignored - not applicable
        on_event: EventCallback | None = None,  # Ignored - not applicable
        sample_rate: int = 16000,  # Must be 16kHz for smart turn
        analysis_interval_ms: int = 500,  # How often to check for turn completion
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Analyze audio for turn detection events.

        NOTE: This does NOT produce transcripts. It detects when speakers
        finish talking using ML-based turn detection.

        Args:
            audio_path: Path to audio file
            diarize: Ignored (not applicable for turn detection)
            keyterms: Ignored (not applicable for turn detection)
            on_event: Ignored (not applicable for turn detection)
            sample_rate: Must be 16000 Hz for smart turn model
            analysis_interval_ms: How often to run turn detection (ms)
            **kwargs: Additional parameters (ignored)

        Returns:
            TranscriptionResult with turn detection events in raw_response
        """
        if sample_rate != self.REQUIRED_SAMPLE_RATE:
            logger.warning(
                f"[local-smart-turn] Sample rate must be {self.REQUIRED_SAMPLE_RATE}Hz, "
                f"overriding {sample_rate}Hz"
            )
            sample_rate = self.REQUIRED_SAMPLE_RATE

        # Load model if not already loaded
        self._load_model()

        # Setup audio streamer at 16kHz
        audio_config = AudioConfig(sample_rate=sample_rate)
        streamer = AudioStreamer(audio_config)

        # Get audio duration
        duration = streamer.get_duration(audio_path)
        logger.info(f"[local-smart-turn] Processing {audio_path} ({duration:.2f}s)")

        # Collect all audio first (smart turn needs to analyze segments)
        pcm_data = streamer.convert_to_pcm16(audio_path)

        # Convert to float32 for model
        audio_int16 = np.frombuffer(pcm_data, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        # Analyze at intervals
        turn_events: list[TurnEvent] = []
        samples_per_interval = int(sample_rate * analysis_interval_ms / 1000)
        window_samples = self.WINDOW_SECONDS * sample_rate

        chunk_no = 0
        for end_sample in range(samples_per_interval, len(audio_float32), samples_per_interval):
            # Get window of audio ending at current position
            start_sample = max(0, end_sample - window_samples)
            audio_window = audio_float32[start_sample:end_sample]

            current_time = end_sample / sample_rate
            logger.debug(f"[local-smart-turn] Analyzing chunk {chunk_no} at {current_time:.2f}s")

            result = self._predict_endpoint(audio_window)

            turn_events.append(TurnEvent(
                timestamp=current_time,
                probability=result["probability"],
                prediction=result["prediction"],
                inference_time_ms=result["inference_time_ms"],
            ))

            if result["prediction"] == 1:
                logger.info(
                    f"[local-smart-turn] Turn complete at {current_time:.2f}s "
                    f"(prob={result['probability']:.3f})"
                    f"(inf time ms={result["inference_time_ms"]})"
                )

            chunk_no += 1

        # Create result
        # Convert turn events to word-like format for compatibility
        words = []
        for event in turn_events:
            if event.prediction == 1:
                words.append(Word(
                    word=f"[END_OF_TURN prob={event.probability:.2f}]",
                    start=event.timestamp - 0.1,
                    end=event.timestamp,
                    confidence=event.probability,
                    speaker=None,
                    speaker_confidence=None,
                ))

        # Count completed turns
        completed_turns = sum(1 for e in turn_events if e.prediction == 1)

        params = {
            "sample_rate": sample_rate,
            "analysis_interval_ms": analysis_interval_ms,
            "window_seconds": self.WINDOW_SECONDS,
        }

        return TranscriptionResult(
            provider=self.name,
            transcript=f"[Turn detection only - {completed_turns} turns detected]",
            words=words,
            speakers=[],  # Not applicable
            duration=duration,
            raw_response={
                "turn_events": [
                    {
                        "timestamp": e.timestamp,
                        "probability": e.probability,
                        "prediction": e.prediction,
                        "inference_time_ms": e.inference_time_ms,
                    }
                    for e in turn_events
                ],
                "completed_turns": completed_turns,
                "total_analyses": len(turn_events),
                "avg_inference_time_ms": (
                    sum(e.inference_time_ms for e in turn_events) / len(turn_events)
                    if turn_events else 0
                ),
            },
            params=params,
        )
