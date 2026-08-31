"""QA LLM failures retry when transient and report classified when final."""

from unittest.mock import AsyncMock

import loguru
import pytest

from api.errors.failure import (
    ErrorOwner,
    ErrorSource,
    annotate_failure_metadata,
    failure_already_reported,
)
from api.services.workflow.qa.analysis import _run_llm_inference


class _CapacityError(Exception):
    """Shaped like a provider 503: structural status, transient."""

    status_code = 503


class _FakeLLM:
    def __init__(self, failures: int, exc_factory):
        self._failures = failures
        self._exc_factory = exc_factory
        self.calls = 0

    async def run_inference(self, context, system_instruction=None):
        self.calls += 1
        if self.calls <= self._failures:
            raise self._exc_factory()
        return '{"tags": []}'


def _qa_llm(failures: int, exc_factory) -> _FakeLLM:
    return annotate_failure_metadata(
        _FakeLLM(failures, exc_factory),
        source=ErrorSource.LLM,
        provider="google",
        error_owner=ErrorOwner.USER,
    )


def _capture():
    records = []
    handler_id = loguru.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    return records, handler_id


def _failure_records(records: list) -> list:
    return [r for r in records if r["extra"].get("error_code")]


async def test_transient_failure_is_retried_to_success(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    llm = _qa_llm(failures=2, exc_factory=_CapacityError)
    records, handler_id = _capture()

    try:
        result = await _run_llm_inference(llm, [], "prompt", workflow_run_id=620156)
    finally:
        loguru.logger.remove(handler_id)

    assert result == '{"tags": []}'
    assert llm.calls == 3
    assert _failure_records(records) == []


async def test_persistent_transient_failure_reports_after_exhausting_retries(
    monkeypatch,
):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    llm = _qa_llm(failures=99, exc_factory=_CapacityError)
    records, handler_id = _capture()

    try:
        with pytest.raises(_CapacityError):
            await _run_llm_inference(llm, [], "prompt", workflow_run_id=620156)
    finally:
        loguru.logger.remove(handler_id)

    assert llm.calls == 3
    failures = _failure_records(records)
    assert len(failures) == 1
    assert failures[0]["extra"]["error_type"] == "provider_error"
    assert failures[0]["extra"]["qa_attempts"] == 3


async def test_non_retryable_failure_raises_immediately_and_is_marked():
    llm = _qa_llm(failures=99, exc_factory=lambda: ValueError("bad request"))
    records, handler_id = _capture()

    try:
        with pytest.raises(ValueError) as excinfo:
            await _run_llm_inference(llm, [], "prompt", workflow_run_id=620156)
    finally:
        loguru.logger.remove(handler_id)

    assert llm.calls == 1
    assert failure_already_reported(excinfo.value)
    failures = _failure_records(records)
    assert len(failures) == 1
    assert failures[0]["level"].name == "ERROR"


async def test_failure_severity_follows_the_caller(monkeypatch):
    llm = _qa_llm(failures=99, exc_factory=lambda: ValueError("bad request"))
    records, handler_id = _capture()

    try:
        with pytest.raises(ValueError):
            await _run_llm_inference(llm, [], "prompt", failure_log_level="WARNING")
    finally:
        loguru.logger.remove(handler_id)

    failures = _failure_records(records)
    assert len(failures) == 1
    assert failures[0]["level"].name == "WARNING"
