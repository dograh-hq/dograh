"""In-memory transport pair for AI-to-AI simulations.

Adapted from the removed LoopTalk internal transport
(api/services/looptalk/internal_transport.py, removed in commit 45b00cd).
Audio written by one pipeline's output transport is delivered to the partner
pipeline's input transport, so two full Dograh pipelines can talk to each
other without any network or microphone involvement.
"""

import asyncio
import time
from typing import Optional

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    OutputDTMFFrame,
    OutputDTMFUrgentFrame,
    OutputImageRawFrame,
    StartFrame,
    StopFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams


class InternalFrameSerializer(FrameSerializer):
    """Serializer that only passes audio frames between the paired agents.

    Filtering to audio frames prevents control frames from leaking between
    the two pipelines and creating loops.
    """

    async def serialize(self, frame: Frame) -> bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            # "AUDIO" (5 bytes) + sample_rate (4 bytes) + num_channels (2 bytes) + pcm
            header = b"AUDIO"
            sample_rate_bytes = frame.sample_rate.to_bytes(4, byteorder="big")
            num_channels_bytes = frame.num_channels.to_bytes(2, byteorder="big")
            return header + sample_rate_bytes + num_channels_bytes + frame.audio
        return None

    async def deserialize(self, data: bytes) -> Frame | None:
        if not data.startswith(b"AUDIO"):
            return None
        if len(data) < 11:
            logger.error(
                f"InternalFrameSerializer: data too short for header: {len(data)} bytes"
            )
            return None
        sample_rate = int.from_bytes(data[5:9], byteorder="big")
        num_channels = int.from_bytes(data[9:11], byteorder="big")
        audio_data = data[11:]
        return InputAudioRawFrame(
            audio=audio_data, num_channels=num_channels, sample_rate=sample_rate
        )


class InternalInputTransport(BaseInputTransport):
    """Input side: receives serialized audio from the partner output transport."""

    def __init__(
        self,
        transport: Optional["InternalTransport"],
        params: TransportParams,
        **kwargs,
    ):
        super().__init__(params, **kwargs)
        self._transport = transport
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._running = False
        self._connected = False
        self._serializer = InternalFrameSerializer()
        self._receive_task: asyncio.Task | None = None

    async def receive_data(self, data: bytes):
        """Receive serialized data from the partner output transport."""
        await self._queue.put(data)

    async def start(self, frame: StartFrame):
        self._running = True
        await super().start(frame)
        await self._serializer.setup(frame)

        # Set transport ready to initialize the audio task for VAD processing.
        await self.set_transport_ready(frame)

        # Fire on_client_connected once, mirroring network transports.
        if self._transport and not self._connected:
            self._connected = True
            await self._transport._call_event_handler(
                "on_client_connected", self._transport
            )

        self._receive_task = asyncio.create_task(self._run())

    async def stop(self, frame: EndFrame | StopFrame | None = None):
        self._running = False
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        await super().stop(frame)

        if self._transport:
            await self._transport._call_event_handler(
                "on_client_disconnected", self._transport
            )

    async def _run(self):
        """Deserialize incoming data and feed it through the audio path."""
        while self._running:
            try:
                data = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                frame = await self._serializer.deserialize(data)
                if frame is None:
                    continue
                if isinstance(frame, InputAudioRawFrame):
                    # Base-class audio path (includes VAD processing).
                    await self.push_audio_frame(frame)
                else:
                    await self.push_frame(frame, FrameDirection.DOWNSTREAM)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in internal input transport: {e}")


class InternalOutputTransport(BaseOutputTransport):
    """Output side: serializes audio and delivers it to the partner input."""

    def __init__(self, params: TransportParams, **kwargs):
        super().__init__(params, **kwargs)
        self._partner: Optional[InternalInputTransport] = None
        self._serializer = InternalFrameSerializer()
        # Pace audio at real-time speed like WebsocketServerOutputTransport,
        # otherwise TTS audio is dumped into the partner pipeline instantly.
        self._send_interval = 0.0
        self._next_send_time = 0.0

    def set_partner(self, partner: InternalInputTransport):
        self._partner = partner

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._serializer.setup(frame)
        self._send_interval = self._params.audio_out_10ms_chunks * 10 / 1000
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        data = await self._serializer.serialize(frame)
        if data and self._partner:
            await self._partner.receive_data(data)
        await self._write_audio_sleep()
        return True

    async def write_video_frame(self, _frame: OutputImageRawFrame) -> bool:
        """Internal transport doesn't support video."""
        return False

    async def write_dtmf(self, _frame: OutputDTMFFrame | OutputDTMFUrgentFrame):
        """Internal transport doesn't support DTMF."""
        pass

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        self._next_send_time = 0.0

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        self._next_send_time = 0.0

    async def _write_audio_sleep(self):
        """Simulate a real-time clock for outgoing audio."""
        current_time = time.monotonic()
        sleep_duration = max(0, self._next_send_time - current_time)
        await asyncio.sleep(sleep_duration)
        if sleep_duration == 0:
            self._next_send_time = time.monotonic() + self._send_interval
        else:
            self._next_send_time += self._send_interval


class InternalTransport(BaseTransport):
    """In-memory transport for agent-to-agent communication."""

    def __init__(self, params: TransportParams, **kwargs):
        super().__init__(**kwargs)
        self._params = params

        self._input = InternalInputTransport(
            self, params, name=self._input_name or f"{self.name}#input"
        )
        self._output = InternalOutputTransport(
            params, name=self._output_name or f"{self.name}#output"
        )

        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")

    def input(self) -> InternalInputTransport:
        return self._input

    def output(self) -> InternalOutputTransport:
        return self._output

    def connect_partner(self, partner: "InternalTransport"):
        """Cross-wire this transport with another internal transport."""
        self._output.set_partner(partner._input)
        partner._output.set_partner(self._input)


def create_internal_transport_pair(
    *,
    name_a: str,
    name_b: str,
    sample_rate: int = 16000,
) -> tuple[InternalTransport, InternalTransport]:
    """Create a connected pair of internal transports."""

    def make_params() -> TransportParams:
        return TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=sample_rate,
            audio_out_sample_rate=sample_rate,
        )

    transport_a = InternalTransport(params=make_params(), name=name_a)
    transport_b = InternalTransport(params=make_params(), name=name_b)
    transport_a.connect_partner(transport_b)
    return transport_a, transport_b
