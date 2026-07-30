"""PoC test for #583: OpenAI Realtime speaks post-greeting fixed messages.

Verifies the frame-routing fix — that a TTSSpeakFrame arriving after the
greeting (an end-call goodbye or node-transition line) triggers a forced
"say this verbatim" response instead of being silently dropped. The live
audio/ordering behavior still needs verification on a real OpenAI Realtime
call; this only proves the routing.
"""

from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.realtime.openai_realtime import (
    DograhOpenAIRealtimeLLMService,
)
from api.services.pipecat.realtime.static_greeting import format_say_verbatim_prompt


def _make_service(**overrides):
    """Build a service instance without running the heavy pipecat __init__,
    setting only the attributes the TTSSpeakFrame path touches."""
    svc = object.__new__(DograhOpenAIRealtimeLLMService)
    svc._name = "DograhOpenAIRealtimeLLMService#test"
    svc._handled_initial_context = True
    svc._disconnecting = False
    svc._api_session_ready = True
    svc._ensure_conversation_setup = AsyncMock()
    svc._send_manual_response_create = AsyncMock()
    for key, value in overrides.items():
        setattr(svc, key, value)
    return svc


@pytest.mark.asyncio
async def test_post_greeting_message_is_spoken_verbatim():
    svc = _make_service()
    message = "Thanks for calling, goodbye!"

    await svc.process_frame(TTSSpeakFrame(message), FrameDirection.DOWNSTREAM)

    svc._send_manual_response_create.assert_awaited_once()
    kwargs = svc._send_manual_response_create.await_args.kwargs
    assert kwargs["tool_choice"] == "none"
    assert kwargs["instructions"] == format_say_verbatim_prompt(message)


@pytest.mark.asyncio
async def test_empty_post_greeting_message_is_ignored():
    svc = _make_service()

    await svc.process_frame(TTSSpeakFrame("   "), FrameDirection.DOWNSTREAM)

    svc._send_manual_response_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_verbatim_message_skipped_before_session_ready():
    svc = _make_service(_api_session_ready=False)

    await svc.process_frame(TTSSpeakFrame("goodbye"), FrameDirection.DOWNSTREAM)

    svc._send_manual_response_create.assert_not_awaited()


def test_say_verbatim_prompt_is_greeting_agnostic():
    prompt = format_say_verbatim_prompt("Bye now")
    assert "Bye now" in prompt
    # Unlike the greeting prompt, it must not assume the call just connected.
    assert "just connected" not in prompt
