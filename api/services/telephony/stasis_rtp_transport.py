# transports/ari_external_media.py  (new file)

"""Stasis RTP transport for Asterisk External Media integration."""

import asyncio
import time
from typing import Awaitable, Callable, Optional

from loguru import logger
from pydantic import BaseModel

from api.services.telephony.stasis_rtp_client import StasisRTPClient
from api.services.telephony.stasis_rtp_connection import StasisRTPConnection
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
    TransportMessageFrame,
    TransportMessageUrgentFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import (
    BaseOutputTransport
)
from pipecat.transports.base_transport import BaseTransport, TransportParams


class StasisRTPTransportParams(TransportParams):
    """Transport parameters for Stasis RTP transport."""

    serializer: FrameSerializer


class StasisRTPCallbacks(BaseModel):
    """Callbacks for Stasis RTP transport events."""

    on_client_connected: Callable[[str], Awaitable[None]]
    on_client_disconnected: Callable[[str], Awaitable[None]]
    on_client_closed: Callable[[str], Awaitable[None]]


# ------------------------------------------------ Input Transport -------------------------

"""
Transport calls client receive to receive the audio from the socket. This happens in the self._receive_audio task.
Then the audio frames are pushed to _audio_in_queue using push_audio_frame method. Then the _audio_task_handler processes
the frames from the _audio_in_queue and pushes them to the VAD analyzer, turn analyzer and pushes the audio
further downstream to tts.

The BaseInputTransport pipeline is responsible for:
- Resampling the audio to the correct sample rate
- Applying the audio filter
- Pushing the audio frames to the VAD analyzer
- Pushing the audio frames to the turn analyzer
- Pushing the audio frames to the bot interruption analyzer
- Pushing the audio frames down the pipeline to the tts

stop method is called from process_frame of the BaseInputTransport. super.stop() stops _audio_task_handler. It then
calls _client.disconnect. Transport's callbacks are sent to the client using StasisRTPCallbacks.
"""


class StasisRTPInputTransport(BaseInputTransport):
    """Input transport for receiving audio over Stasis RTP."""

    def __init__(
        self,
        transport: BaseTransport,
        client: StasisRTPClient,
        params: StasisRTPTransportParams,
        **kwargs,
    ):
        """Initialize Stasis RTP input transport.

        Args:
            transport: Parent transport instance.
            client: Stasis RTP client for socket communication.
            params: Transport parameters including serializer.
            **kwargs: Additional keyword arguments for BaseInputTransport.
        """
        super().__init__(params, **kwargs)
        self._transport = transport
        self._client = client
        self._params = params

        self._receive_task: Optional[asyncio.Task] = None

    async def start(self, frame: StartFrame):
        """Start the input transport."""
        await super().start(frame)

        await self._client.setup(frame)
        await self._params.serializer.setup(frame)

        # Ensure underlying connection is established and socket ready.
        await self._client.connect()

        if not self._receive_task:
            self._receive_task = self.create_task(self._receive_audio())

        await self.set_transport_ready(frame)

    async def _stop_tasks(self):
        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None

    async def stop(self, frame: EndFrame):
        """Stop the input transport."""
        await super().stop(frame)
        await self._stop_tasks()
        await self._client.disconnect()
        logger.debug("Successfully disconnected from StasisRTPClient")

    async def cancel(self, frame: CancelFrame):
        """Cancel the input transport."""
        await super().cancel(frame)
        await self._stop_tasks()
        await self._client.disconnect()

    async def _receive_audio(self):
        try:
            async for payload in self._client.receive():
                frame = await self._params.serializer.deserialize(payload)
                if not frame:
                    continue

                if isinstance(frame, InputAudioRawFrame):
                    await self.push_audio_frame(frame)
                else:
                    await self.push_frame(frame)
        except Exception as exc:
            logger.error(f"StasisRTPInputTransport receive error: {exc}")

    # No app-messages in RTP path, but keep compatibility
    async def push_app_message(self, message):
        """Push app message (not supported in RTP transport)."""
        logger.debug("StasisRTPInputTransport received app message ignored (RTP only)")


# ------------------------------------------------ Output Transport ------------------------


