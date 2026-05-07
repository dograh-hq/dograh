"""Smart-Turn analyzer that talks to a FastAPI WebSocket endpoint.

This analyzer keeps a persistent WebSocket connection alive so that the TCP/TLS
handshake and HTTP upgrade happen only once per call session. Each speech
segment is sent as a single binary message containing the NumPy-serialized
float32 array, and a JSON reply is expected in return.

Rewritten to use the websockets library for simplified connection management.
"""

from __future__ import annotations

import asyncio
import io
import json
import random
import time
from typing import Any, Dict, Optional

import numpy as np
import websockets
from loguru import logger
from pipecat.audio.turn.smart_turn.base_smart_turn import (
    BaseSmartTurn,
    SmartTurnTimeoutException,
)


class WebSocketSmartTurnAnalyzer(BaseSmartTurn):
    """End-of-turn analyzer that sends audio via a persistent WebSocket."""

    def __init__(
        self,
        *,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        service_context: Optional[Any] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._url = url.rstrip("/")
        self._headers = headers or {}
        self._service_context = service_context

        # WebSocket connection
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_lock = asyncio.Lock()

        # Connection management
        self._connection_task: Optional[asyncio.Task] = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0
        self._closing = False
        self._connection_closed_event = asyncio.Event()

        # Connection health monitoring
        self._last_successful_request = 0.0
        self._connection_attempts = 0

        # Start connection manager in background
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._connection_task = loop.create_task(self._connection_manager())
        except RuntimeError:
            logger.debug(
                "No running loop at object creation time. Connection will be opened lazily on first use."
            )

    def _serialize_array(self, audio_array: np.ndarray) -> bytes:
        """Serialize numpy array to bytes."""
        buffer = io.BytesIO()
        np.save(buffer, audio_array)
        return buffer.getvalue()

    async def _connection_manager(self) -> None:
        """Manages WebSocket connection lifecycle with automatic reconnection."""
        while not self._closing:
            try:
                # Establish connection
                await self._establish_connection()

                # Reset reconnect delay on successful connection
                self._reconnect_delay = 1.0
                self._connection_attempts = 0

                # Wait for connection close event
                self._connection_closed_event.clear()
                await self._connection_closed_event.wait()

                logger.debug("WebSocket connection closed")

            except Exception as e:
                logger.error(f"Connection manager error: {e}")

            finally:
                # Clean up connection
                if self._ws:
                    try:
                        await self._ws.close()
                    except:
                        pass
                self._ws = None

                if not self._closing:
                    # Exponential backoff for reconnection
                    self._connection_attempts += 1
                    delay = min(
                        self._reconnect_delay
                        * (2 ** min(self._connection_attempts - 1, 5)),
                        self._max_reconnect_delay,
                    )
                    # Add jitter to avoid thundering herd
                    delay += random.uniform(0, 0.5)
                    logger.info(
                        f"Reconnecting in {delay:.1f} seconds (attempt {self._connection_attempts})"
                    )
                    await asyncio.sleep(delay)

    async def _establish_connection(self) -> None:
        """Establish a new WebSocket connection with retry logic."""
        logger.debug("Establishing new WebSocket connection to Smart-Turn service...")

        # Prepare headers
        additional_headers = dict(self._headers)
        if self._service_context is not None:
            additional_headers["X-Service-Context"] = str(self._service_context)

        # _init_sample_rate is being set in the constructor, which we should
        # use in case self._sample_rate is not set yet. The actual _sample_rate
        # is being set in the set_sample_rate() method
        # but in case of WebSocketSmartTurnAnalyzer, we establish the websocket connection
        # during __init__() and won't see the set_sample_rate until later. So, lets
        # user the _init_sample_rate instead
        _sample_rate = self._sample_rate or self._init_sample_rate

        if _sample_rate > 0:
            additional_headers["X-Sample-Rate"] = str(_sample_rate)

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # Add jitter to prevent thundering herd
                if attempt > 0:
                    jitter = 0.1 * attempt
                    await asyncio.sleep(jitter)

                # Connect with websockets library
                self._ws = await websockets.connect(
                    self._url,
                    additional_headers=additional_headers,
                    ping_interval=5.0,  # let websockets send pings every 5s
                    ping_timeout=3.0,  # fail fast if no pong in 3s
                    close_timeout=10,
                    max_size=10 * 1024 * 1024,  # 10MB max message size
                )

                logger.info("WebSocket connection established successfully")
                return

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    f"Failed to establish WebSocket (attempt {attempt + 1}/{max_attempts}): {exc}"
                )
                if attempt == max_attempts - 1:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))

    async def _ensure_ws(self) -> websockets.WebSocketClientProtocol:
        """Return a connected WebSocket, waiting for connection if necessary."""
        async with self._ws_lock:
            # If connection manager isn't running, start it
            if not self._connection_task or self._connection_task.done():
                self._connection_task = asyncio.create_task(self._connection_manager())

        # Wait for connection with timeout
        start_time = time.time()
        max_wait_time = 10.0

        while not self._closing:
            if self._ws:
                return self._ws

            elapsed = time.time() - start_time
            if elapsed > max_wait_time:
                raise Exception(
                    f"Timeout waiting for WebSocket connection after {max_wait_time}s"
                )

            await asyncio.sleep(0.1)

        if self._closing:
            raise Exception("Analyzer is closing")

        raise Exception("Failed to establish WebSocket connection")

    async def _predict_endpoint(self, audio_array: np.ndarray) -> Dict[str, Any]:
        """Send audio and await JSON response via WebSocket."""
        data_bytes = self._serialize_array(audio_array)

        try:
            # Ensure we have a connection
            ws = await self._ensure_ws()

            # Send data
            try:
                await ws.send(data_bytes)
            except Exception as e:
                logger.error(f"Failed to send data: {e}")
                self._connection_closed_event.set()
                return {
                    "prediction": 0,
                    "probability": 0.0,
                    "metrics": {"inference_time": 0.0, "total_time": 0.0},
                }

            # Wait for response
            start_time = time.time()
            while True:
                remaining_timeout = self._params.stop_secs - (time.time() - start_time)
                if remaining_timeout <= 0:
                    raise SmartTurnTimeoutException(
                        f"Request exceeded {self._params.stop_secs} seconds."
                    )

                try:
                    # Receive message with timeout
                    message = await asyncio.wait_for(
                        ws.recv(), timeout=min(remaining_timeout, 0.5)
                    )

                    # Handle text messages (JSON responses)
                    if isinstance(message, str):
                        try:
                            result = json.loads(message)

                            # Skip ping/pong messages
                            if result.get("type") in ["ping", "pong"]:
                                continue

                            # Validate prediction response
                            if "prediction" not in result:
                                if "type" in result:
                                    continue
                                else:
                                    logger.error(
                                        "Invalid response format from Smart-Turn service"
                                    )
                                    return {
                                        "prediction": 0,
                                        "probability": 0.0,
                                        "metrics": {
                                            "inference_time": 0.0,
                                            "total_time": 0.0,
                                        },
                                    }

                            self._last_successful_request = time.time()
                            return result

                        except json.JSONDecodeError as exc:
                            logger.error(
                                f"Smart turn service returned invalid JSON: {exc}"
                            )
                            raise
                    else:
                        logger.error(f"Unexpected message type: {type(message)}")

                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("WebSocket connection closed during prediction")
                    self._connection_closed_event.set()
                    return {
                        "prediction": 0,
                        "probability": 0.0,
                        "metrics": {"inference_time": 0.0, "total_time": 0.0},
                    }

        except SmartTurnTimeoutException:
            raise
        except Exception as exc:
            logger.error(f"Smart turn prediction failed over WebSocket: {exc}")
            self._connection_closed_event.set()
            return {
                "prediction": 0,
                "probability": 0.0,
                "metrics": {"inference_time": 0.0, "total_time": 0.0},
            }

    async def close(self):
        """Asynchronously close the WebSocket."""
        self._closing = True
        self._connection_closed_event.set()

        async with self._ws_lock:
            # Cancel tasks
            if self._connection_task and not self._connection_task.done():
                self._connection_task.cancel()
                try:
                    await self._connection_task
                except asyncio.CancelledError:
                    pass

            # Close WebSocket
            if self._ws:
                try:
                    await self._ws.close()
                except:
                    pass
                finally:
                    self._ws = None
