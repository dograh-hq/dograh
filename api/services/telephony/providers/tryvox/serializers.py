"""TryVox binary PCM WebSocket serializer."""

import base64
import json

import aiohttp
from loguru import logger

from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer


class TryVoxFrameSerializer(FrameSerializer):
    """Translate between Pipecat frames and TryVox's native stream protocol."""

    class InputParams(FrameSerializer.InputParams):
        tryvox_sample_rate: int = 8000
        sample_rate: int | None = None
        auto_hang_up: bool = True

    def __init__(
        self,
        *,
        call_id: str,
        auth_id: str,
        auth_token: str,
        api_base_url: str,
        params: InputParams | None = None,
    ):
        params = params or TryVoxFrameSerializer.InputParams()
        super().__init__(params)
        self._params: TryVoxFrameSerializer.InputParams = params
        self._call_id = call_id
        self._auth_id = auth_id
        self._auth_token = auth_token
        self._api_base_url = api_base_url.rstrip("/")
        self._tryvox_sample_rate = params.tryvox_sample_rate
        self._sample_rate = 0
        self._input_resampler = create_stream_resampler(
            clear_after_secs=params.resampler_clear_after_secs
        )
        self._output_resampler = create_stream_resampler(
            clear_after_secs=params.resampler_clear_after_secs
        )
        self._hangup_attempted = False

    async def setup(self, frame: StartFrame):
        self._sample_rate = self._params.sample_rate or frame.audio_in_sample_rate

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if (
            self._params.auto_hang_up
            and not self._hangup_attempted
            and isinstance(frame, (EndFrame, CancelFrame))
        ):
            self._hangup_attempted = True
            await self._hang_up_call()
            return None

        if isinstance(frame, InterruptionFrame):
            # TryVox currently guarantees ordered playAudio, but does not expose
            # a public clear-buffer command.
            return None

        if isinstance(frame, AudioRawFrame):
            audio = await self._output_resampler.resample(
                frame.audio, frame.sample_rate, self._tryvox_sample_rate
            )
            if audio is None or len(audio) == 0:
                return None
            return json.dumps(
                {
                    "type": "playAudio",
                    "data": {
                        "audioContentType": "raw",
                        "sampleRate": self._tryvox_sample_rate,
                        "audioContent": base64.b64encode(audio).decode("ascii"),
                    },
                }
            )

        if isinstance(
            frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)
        ):
            if self.should_ignore_frame(frame):
                return None
            return json.dumps(frame.message)

        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            audio = await self._input_resampler.resample(
                data, self._tryvox_sample_rate, self._sample_rate
            )
            if audio is None or len(audio) == 0:
                return None
            return InputAudioRawFrame(
                audio=audio,
                num_channels=1,
                sample_rate=self._sample_rate,
            )

        # The first text frame contains Stream parameters. It is consumed by
        # the provider handshake; tolerate any later metadata frames as well.
        try:
            json.loads(data)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid TryVox stream metadata")
        return None

    async def _hang_up_call(self) -> None:
        if not self._call_id or not self._auth_id or not self._auth_token:
            logger.warning("Cannot hang up TryVox call: incomplete call credentials")
            return

        endpoint = (
            f"{self._api_base_url}/v1/voice/accounts/"
            f"{self._auth_id}/calls/{self._call_id}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                auth = aiohttp.BasicAuth(self._auth_id, self._auth_token)
                async with session.delete(endpoint, auth=auth) as response:
                    if response.status in (204, 404, 409):
                        return
                    body = await response.text()
                    logger.error(
                        f"TryVox call hangup failed: "
                        f"status={response.status} body={body}"
                    )
        except Exception as exc:
            logger.error(f"TryVox call hangup failed: {exc}")


__all__ = ["TryVoxFrameSerializer"]
