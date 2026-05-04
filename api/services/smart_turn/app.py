import asyncio
import io
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from fastapi.websockets import WebSocketState
from pipecat.audio.turn.smart_turn.local_smart_turn_v2 import LocalSmartTurnAnalyzerV2
from scipy.io import wavfile

LOG_LEVEL = (
    logging.DEBUG
    if os.environ.get("LOG_LEVEL", "DEBUG").lower() == "debug"
    else logging.INFO
)

logger = logging.getLogger("smart_turn")
logger.setLevel(LOG_LEVEL)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logger.addHandler(handler)


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
MODEL_PATH = os.getenv("LOCAL_SMART_TURN_MODEL_PATH", "pipecat-ai/smart-turn-v2")

# ----------------------------------------------------------------------------
# Analyzer Pool
# ----------------------------------------------------------------------------


class _AnalyzerWrapper:
    """Wraps a LocalSmartTurnAnalyzer with a lock so only one request can use it at a time."""

    def __init__(self, analyzer: LocalSmartTurnAnalyzerV2):
        self.analyzer = analyzer
        self.lock = asyncio.Lock()


_analyzer_wrapper: _AnalyzerWrapper | None = None  # Will be initialised in the lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the application lifespan - startup and shutdown logic."""
    # Startup logic
    global _analyzer_wrapper

    if _analyzer_wrapper is None:
        logger.debug("Initializing LocalSmartTurnAnalyzer")
        analyzer = LocalSmartTurnAnalyzerV2(smart_turn_model_path=MODEL_PATH)
        _analyzer_wrapper = _AnalyzerWrapper(analyzer)
        logger.debug("LocalSmartTurnAnalyzer initialized")

    yield  # Application runs here

    # Shutdown logic (if needed in the future)
    # Any cleanup code would go here


app = FastAPI(
    title="Smart Turn API",
    description="A FastAPI application exposing LocalSmartTurnAnalyzer via HTTP",
    lifespan=lifespan,
)

# ----------------------------------------------------------------------------
# API Endpoints
# ----------------------------------------------------------------------------


async def save_wav_file(
    audio_array: np.ndarray,
    prediction: int,
    probability: float,
    service_id: str | None = None,
    sample_rate: int = 16000,
) -> None:
    """Save audio data as a WAV file in the background.

    Runs the blocking ``wavfile.write`` call in a thread so that the event loop
    is not blocked.  This function is now ``async`` so it can be scheduled with
    ``asyncio.create_task`` from the WebSocket endpoint, while still being
    compatible with ``BackgroundTasks`` (which will ``await`` coroutine
    functions).

    Args:
        audio_array: The audio data as a numpy array
        prediction: The prediction result (0 or 1)
        probability: The probability of the prediction
        service_id: Optional service identifier
        sample_rate: The sample rate of the audio (default: 16000 Hz)
    """

    def _blocking_save() -> None:
        try:
            # Generate filename with current timestamp and prediction
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # Include ms

            # Include service_id in filename if available
            service_prefix = f"{service_id}_" if service_id else ""

            root_dir = (
                Path(__file__).resolve().parents[3]
            )  # dograh/api/services/smart_turn/app.py
            filename = (
                root_dir
                / f"smart_turn_pipeline/{service_prefix}{timestamp}_{prediction}_{probability}.wav"
            )

            # Convert float32 [-1, 1] back to int16 PCM for WAV file
            audio_int16 = np.clip(audio_array * 32767, -32768, 32767).astype(np.int16)

            # Use provided sample rate
            wavfile.write(filename, sample_rate, audio_int16)

            length_seconds = len(audio_array) / sample_rate
            log_message = f"Saved audio to {filename} (length: {length_seconds:.2f}s, prediction: {prediction}"
            if service_id:
                log_message += f", service_id: {service_id}"
            log_message += ")"

            logger.info(log_message)

        except Exception as exc:  # pragma: no cover – best-effort logging only
            log_message = f"Failed to save WAV file: {exc}"
            if service_id:
                log_message += f" (service_id: {service_id})"
            logger.error(log_message)

    # Offload the blocking I/O to a thread to avoid blocking the event loop
    await asyncio.to_thread(_blocking_save)


