from types import SimpleNamespace

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext
from pydantic import ValidationError

from api.schemas.answer_supervisor import resolve_answer_supervisor_config
from api.services.pipecat.processors.answer_supervisor import AnswerSupervisor


def resolve(config, **kwargs):
    return resolve_answer_supervisor_config(
        config,
        call_direction=kwargs.get("direction", "outbound"),
        is_realtime=kwargs.get("realtime", False),
        start_node=kwargs.get(
            "start_node",
            SimpleNamespace(delayed_start=True, delayed_start_duration=2.5),
        ),
    )


@pytest.mark.parametrize(
    "kwargs", [{"direction": "inbound"}, {"direction": None}, {"realtime": True}]
)
def test_supervision_is_scoped_to_outbound_cascade_calls(kwargs):
    assert resolve({"enabled": True}, **kwargs) is None


def test_disabled_handling_does_not_create_a_supervisor():
    assert resolve({"enabled": False}) is None
    assert resolve({}) is None


@pytest.mark.parametrize(
    "old_mode", [None, "legacy", "shadow", "listen", "full", "invalid"]
)
def test_existing_enabled_workflows_use_the_replacement_without_resaving(old_mode):
    saved = {
        "enabled": True,
        "system_prompt": "Old detector prompt",
        "long_speech_timeout": 8,
    }
    if old_mode is not None:
        saved["supervisor_mode"] = old_mode
    config = resolve(saved)
    assert config is not None
    supervisor = AnswerSupervisor(config, context=LLMContext())
    assert supervisor.llm_gate().closed
    assert "supervisor_mode" not in config.model_dump()
    assert "system_prompt" not in config.model_dump()


def test_delayed_start_seconds_become_listening_milliseconds():
    assert resolve({"enabled": True}).listening_window_ms == 2500


@pytest.mark.parametrize("window", [0, 400])
def test_ui_delayed_start_overrides_saved_listening_window(window):
    config = resolve({"enabled": True, "listening_window_ms": window})
    assert config is not None
    assert config.listening_window_ms == 2500


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"screening_wait_ms": -1},
        {"screening_wait_ms": -1, "classify_budget_ms": "invalid"},
        {"listening_window_ms": None},
        {"listening_window_ms": -1},
        {"voicemail_message": {"recording_pk": -1}},
        {"voicemail_action": "invalid"},
    ],
)
def test_invalid_settings_are_rejected_instead_of_repaired(invalid_fields):
    with pytest.raises(ValidationError):
        resolve({"enabled": True, **invalid_fields}, start_node=None)


@pytest.mark.parametrize(
    "duration, expected_ms",
    [(0.1, 100), (1.25, 1250), (10, 10000), (0, 0), (None, 1200)],
)
def test_ui_delayed_start_duration_is_converted_to_supervisor_window(
    duration, expected_ms
):
    node = SimpleNamespace(delayed_start=True, delayed_start_duration=duration)
    assert (
        resolve({"enabled": True}, start_node=node).listening_window_ms == expected_ms
    )


@pytest.mark.parametrize(
    "node", [None, SimpleNamespace(delayed_start=False, delayed_start_duration=9)]
)
def test_without_ui_delayed_start_the_default_window_is_used(node):
    assert resolve({"enabled": True}, start_node=node).listening_window_ms == 1200


def test_saved_window_is_only_a_fallback_without_ui_delayed_start():
    config = resolve({"enabled": True, "listening_window_ms": 400}, start_node=None)
    assert config.listening_window_ms == 400
