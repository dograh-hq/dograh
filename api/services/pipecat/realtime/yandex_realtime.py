"""Dograh subclass of pipecat's OpenAI Realtime LLM service, pointed at Yandex
Cloud's OpenAI-Realtime-wire-compatible endpoint.

Yandex Cloud's Realtime API (``wss://ai.api.cloud.yandex.net/v1/realtime/openai``)
speaks the same event protocol as OpenAI's Realtime API — confirmed against a
user-supplied example script exercising session setup, audio streaming,
barge-in, and function calling. The only behavioral delta from
``DograhOpenAIRealtimeLLMService`` is the default connection URL and the
auth header (``Api-Key`` instead of ``Bearer``); everything else — session
management, event parsing, tool-calling glue, turn-taking, and all of
Dograh's engine-integration behavior — is inherited unmodified.
"""

from websockets.asyncio.client import connect as websocket_connect

from api.services.pipecat.realtime.openai_realtime import (
    DograhOpenAIRealtimeLLMService,
)

YANDEX_REALTIME_BASE_URL = "wss://ai.api.cloud.yandex.net/v1/realtime/openai"


class DograhYandexRealtimeLLMService(DograhOpenAIRealtimeLLMService):
    """OpenAI Realtime wire-protocol client pointed at Yandex Cloud."""

    def __init__(self, **kwargs):
        kwargs.setdefault("base_url", YANDEX_REALTIME_BASE_URL)
        super().__init__(**kwargs)

    async def _connect(self):
        try:
            if self._websocket:
                # Here we assume that if we have a websocket, we are connected. We
                # handle disconnections in the send/recv code paths.
                return
            self._websocket = await websocket_connect(
                uri=self.base_url,
                additional_headers={
                    "Authorization": f"Api-Key {self.api_key}",
                },
            )
            self._receive_task = self.create_task(self._receive_task_handler())
        except Exception as e:
            await self.push_error(error_msg=f"Error connecting: {e}", exception=e)
            self._websocket = None
