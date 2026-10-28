from types import SimpleNamespace

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext
from pydantic import ValidationError

from api.schemas.answer_supervisor import (
    resolve_answer_supervisor_config,
    resolve_listening_window_ms,
)
from api.services.pipecat.processors.answer_supervisor import AnswerSupervisor


def resolve(config, **kwargs):
    return resolve_answer_supervisor_config(
        config,
        is_realtime=kwargs.get("realtime", False),
        start_node=kwargs.get(
            "start_node",
            SimpleNamespace(delayed_start=True, delayed_start_duration=2.5),
        ),
    )


def test_realtime_workflows_do_not_get_a_supervisor():
    assert resolve({"enabled": True}, realtime=True) is None


def test_enabling_is_the_only_switch_regardless_of_call_direction():
    """Enabled answer handling applies to both inbound and outbound calls."""
    assert resolve({"enabled": True}) is not None


@pytest.mark.parametrize("saved", [{"enabled": False}, {}])
def test_disabled_detection_still_uses_the_shared_opening(saved):
    config = resolve(saved)
    assert not config.enabled
    assert config.listening_window_ms == 2500


def test_disabled_detection_ignores_inactive_machine_settings():
    config = resolve({"enabled": False, "voicemail_action": "invalid"})
    assert not config.enabled


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


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("window", [0, 400, None, -1])
def test_ui_delayed_start_overrides_saved_listening_window(window, enabled):
    config = resolve({"enabled": enabled, "listening_window_ms": window})
    assert config is not None
    assert config.listening_window_ms == 2500


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"screening_wait_ms": -1},
        {"screening_wait_ms": -1, "classify_budget_ms": "invalid"},
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
@pytest.mark.parametrize("enabled", [False, True])
def test_ui_delayed_start_duration_is_converted_to_supervisor_window(
    duration, expected_ms, enabled
):
    node = SimpleNamespace(delayed_start=True, delayed_start_duration=duration)
    assert (
        resolve({"enabled": enabled}, start_node=node).listening_window_ms
        == expected_ms
    )
    assert resolve_listening_window_ms(node) == expected_ms


@pytest.mark.parametrize(
    "duration", [-1, 10.01, float("nan"), float("inf"), -float("inf"), "bad", True]
)
def test_delayed_start_rejects_invalid_durations_with_or_without_supervision(duration):
    node = SimpleNamespace(delayed_start=True, delayed_start_duration=duration)
    with pytest.raises(ValidationError):
        resolve_listening_window_ms(node)
    with pytest.raises(ValidationError):
        resolve({"enabled": True}, start_node=node)


@pytest.mark.parametrize("enabled", [False, True])
def test_disabled_delayed_start_does_not_apply_an_unused_invalid_duration(enabled):
    node = SimpleNamespace(delayed_start=False, delayed_start_duration=float("nan"))
    assert resolve_listening_window_ms(node) == 0
    assert resolve({"enabled": enabled}, start_node=node).listening_window_ms == 0


@pytest.mark.parametrize(
    "node", [None, SimpleNamespace(delayed_start=False, delayed_start_duration=9)]
)
@pytest.mark.parametrize("enabled", [False, True])
def test_without_delayed_start_there_is_no_hidden_listening_window(node, enabled):
    assert resolve({"enabled": enabled}, start_node=node).listening_window_ms == 0


@pytest.mark.parametrize("enabled", [False, True])
def test_legacy_detector_window_cannot_enable_delayed_start(enabled):
    config = resolve({"enabled": enabled, "listening_window_ms": 400}, start_node=None)
    assert config.listening_window_ms == 0
