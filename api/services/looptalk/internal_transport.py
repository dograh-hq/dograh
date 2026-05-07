#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Internal transport for in-memory agent-to-agent communication."""

import asyncio
import time
from typing import Dict, Optional, Tuple

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    OutputDTMFFrame,
    OutputDTMFUrgentFrame,
    OutputImageRawFrame,
    StartFrame,
    StopFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams

from api.services.looptalk.internal_serializer import InternalFrameSerializer


class InternalInputTransport(BaseInputTransport):
    """Input side of internal transport for agent-to-agent communication."""

    def __init__(
        self,
        transport: Optional["InternalTransport"],
        params: TransportParams,
        **kwargs,
    ):
        """Initialize internal input transport.

        Args:
            transport: The parent InternalTransport instance.
            params: Transport parameters for configuration.
            **kwargs: Additional keyword arguments including latency_seconds.
        """
        # Extract latency configuration before passing to parent
        self._latency_seconds = kwargs.pop("latency_seconds", 0.0)

        super().__init__(params, **kwargs)
        self._transport = transport
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._partner: Optional["InternalOutputTransport"] = None
        self._running = False
        self._connected = False
        self._serializer = InternalFrameSerializer()
        # Queue for delayed packets (timestamp, data)
        self._delayed_queue: asyncio.Queue[Tuple[float, bytes]] = asyncio.Queue()
        self._latency_task: Optional[asyncio.Task] = None

    def set_partner(self, partner: "InternalOutputTransport"):
        """Connect this input transport to an output transport."""
        self._partner = partner

    async def receive_data(self, data: bytes):
        """Receive serialized data from the partner output transport."""
        # logger.debug("received data in input transport")
        if self._latency_seconds > 0:
            # Add to delayed queue with delivery timestamp
            delivery_time = time.monotonic() + self._latency_seconds
            await self._delayed_queue.put((delivery_time, data))
        else:
            # No latency, put directly in the main queue
            await self._queue.put(data)

    async def start(self, frame: StartFrame):
        """Start the input transport."""
        self._running = True
        await super().start(frame)
        await self._serializer.setup(frame)

        # Set transport ready to initialize audio task for VAD processing
        await self.set_transport_ready(frame)

        # Trigger on_client_connected event for InternalTransport (only once)
        if hasattr(self, "_transport") and self._transport and not self._connected:
            self._connected = True
            await self._transport._call_event_handler(
                "on_client_connected", self._transport
            )

        # Start latency processor if latency is configured
        if self._latency_seconds > 0:
            self._latency_task = asyncio.create_task(self._latency_processor())

        asyncio.create_task(self._run())

    async def stop(self, frame: EndFrame | StopFrame | None = None):
        """Stop the input transport."""
        self._running = False

        # Stop latency processor
        if self._latency_task:
            self._latency_task.cancel()
            try:
                await self._latency_task
            except asyncio.CancelledError:
                pass
            self._latency_task = None

        await super().stop(frame)

        # Trigger on_client_disconnected event for InternalTransport
        if hasattr(self, "_transport") and self._transport:
            await self._transport._call_event_handler(
                "on_client_disconnected", self._transport
            )

    async def _run(self):
        """Main loop to process incoming data."""
        while self._running:
            try:
                data = await asyncio.wait_for(self._queue.get(), timeout=0.1)

                # Deserialize the data
                frame = await self._serializer.deserialize(data)
                if frame:
                    if isinstance(frame, InputAudioRawFrame):
                        # Debug received audio
                        try:
                            import numpy as np

                            # Check if audio length is valid for int16
                            if len(frame.audio) % 2 != 0:
                                logger.error(
                                    f"InternalInput: Audio buffer has odd length: {len(frame.audio)}"
                                )
                            else:
                                audio_array = np.frombuffer(frame.audio, dtype=np.int16)
                                # logger.debug(f"InternalInput: Received audio - size: {len(frame.audio)} bytes, "
                                #            f"samples: {len(audio_array)}, min: {audio_array.min()}, max: {audio_array.max()}, "
                                #            f"sample_rate: {frame.sample_rate}")
                        except Exception as e:
                            logger.error(f"InternalInput: Error analyzing audio: {e}")

                        # Use the base class's audio processing which includes VAD
                        await self.push_audio_frame(frame)
                    else:
                        # For non-audio frames, push directly
                        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error in internal input transport: {e}")

    async def _latency_processor(self):
        """Process delayed packets and deliver them after the configured latency."""
        logger.info(
            f"InternalInput: Started latency processor with {self._latency_seconds}s delay"
        )

        # Use a list to maintain order (we'll process in FIFO order)
        pending_packets = []

        while self._running:
            try:
                # Get all new packets from the delayed queue (non-blocking)
                while True:
                    try:
                        packet = self._delayed_queue.get_nowait()
                        pending_packets.append(packet)
                    except asyncio.QueueEmpty:
                        break

                # Process packets that are ready
                current_time = time.monotonic()
                delivered = []

                for i, (delivery_time, data) in enumerate(pending_packets):
                    if current_time >= delivery_time:
                        # Time to deliver this packet
                        await self._queue.put(data)
                        delivered.append(i)

                # Remove delivered packets (in reverse order to maintain indices)
                for i in reversed(delivered):
                    pending_packets.pop(i)

                # Sleep briefly before next check
                await asyncio.sleep(0.005)  # 5ms for more responsive delivery

            except asyncio.CancelledError:
                # Deliver any remaining packets immediately on shutdown
                for _, data in pending_packets:
                    await self._queue.put(data)
                break
            except Exception as e:
                logger.error(f"Error in latency processor: {e}")
                await asyncio.sleep(0.01)

        logger.info("InternalInput: Stopped latency processor")