@app.post("/raw", status_code=status.HTTP_200_OK)
async def handle_raw(request: Request, background_tasks: BackgroundTasks):
    """
    Accept a NumPy-serialized float32 array (written via ``np.save``) in the body and
    return a JSON prediction compatible with ``HttpSmartTurnAnalyzer``.
    """

    # ------------------------------------------------------------------
    # Secret key validation
    # ------------------------------------------------------------------
    expected_secret = os.getenv("SMART_TURN_HTTP_SERVICE_KEY")
    if expected_secret:  # If a secret is configured, enforce validation
        provided_secret = request.headers.get("X-API-Key")
        if provided_secret != expected_secret:
            logger.warning(
                "Unauthorized access attempt to /raw endpoint with invalid or missing secret key"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized",
            )

    # ------------------------------------------------------------------
    # Start total-time measurement as early as possible
    # ------------------------------------------------------------------
    request_start_time = time.perf_counter()

    # ------------------------------------------------------------------
    # Log that we received a request (before doing any heavy work)
    # ------------------------------------------------------------------
    logger.debug("Received /raw request")

    body = await request.body()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Empty request body"
        )

    # Extract service context and sample rate from headers
    service_id = request.headers.get("X-Service-Context")
    sample_rate_str = request.headers.get("X-Sample-Rate")
    sample_rate = int(sample_rate_str) if sample_rate_str else 16000

    # Deserialize NumPy array
    try:
        audio_array = np.load(io.BytesIO(body))
    except Exception as exc:
        error_msg = f"Invalid NumPy payload: {exc}"
        if service_id:
            error_msg += f" (service_id: {service_id})"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
        )

    wrapper = _analyzer_wrapper
    if wrapper is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analyzer not initialized",
        )

    # Run inference guarded by the wrapper lock so the model isn't used concurrently
    log_msg = "Going to acquire lock for model inference"
    if service_id:
        log_msg += f" (service_id: {service_id})"
    logger.debug(log_msg)

    async with wrapper.lock:
        log_msg = "Acquired lock for model inference"
        if service_id:
            log_msg += f" (service_id: {service_id})"
        logger.debug(log_msg)

        # Measure inference-only latency
        inference_start_time = time.perf_counter()
        result = await wrapper.analyzer._predict_endpoint(audio_array)
        inference_time = time.perf_counter() - inference_start_time

    # Calculate total processing time (from request receipt to response preparation)
    total_time = time.perf_counter() - request_start_time

    log_msg = (
        f"Inference done result: {result['prediction']} "
        f"probability: {result['probability']} time taken: {inference_time:.2f}s total: {total_time:.2f}s"
    )
    if service_id:
        log_msg += f" (service_id: {service_id})"
    logger.debug(log_msg)

    # Ensure metrics section exists so client code can parse it consistently
    metrics = result.get("metrics", {})
    # Overwrite / set the timing metrics explicitly
    metrics["inference_time"] = inference_time
    metrics["total_time"] = total_time
    result["metrics"] = metrics

    logger.debug(f"Result for service_id: {service_id} is: {result}")

    # Add service_id to result for potential client use
    if service_id:
        result["service_id"] = service_id

    # Persist audio in background so it doesn't block the response.
    background_tasks.add_task(
        save_wav_file,
        audio_array,
        result.get("prediction", 0),
        result.get("probability", 0),
        service_id,
        sample_rate,
    )
    return result


@app.get("/")
async def root():
    """Health-check endpoint."""
    return {"message": "Smart Turn API is running"}


