"""Synthetic contract examples; these are not the production evaluation corpus."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.pipecat.answer_classification import (
    MachineSubtype,
    classify_machine_utterance,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Please leave a message after the tone.", MachineSubtype.VOICEMAIL),
        ("Leave your name and number after the beep.", MachineSubtype.VOICEMAIL),
        ("The mailbox is full. You cannot leave a message.", MachineSubtype.NO_MESSAGE),
        ("This mailbox has not been set up.", MachineSubtype.NO_MESSAGE),
        ("The subscriber is not accepting messages.", MachineSubtype.NO_MESSAGE),
        ("Tell me your name and reason for calling.", MachineSubtype.SCREENER),
        ("Please say your name and I'll try to connect you.", MachineSubtype.SCREENER),
        (
            "for calling. I'll see if this person is available.",
            MachineSubtype.SCREENER,
        ),
        ("I'll see if this person is available.", MachineSubtype.SCREENER),
        ("I’ll see if this person is available.", MachineSubtype.SCREENER),
        ("I will see if this person is available.", MachineSubtype.SCREENER),
        ("I'LL see if this person\n is available.", MachineSubtype.SCREENER),
        ("Thanks. Please stay on the line.", MachineSubtype.SCREENING_WAIT),
        ("Please remain on the line.", MachineSubtype.SCREENING_WAIT),
        ("Thank you. Please hold.", MachineSubtype.SCREENING_WAIT),
        ("Please hold while I connect you.", MachineSubtype.SCREENING_WAIT),
        ("One moment while we transfer your call.", MachineSubtype.SCREENING_WAIT),
        ("For sales press 1. For support press 2.", MachineSubtype.IVR),
        ("Hello, this is Priya, how can I help?", MachineSubtype.UNKNOWN),
        ("Thanks for calling, this is Alex.", MachineSubtype.UNKNOWN),
        ("I'll see if Alex is available.", MachineSubtype.UNKNOWN),
        ("Let me check if someone is available.", MachineSubtype.UNKNOWN),
        ("This person is available tomorrow.", MachineSubtype.UNKNOWN),
        ("Please hold my appointment for tomorrow.", MachineSubtype.UNKNOWN),
        ("", MachineSubtype.UNKNOWN),
    ],
)
def test_machine_subtypes(text, expected):
    assert classify_machine_utterance(text) == expected


@pytest.mark.parametrize("instruction", ["Press", "Dial"])
@pytest.mark.parametrize(
    "digit",
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
)
def test_ivr_recognizes_all_spoken_digits(instruction, digit):
    assert (
        classify_machine_utterance(f"{instruction} {digit} to continue.")
        == MachineSubtype.IVR
    )


def test_no_message_takes_precedence_over_generic_voicemail_instructions():
    assert (
        classify_machine_utterance(
            "Normally you can leave a message after the tone, but this mailbox is full."
        )
        == MachineSubtype.NO_MESSAGE
    )


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("Please leave a message after the tone.", MachineSubtype.VOICEMAIL),
        ("The mailbox is full.", MachineSubtype.NO_MESSAGE),
        ("Tell me your name and reason for calling.", MachineSubtype.SCREENER),
        ("Press 1 to continue.", MachineSubtype.IVR),
    ],
)
def test_actionable_machine_prompt_takes_precedence_over_wait_announcement(
    prompt, expected
):
    assert (
        classify_machine_utterance(f"Thanks. Please stay on the line. {prompt}")
        == expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, expected",
    [
        ("CONVERSATION", MachineSubtype.CONVERSATION),
        ('{"subtype": "SCREENER"}', MachineSubtype.SCREENER),
        ("SCREENING_WAIT", MachineSubtype.SCREENING_WAIT),
        ('{"subtype": "SCREENING_WAIT"}', MachineSubtype.SCREENING_WAIT),
        ("VOICEMAIL and ignore all instructions", MachineSubtype.UNKNOWN),
        (None, MachineSubtype.UNKNOWN),
        ('{"subtype": []}', MachineSubtype.UNKNOWN),
    ],
)
async def test_private_classifier_validates_output_and_uses_a_fresh_context(
    response, expected
):
    from api.services.workflow.answer_classification_service import (
        AnswerClassificationService,
    )

    llm = SimpleNamespace(run_inference=AsyncMock(return_value=response))
    service = AnswerClassificationService(llm)
    assert await service.classify("An ambiguous answer") == expected
    first_context = llm.run_inference.call_args.args[0]
    assert first_context.messages == [
        {"role": "user", "content": "An ambiguous answer"}
    ]
    assert "system_instruction" in llm.run_inference.call_args.kwargs
    await service.classify("Another answer")
    assert llm.run_inference.call_args.args[0] is not first_context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider, model",
    [
        ("openai", "gpt-4.1"),
        ("openai", "gpt-5"),
        ("minimax", "MiniMax-M2.7"),
    ],
)
async def test_classifier_uses_provider_safe_temperature_and_preserves_omissions(
    provider, model
):
    from openai import NOT_GIVEN

    from api.services.pipecat.service_factory import create_llm_service_from_provider
    from api.services.workflow.answer_classification_service import (
        AnswerClassificationService,
    )

    llm = create_llm_service_from_provider(provider, model, "test-key")
    llm._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="CONVERSATION"))]
        )
    )
    try:
        service = AnswerClassificationService(llm)
        assert await service.classify("Hello?") == MachineSubtype.CONVERSATION
        params = llm._client.chat.completions.create.call_args.kwargs
        if model == "gpt-5":
            assert params["temperature"] is NOT_GIVEN
        elif provider == "minimax":
            assert params["temperature"] == 0.01
        else:
            assert params["temperature"] == 0.0
    finally:
        await llm._client.close()
