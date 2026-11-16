"""Sequence Gnani synthesis requests on a shared WebSocket."""

import asyncio

from pipecat_gnani import GnaniTTSService


class DograhGnaniTTSService(GnaniTTSService):
    """Keep each untagged Gnani response attached to the sentence that requested it."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._request_context_id: str | None = None
        self._request_lock = asyncio.Lock()

    async def run_tts(self, text: str, context_id: str):
        # The socket accepts the next request after the previous one completes.
        await self._request_lock.acquire()
        self._request_context_id = context_id
        async for frame in super().run_tts(text, context_id):
            yield frame

    def get_active_audio_context_id(self) -> str | None:
        # The receiver can get the next response before Pipecat drains playback.
        return self._request_context_id

    def _release_request(self):
        if self._request_context_id is not None:
            self._request_context_id = None
            self._request_lock.release()

    async def _finish_audio_context(self):
        await super()._finish_audio_context()
        self._release_request()

    async def _disconnect_websocket(self):
        await super()._disconnect_websocket()
        self._release_request()

    async def on_audio_context_completed(self, context_id: str):
        if self._request_context_id == context_id:
            # Playback timed out without a terminal response. A late chunk from
            # this socket must never be attributed to the next request.
            await self._disconnect_websocket()

    async def on_audio_context_interrupted(self, context_id: str):
        if self._request_context_id == context_id:
            # An interruption can arrive before Pipecat announces playback.
            await self._disconnect_websocket()