# ----------------------------------------------------------------------------
# WebSocket endpoint
# ----------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Handle streaming Smart Turn requests over WebSocket.

    Each incoming binary message must be a NumPy-serialized float32 array (as
    produced by ``np.save``).  A JSON-formatted prediction (identical to the
    ``/raw`` HTTP endpoint) is sent back as a text message.
    """

    # Extract optional secret key from headers (during handshake)
    expected_secret = os.getenv("SMART_TURN_HTTP_SERVICE_KEY")
    if expected_secret:
        provided_secret = ws.headers.get("X-API-Key")
        if provided_secret != expected_secret:
            await ws.close(code=4401, reason="Unauthorized")
            return

    # Accept the websocket connection and log it
    await ws.accept()

    service_id = ws.headers.get("X-Service-Context")
    sample_rate_str = ws.headers.get("X-Sample-Rate")
    sample_rate = int(sample_rate_str) if sample_rate_str else 16000
    logger.debug(
        f"WebSocket connection accepted from service_id: {service_id}, sample_rate: {sample_rate}"
    )

    # ------------------------------------------------------------------
    # Tunables – consider moving to env vars for ops control
    # ------------------------------------------------------------------
    connection_timeout = 120.0  # Seconds of inactivity before timing out
    MAX_BINARY_SIZE = int(
        os.getenv("SMART_TURN_MAX_PAYLOAD", 10 * 1024 * 1024)  # 10MB max message size
    )

    # Track background tasks so we can cancel them on disconnect
    background_tasks = set()  # Track background tasks for cleanup

    try:
        logger.debug("Entering WebSocket message loop")
        while True:
            data = None  # Initialize data for each iteration
            try:
                logger.debug("Waiting for WebSocket message…")

                # Create receive task to handle timeout properly
                receive_task = asyncio.create_task(ws.receive())
                try:
                    msg = await asyncio.wait_for(
                        receive_task, timeout=connection_timeout
                    )
                except asyncio.TimeoutError:
                    # Cancel the receive task to prevent it from running in background
                    receive_task.cancel()
                    try:
                        await receive_task
                    except asyncio.CancelledError:
                        pass

                    logger.warning(
                        f"WebSocket connection timeout for service_id: {service_id}"
                    )
                    try:
                        await ws.close(code=1001, reason="Connection timeout")
                    except Exception as e:
                        logger.debug(f"Error closing WebSocket after timeout: {e}")
                    break
                except WebSocketDisconnect as e:
                    logger.debug(f"WebSocket client disconnected: {e}")
                    break

                # Validate message structure
                if not isinstance(msg, dict):
                    logger.error(f"Unexpected message type: {type(msg)}")
                    break

                # Handle disconnect message explicitly
                if msg.get("type") == "websocket.disconnect":
                    logger.debug("Client sent disconnect frame")
                    break

                data = None
                # Binary frame
                if "bytes" in msg and msg["bytes"] is not None:
                    data = msg["bytes"]
                    logger.debug(
                        "Received WebSocket audio payload (%d bytes)", len(data)
                    )

            except WebSocketDisconnect as e:
                logger.debug(f"WebSocket client disconnected: {e}")
                break
            except Exception as e:
                logger.error(f"Error in WebSocket loop: {e}")
                break

            if data is None:
                continue

            request_start_time = time.perf_counter()

            # --------------------------------------------------------------
            # Basic validation & secure deserialisation
            # --------------------------------------------------------------
            if len(data) > MAX_BINARY_SIZE:
                logger.warning("Received payload exceeding maximum allowed size")
                await ws.send_text('{"error": "Payload too large"}')
                continue

            # Deserialize NumPy array (pickle disabled for security)
            try:
                audio_array = np.load(io.BytesIO(data), allow_pickle=False)
            except Exception as exc:
                error_msg = f"Invalid NumPy payload: {exc}"
                if service_id:
                    error_msg += f" (service_id: {service_id})"
                # Send error response with proper error handling
                if ws.application_state == WebSocketState.CONNECTED:
                    try:
                        await ws.send_text(f'{{"error": "{error_msg}"}}')
                    except Exception as e:
                        logger.error(f"Failed to send error message: {e}")
                continue

            wrapper = _analyzer_wrapper
            if wrapper is None:
                logger.error("Analyzer not initialized; closing connection")
                if ws.application_state == WebSocketState.CONNECTED:
                    await ws.close(code=1011, reason="Analyzer not ready")
                break

            async with wrapper.lock:
                inference_start_time = time.perf_counter()
                result = await wrapper.analyzer._predict_endpoint(audio_array)
                inference_time = time.perf_counter() - inference_start_time

            # Timing metrics
            total_time = time.perf_counter() - request_start_time
            metrics = result.get("metrics", {})
            metrics["inference_time"] = inference_time
            metrics["total_time"] = total_time
            result["metrics"] = metrics

            logger.debug(f"Result for service_id: {service_id} is: {result}")

            if service_id:
                result["service_id"] = service_id

            # Send result with proper error handling
            try:
                if ws.application_state == WebSocketState.CONNECTED:
                    await ws.send_text(json.dumps(result))
                else:
                    logger.warning(
                        f"Cannot send result - WebSocket not connected for service_id: {service_id}"
                    )
                    break
            except WebSocketDisconnect:
                logger.debug(
                    f"Client disconnected while sending result for service_id: {service_id}"
                )
                break
            except Exception as e:
                logger.error(f"Failed to send result: {e}")
                break

            # Save audio in the background so that it doesn't block streaming
            task = asyncio.create_task(
                save_wav_file(
                    audio_array,
                    result.get("prediction", 0),
                    result.get("probability", 0),
                    service_id,
                    sample_rate,
                )
            )
            # Track task and remove when done
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

    except WebSocketException as exc:
        logger.error(f"WebSocket error: {exc}")
    finally:
        # Cancel any remaining background tasks
        for task in background_tasks:
            if not task.done():
                task.cancel()
        # Wait for all background tasks to complete or be cancelled
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

        # Attempt a graceful close if it's not already closed
        if ws.application_state == WebSocketState.CONNECTED:
            try:
                await ws.close()
            except Exception as exc:
                # Socket is probably already closed; log and ignore
                logger.debug(f"WebSocket already closed: {exc}")