class StasisRTPOutputTransport(BaseOutputTransport):
    """Output transport for sending audio over Stasis RTP."""

    def __init__(
        self,
        transport: BaseTransport,
        client: StasisRTPClient,
        params: StasisRTPTransportParams,
        **kwargs,
    ):
        """Initialize Stasis RTP output transport.

        Args:
            transport: Parent transport instance.
            client: Stasis RTP client for socket communication.
            params: Transport parameters including serializer.
            **kwargs: Additional keyword arguments for BaseOutputTransport.
        """
        super().__init__(params, **kwargs)

        self._transport = transport
        self._client = client
        self._params = params

        # Pace outgoing audio so we don't dump buffers instantly (simulate 10-ms chunks)
        self._send_interval: float = 0
        self._next_send_time: float = 0

    async def start(self, frame: StartFrame):
        """Start the output transport."""
        await super().start(frame)

        await self._client.setup(frame)
        await self._params.serializer.setup(frame)

        self._send_interval = self._params.audio_out_10ms_chunks * 10 / 1000  # ms

        await self.set_transport_ready(frame)

    async def stop(self, frame: EndFrame):
        """Stop the output transport."""
        await super().stop(frame)
        await self._client.disconnect()

    async def cancel(self, frame: CancelFrame):
        """Cancel the output transport."""
        await super().cancel(frame)
        await self._client.disconnect()

    async def send_message(
        self, frame: TransportMessageFrame | TransportMessageUrgentFrame
    ):
        """Send message frame (not supported in RTP transport)."""
        # RTP path has no generic message channel; ignore.
        pass

    async def write_audio_frame(self, frame: OutputAudioRawFrame):
        """Write audio frame to RTP stream."""
        if self._client.is_closing:
            return False

        if not self._client.is_connected:
            # If not connected yet, just simulate playback delay.
            await self._write_audio_sleep()
            return

        payload = await self._params.serializer.serialize(frame)
        if payload:
            await self._client.send(payload)

        await self._write_audio_sleep()

    async def _write_audio_sleep(self):
        """Simulates real-time audio playback timing by introducing controlled delays.

        This method implements a clock simulation to pace audio transmission at realistic
        intervals. Without this pacing, audio frames would be sent as fast as possible,
        which could overwhelm receivers or cause buffering issues.

        The method:
        1. Calculates how long to sleep based on when the next frame should be sent
        2. Sleeps for the calculated duration (or 0 if we're already behind schedule)
        3. Updates _next_send_time for the next audio chunk

        The _send_interval is computed as: (audio_chunk_size / sample_rate) / 2
        This creates timing that simulates how an actual audio device would output
        audio at the proper rate (e.g., every 10ms for 10ms audio chunks).
        """
        current_time = time.monotonic()
        sleep_duration = max(0, self._next_send_time - current_time)
        await asyncio.sleep(sleep_duration)
        if sleep_duration == 0:
            self._next_send_time = time.monotonic() + self._send_interval
        else:
            self._next_send_time += self._send_interval


class StasisRTPTransport(BaseTransport):
    """Main transport class for Stasis RTP communication."""

    def __init__(
        self,
        stasis_connection: StasisRTPConnection,
        params: StasisRTPTransportParams,
        input_name: Optional[str] = None,
        output_name: Optional[str] = None,
    ):
        """Initialize Stasis RTP transport.

        Args:
            stasis_connection: Connection parameters for Stasis RTP.
            params: Transport parameters including serializer.
            input_name: Optional name for input transport.
            output_name: Optional name for output transport.
        """
        super().__init__(input_name=input_name, output_name=output_name)

        self._params = params

        client_callbacks = StasisRTPCallbacks(
            on_client_connected=self._on_client_connected,
            on_client_disconnected=self._on_client_disconnected,
            on_client_closed=self._on_client_closed,
        )
        self._client = StasisRTPClient(stasis_connection, client_callbacks)

        self._input = StasisRTPInputTransport(
            self, self._client, self._params, name=self._input_name
        )

        self._output = StasisRTPOutputTransport(
            self, self._client, self._params, name=self._output_name
        )

        # expose handlers
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")
        self._register_event_handler("on_client_closed")

    def input(self) -> StasisRTPInputTransport:
        """Get the input transport."""
        return self._input

    def output(self) -> StasisRTPOutputTransport:
        """Get the output transport."""
        return self._output

    # ------------------------------------------------ event adapters ----------
    async def _on_client_connected(self, chan_id: str):
        await self._call_event_handler("on_client_connected", chan_id)

    async def _on_client_disconnected(self, chan_id: str):
        await self._call_event_handler("on_client_disconnected", chan_id)

    async def _on_client_closed(self, chan_id: str):
        await self._call_event_handler("on_client_closed", chan_id)
