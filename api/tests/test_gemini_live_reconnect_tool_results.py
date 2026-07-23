import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.frames.frames import (
    EndFrame,
    NodeTransitionStartedFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallFromLLM

from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService


class _TestDograhGeminiLiveLLMService(DograhGeminiLiveLLMService):
    """Dograh Gemini service with client creation stubbed for unit tests."""

    def create_client(self):
        self._client = SimpleNamespace(
            aio=SimpleNamespace(live=SimpleNamespace(connect=None))
        )


class _FakeSession:
    def __init__(self):
        self.send_client_content = AsyncMock()
        self.send_tool_response = AsyncMock()
        self.send_realtime_input = AsyncMock()
        self.close = AsyncMock()


def _make_service() -> _TestDograhGeminiLiveLLMService:
    service = _TestDograhGeminiLiveLLMService(api_key="test-key")
    service.stop_all_metrics = AsyncMock()
    service.start_ttfb_metrics = AsyncMock()
    service.cancel_task = AsyncMock()
    service.push_error = AsyncMock()
    return service


def _make_tool_result_context(tool_call_id: str) -> LLMContext:
    return LLMContext(
        messages=[
            {
                "role": "tool",
                "content": json.dumps({"status": "done"}),
                "tool_call_id": tool_call_id,
            }
        ]
    )


@pytest.mark.asyncio
async def test_updated_context_during_reconnect_keeps_result_pending_until_session_ready():
    service = _make_service()
    service._handled_initial_context = True
    service._tool_call_id_to_name = {"call-transition": "transition_to_next_node"}
    service._session = _FakeSession()

    context = _make_tool_result_context("call-transition")

    await service._disconnect()
    await service._handle_context(context)

    # A reconnect gap should not count as successful delivery to Gemini.
    assert "call-transition" not in service._completed_tool_calls

    session = _FakeSession()
    await service._handle_session_ready(session)

    session.send_tool_response.assert_awaited_once()
    sent_response = session.send_tool_response.await_args.kwargs["function_responses"]
    assert sent_response.id == "call-transition"
    assert sent_response.name == "transition_to_next_node"
    assert "call-transition" in service._completed_tool_calls


@pytest.mark.asyncio
async def test_disconnect_does_not_forget_previously_delivered_tool_results():
    service = _make_service()
    service._context = _make_tool_result_context("call-transition")
    service._completed_tool_calls = {"call-transition"}
    service._tool_call_id_to_name = {"call-transition": "transition_to_next_node"}
    service._session = _FakeSession()
    service._tool_result = AsyncMock()

    await service._disconnect()
    await service._process_completed_function_calls(send_new_results=True)

    service._tool_result.assert_not_awaited()
    assert service._completed_tool_calls == {"call-transition"}


@pytest.mark.asyncio
async def test_user_transcription_matches_upstream_upstream_push_behavior():
    service = _make_service()
    service._handle_user_transcription = AsyncMock()
    service.push_frame = AsyncMock()
    service.broadcast_frame = AsyncMock()

    await service._push_user_transcription("Hi there")

    service._handle_user_transcription.assert_awaited_once_with(
        "Hi there", True, service._settings.language
    )
    service.broadcast_frame.assert_not_awaited()
    service.push_frame.assert_awaited_once()

    frame, direction = service.push_frame.await_args.args
    assert isinstance(frame, TranscriptionFrame)
    assert frame.text == "Hi there"
    assert frame.finalized is False
    assert direction == FrameDirection.UPSTREAM


@pytest.mark.asyncio
async def test_tts_greeting_sends_exact_static_greeting_prompt_to_gemini():
    service = _make_service()
    service._context = LLMContext()
    service._session = _FakeSession()

    await service.process_frame(
        TTSSpeakFrame("Hi Sam, this is Sarah from Acme.", append_to_context=True),
        FrameDirection.DOWNSTREAM,
    )

    service._session.send_client_content.assert_awaited_once()
    kwargs = service._session.send_client_content.await_args.kwargs
    assert kwargs["turn_complete"] is True

    turns = kwargs["turns"]
    assert len(turns) == 1
    assert turns[0].role == "user"
    prompt = turns[0].parts[0].text
    assert "The phone call has just connected. Greet the caller now:" in prompt
    assert (
        'Do not add anything before or after it.\n\n"Hi Sam, this is Sarah from Acme."'
        in prompt
    )

    assert service._handled_initial_context is True
    assert service._pending_initial_greeting_text is None
    assert service._ready_for_realtime_input is True


@pytest.mark.asyncio
async def test_tts_greeting_waits_for_gemini_session_before_sending_prompt():
    service = _make_service()
    service._context = LLMContext()

    await service.process_frame(
        TTSSpeakFrame("Hello from Dograh.", append_to_context=True),
        FrameDirection.DOWNSTREAM,
    )

    assert service._handled_initial_context is True
    assert service._run_llm_when_session_ready is True
    assert service._pending_initial_greeting_text == "Hello from Dograh."

    session = _FakeSession()
    await service._handle_session_ready(session)

    session.send_client_content.assert_awaited_once()
    prompt = session.send_client_content.await_args.kwargs["turns"][0].parts[0].text
    assert prompt.endswith('"Hello from Dograh."')
    assert service._run_llm_when_session_ready is False
    assert service._pending_initial_greeting_text is None


@pytest.mark.asyncio
async def test_transition_call_flushes_pending_transcription_before_execution():
    service = _make_service()
    service._NODE_TRANSITION_TRANSCRIPTION_GRACE_SECONDS = 0
    service.register_function(
        "transition_to_next_node",
        AsyncMock(),
        is_node_transition=True,
    )
    service._user_transcription_buffer = "My last answer"

    events = []

    async def _push_transcription(text, result=None):
        events.append(("transcription", text))

    async def _run_function_calls(function_calls):
        events.append(("function", function_calls[0].function_name))

    service._push_user_transcription = AsyncMock(side_effect=_push_transcription)
    service.run_function_calls = AsyncMock(side_effect=_run_function_calls)
    service.create_task = lambda coro, name=None: asyncio.create_task(coro, name=name)

    function_call = FunctionCallFromLLM(
        context=LLMContext(),
        tool_call_id="call-transition",
        function_name="transition_to_next_node",
        arguments={},
    )

    await service._run_or_defer_function_calls([function_call])
    transition_task = service._transition_function_call_task
    assert transition_task is not None
    await transition_task

    assert events == [
        ("transcription", "My last answer"),
        ("function", "transition_to_next_node"),
    ]
    assert service._user_transcription_buffer == ""


@pytest.mark.asyncio
async def test_non_transition_call_is_not_deferred_while_bot_is_responding():
    service = _make_service()
    service._bot_is_responding = True
    service.run_function_calls = AsyncMock()

    function_call = FunctionCallFromLLM(
        context=LLMContext(),
        tool_call_id="call-ordinary",
        function_name="look_up_account",
        arguments={},
    )

    await service._run_or_defer_function_calls([function_call])

    service.run_function_calls.assert_awaited_once_with([function_call])
    assert service._pending_node_transition_function_calls == []
    assert service._transition_function_call_task is None


@pytest.mark.asyncio
async def test_node_transition_call_is_deferred_while_bot_is_responding():
    service = _make_service()
    service._bot_is_responding = True
    service.register_function(
        "transition_to_next_node",
        AsyncMock(),
        is_node_transition=True,
    )
    service.run_function_calls = AsyncMock()

    function_call = FunctionCallFromLLM(
        context=LLMContext(),
        tool_call_id="call-transition",
        function_name="transition_to_next_node",
        arguments={},
    )

    await service._run_or_defer_function_calls([function_call])

    service.run_function_calls.assert_not_awaited()
    assert service._pending_node_transition_function_calls == [function_call]
    assert service._transition_function_call_task is None


@pytest.mark.asyncio
async def test_initial_system_instruction_update_is_not_a_node_handoff():
    service = _make_service()
    service._connect = AsyncMock()
    service._disconnect = AsyncMock()

    handled = await service._handle_changed_settings(
        {"system_instruction": "old prompt"}
    )

    assert handled == {"system_instruction"}
    service._connect.assert_awaited_once_with()
    service._disconnect.assert_not_awaited()
    assert service._awaiting_node_transition_context is False


@pytest.mark.asyncio
async def test_node_transition_uses_fresh_connection_instead_of_stale_handle():
    service = _make_service()
    service._session = _FakeSession()
    service._session_resumption_handle = "stale-handle"
    service._disconnect = AsyncMock()
    service._connect = AsyncMock()

    handled = await service._handle_changed_settings(
        {"system_instruction": "old prompt"}
    )

    assert handled == {"system_instruction"}
    assert service._session_resumption_handle is None
    assert service._awaiting_node_transition_context is True
    service._disconnect.assert_awaited_once_with(preserve_pending_end_frame=True)
    service._connect.assert_awaited_once_with(session_resumption_handle=None)


@pytest.mark.asyncio
async def test_node_transition_releases_deferred_end_frame_instead_of_reconnecting():
    service = _make_service()
    service._session = _FakeSession()
    service._connect = AsyncMock()
    service.queue_frame = AsyncMock()
    service._bot_is_responding = True

    end_frame = EndFrame(reason="user_idle_max_duration_exceeded")
    timeout_task = MagicMock()
    timeout_task.done.return_value = False
    service._end_frame_pending_bot_turn_finished = end_frame
    service._end_frame_deferral_timeout_task = timeout_task

    await service._reconnect_for_node_transition()

    service.queue_frame.assert_awaited_once_with(end_frame)
    service._connect.assert_not_awaited()
    timeout_task.cancel.assert_called_once_with()
    assert service._end_frame_pending_bot_turn_finished is None
    assert service._end_frame_deferral_timeout_task is None
    assert service._awaiting_node_transition_context is False
    assert service._node_transition_context_received is False
    assert service._node_transition_context_seed_started is False


@pytest.mark.asyncio
async def test_fresh_transition_session_waits_for_updated_context_before_ready():
    service = _make_service()
    service._handled_initial_context = True
    service._awaiting_node_transition_context = True
    service._node_transition_context_received = False
    service._process_completed_function_calls = AsyncMock()
    service._drain_pending_tool_results = AsyncMock()

    async def _seed_context():
        service._ready_for_realtime_input = True

    service._create_initial_response = AsyncMock(side_effect=_seed_context)

    session = _FakeSession()
    await service._handle_session_ready(session)

    assert service._ready_for_realtime_input is False
    service._create_initial_response.assert_not_awaited()

    context = _make_tool_result_context("call-transition")
    await service._handle_context(context)

    service._process_completed_function_calls.assert_awaited_once_with(
        send_new_results=False
    )
    service._create_initial_response.assert_awaited_once()
    service._drain_pending_tool_results.assert_awaited_once()
    assert service._ready_for_realtime_input is True
    assert service._awaiting_node_transition_context is False


@pytest.mark.asyncio
async def test_node_transition_frame_commits_user_transcript_to_context():
    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(context, realtime_service_mode=True)
    user_aggregator = context_aggregator.user()
    user_aggregator.push_context_frame = AsyncMock()

    await user_aggregator._handle_transcription(
        TranscriptionFrame(
            text="The answer that selected this node",
            user_id="",
            timestamp="now",
        )
    )
    context_aggregation_event = asyncio.Event()
    await user_aggregator._handle_node_transition_started(
        NodeTransitionStartedFrame(
            function_calls=[
                FunctionCallFromLLM(
                    context=context,
                    tool_call_id="call-transition",
                    function_name="transition_to_next_node",
                    arguments={},
                )
            ],
            context_aggregation_event=context_aggregation_event,
        )
    )

    assert context.messages[-1] == {
        "role": "user",
        "content": "The answer that selected this node",
    }
    user_aggregator.push_context_frame.assert_awaited_once()
    assert context_aggregation_event.is_set()
