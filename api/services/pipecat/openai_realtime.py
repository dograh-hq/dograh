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

    Temporary workaround for Pipecat's OpenAI Realtime tool-result serialization.
    The upstream async-tool flow is now handled by Pipecat itself; we only keep
    the output payload shape correction here until upstream matches the API.
    """

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
