import json
from typing import Any

from pipecat.services.openai.realtime.events import (
    AudioConfiguration,
    AudioInput,
    AudioOutput,
    ConversationItem,
    ConversationItemCreateEvent,
    InputAudioTranscription,
    SessionProperties,
)
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService


class DograhOpenAIRealtimeLLMService(OpenAIRealtimeLLMService):
    """OpenAI Realtime service with Dograh async tool result forwarding.

    Temporary workaround for Pipecat's OpenAI Realtime async tool completion flow.
    Remove this override once upstream handles async tool markers and final results
    without double-encoding or premature completion.
    """

    async def _process_completed_function_calls(self, send_new_results: bool):
        # Workaround: Pipecat's stock OpenAI Realtime handler treats async
        # running markers as completed tool output and ignores the later
        # developer-role finished payload. We keep the normal sync path and
        # only forward the async final result here until upstream fixes that.
        sent_new_result = False
        for message in self._context.get_messages():
            if not message.get("role") or message.get("content") == "IN_PROGRESS":
                continue

            tool_call_id = message.get("tool_call_id")
            if tool_call_id:
                if self._is_async_tool_marker(message.get("content")):
                    continue
                if tool_call_id not in self._completed_tool_calls:
                    if send_new_results:
                        sent_new_result = True
                        await self._send_tool_result(tool_call_id, message.get("content"))
                    self._completed_tool_calls.add(tool_call_id)
                continue

            payload = self._async_tool_payload(message.get("content"))
            if not payload or payload.get("status") != "finished":
                continue

            tool_call_id = payload.get("tool_call_id")
            if tool_call_id and tool_call_id not in self._completed_tool_calls:
                if send_new_results:
                    sent_new_result = True
                    await self._send_tool_result(
                        tool_call_id,
                        payload.get("result", message.get("content")),
                    )
                self._completed_tool_calls.add(tool_call_id)

        if sent_new_result:
            await self._create_response()

    async def _send_tool_result(self, tool_call_id: str, result: str):
        await self.send_client_event(
            ConversationItemCreateEvent(
                item=ConversationItem(
                    type="function_call_output",
                    call_id=tool_call_id,
                    output=result,
                )
            )
        )

    @classmethod
    def _is_async_tool_marker(cls, content: Any) -> bool:
        payload = cls._async_tool_payload(content)
        return bool(payload and payload.get("status") == "running")

    @staticmethod
    def _async_tool_payload(content: Any) -> dict[str, Any] | None:
        if not isinstance(content, str):
            return None
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict) and payload.get("type") == "async_tool":
            return payload
        return None


def create_openai_realtime_llm_service(
    *,
    api_key: str,
    model: str,
    voice: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
) -> OpenAIRealtimeLLMService:
    """Build an OpenAI realtime service with the voice/audio/tool mapping we need.

    OpenAI Realtime is speech-to-speech: the model handles input audio, output
    audio, and tool calls within the same session. We explicitly map the
    session audio config here so the rest of the pipeline can stay provider-agnostic.
    """

    session_kwargs: dict[str, object] = {
        "audio": AudioConfiguration(
            input=AudioInput(
                transcription=InputAudioTranscription(),
            ),
            output=AudioOutput(
                voice=voice or "alloy",
            ),
        ),
    }

    if tools:
        session_kwargs["tools"] = tools
        session_kwargs["tool_choice"] = tool_choice or "auto"

    return DograhOpenAIRealtimeLLMService(
        api_key=api_key,
        settings=DograhOpenAIRealtimeLLMService.Settings(
            model=model,
            session_properties=SessionProperties(**session_kwargs),
        ),
        enable_async_tool_cancellation=True,
    )