class InternalOutputTransport(BaseOutputTransport):
    """Output side of internal transport for agent-to-agent communication."""

    def __init__(self, params: TransportParams, **kwargs):
        """Initialize internal output transport.

        Args:
            params: Transport parameters for configuration.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(params, **kwargs)
        self._partner: Optional[InternalInputTransport] = None
        self._serializer = InternalFrameSerializer()

        # Audio timing synchronization (similar to WebsocketServerOutputTransport)
        # _send_interval is the time interval between audio chunks in seconds
        self._send_interval = 0
        self._next_send_time = 0

    def set_partner(self, partner: InternalInputTransport):
        """Connect this output transport to an input transport."""
        self._partner = partner

    async def start(self, frame: StartFrame):
        """Start the output transport."""
        await super().start(frame)
        await self._serializer.setup(frame)
        # Calculate the send interval based on audio chunk size (like WebsocketServerOutputTransport)
        self._send_interval = (
            self._params.audio_out_10ms_chunks * 10 / 1000
        )  # Convert ms to seconds
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame: OutputAudioRawFrame):
        """Write audio frame to partner through serializer with proper timing."""
        # Debug audio characteristics
        # import numpy as np
        # audio_array = np.frombuffer(frame.audio, dtype=np.int16)
        # logger.debug(f"InternalOutput: Sending audio - type: {type(frame).__name__}, size: {len(frame.audio)} bytes, "
        #             f"samples: {len(audio_array)}, min: {audio_array.min()}, max: {audio_array.max()}, "
        #             f"sample_rate: {frame.sample_rate}")

        # Serialize and send the audio first
        data = await self._serializer.serialize(frame)
        if data and self._partner:
            await self._partner.receive_data(data)

        # logger.debug(f"InternalOutput: Sent audio frame to partner")

        # Then simulate audio playback timing (following WebsocketServerOutputTransport pattern)
        await self._write_audio_sleep()

    async def write_video_frame(self, _frame: OutputImageRawFrame):
        """Internal transport doesn't support video."""
        pass

    async def write_dtmf(self, _frame: OutputDTMFFrame | OutputDTMFUrgentFrame):
        """Internal transport doesn't support DTMF."""
        pass

    async def stop(self, frame: EndFrame):
        """Stop the output transport and reset timing."""
        await super().stop(frame)
        self._next_send_time = 0

    async def cancel(self, frame: CancelFrame):
        """Cancel the output transport and reset timing."""
        await super().cancel(frame)
        self._next_send_time = 0

    async def _write_audio_sleep(self):
        """Simulate audio playback timing (following WebsocketServerOutputTransport pattern)."""
        # Simulate a clock to ensure audio is sent at real-time pace
        current_time = time.monotonic()
        sleep_duration = max(0, self._next_send_time - current_time)
        await asyncio.sleep(sleep_duration)
        if sleep_duration == 0:
            self._next_send_time = time.monotonic() + self._send_interval
        else:
            self._next_send_time += self._send_interval


