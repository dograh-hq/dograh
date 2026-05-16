from pipecat.services.openai.realtime.events import (
    AudioConfiguration,
    AudioInput,
    AudioOutput,
    InputAudioTranscription,
    SessionProperties,
)
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService


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

    return OpenAIRealtimeLLMService(
        api_key=api_key,
        settings=OpenAIRealtimeLLMService.Settings(
            model=model,
            session_properties=SessionProperties(**session_kwargs),
        ),
    )