class InternalTransport(BaseTransport):
    """Internal transport for in-memory agent-to-agent communication."""

    def __init__(self, params: TransportParams, **kwargs):
        """Initialize internal transport.

        Args:
            params: Transport parameters for configuration.
            **kwargs: Additional keyword arguments including latency_seconds.
        """
        # Extract latency configuration before passing to parent
        self._latency_seconds = kwargs.pop("latency_seconds", 0.0)

        super().__init__(**kwargs)
        self._params = params

        # Create input and output transports
        self._input = InternalInputTransport(
            self,
            params,
            name=self._input_name or f"{self.name}#input",
            latency_seconds=self._latency_seconds,
        )
        self._output = InternalOutputTransport(
            params, name=self._output_name or f"{self.name}#output"
        )

        # Register supported event handlers
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")

    def input(self) -> InternalInputTransport:
        """Get the input transport."""
        return self._input

    def output(self) -> InternalOutputTransport:
        """Get the output transport."""
        return self._output

    def connect_partner(self, partner: "InternalTransport"):
        """Connect this transport to another internal transport."""
        # Connect output of this transport to input of partner
        self._output.set_partner(partner._input)
        # Connect output of partner to input of this transport
        partner._output.set_partner(self._input)


class InternalTransportManager:
    """Manages multiple internal transport pairs for load testing."""

    def __init__(self):
        """Initialize internal transport manager."""
        self._transport_pairs: Dict[
            str, Tuple[InternalTransport, InternalTransport]
        ] = {}

    def create_transport_pair(
        self,
        test_session_id: str,
        actor_params: TransportParams,
        adversary_params: TransportParams,
        latency_seconds: float = 0.0,
    ) -> Tuple[InternalTransport, InternalTransport]:
        """Create a connected pair of internal transports.

        Args:
            test_session_id: Unique identifier for the test session.
            actor_params: Transport parameters for the actor.
            adversary_params: Transport parameters for the adversary.
            latency_seconds: Simulated network latency in seconds (default: 0.0).

        Returns:
            Tuple of (actor_transport, adversary_transport).
        """
        # Create actor transport with latency
        actor_transport = InternalTransport(
            params=actor_params,
            name=f"actor-{test_session_id}",
            latency_seconds=latency_seconds,
        )

        # Create adversary transport with latency
        adversary_transport = InternalTransport(
            params=adversary_params,
            name=f"adversary-{test_session_id}",
            latency_seconds=latency_seconds,
        )

        # Connect them
        actor_transport.connect_partner(adversary_transport)

        # Store the pair
        self._transport_pairs[test_session_id] = (actor_transport, adversary_transport)

        logger.info(
            f"Created internal transport pair for test session: {test_session_id} with {latency_seconds}s latency"
        )

        return actor_transport, adversary_transport

    def get_transport_pair(
        self, test_session_id: str
    ) -> Optional[Tuple[InternalTransport, InternalTransport]]:
        """Get an existing transport pair."""
        return self._transport_pairs.get(test_session_id)

    def remove_transport_pair(self, test_session_id: str):
        """Remove a transport pair."""
        if test_session_id in self._transport_pairs:
            del self._transport_pairs[test_session_id]
            logger.info(
                f"Removed internal transport pair for test session: {test_session_id}"
            )

    def get_active_test_count(self) -> int:
        """Get the number of active test sessions."""
        return len(self._transport_pairs)
