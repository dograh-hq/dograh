"""Omitted campaign rate limits must not become JSON null in the Python SDK."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_PY_SRC = REPO_ROOT / "sdk" / "python" / "src"
if str(SDK_PY_SRC) not in sys.path:
    sys.path.insert(0, str(SDK_PY_SRC))

from dograh_sdk._generated_models import CreateCampaignRequest  # noqa: E402


def test_omitted_rate_limit_dumps_as_one_not_null():
    body = CreateCampaignRequest(
        name="SDK campaign",
        workflow_id=1,
        source_type="csv",
        source_id="contacts.csv",
    )
    dumped = body.model_dump(mode="json")
    assert dumped["rate_limit_per_second"] == 1


def _schema_body(source: str, name: str) -> str:
    marker = f"{name}: {{"
    start = source.index(marker) + len(marker)
    depth = 1
    index = start
    while depth:
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
        index += 1
    return source[start : index - 1]


def test_typescript_create_campaign_rate_limit_stays_optional():
    source = (
        REPO_ROOT / "sdk" / "typescript" / "src" / "_generated_models.ts"
    ).read_text()
    assert "rate_limit_per_second?: number;" in source


def test_typescript_campaign_request_defaults_stay_optional():
    """Server defaults must not become required TypeScript properties.

    openapi-typescript treats a component ``default`` as required. Callers
    can omit those fields; schedule slots stay required because the API
    requires them.
    """
    source = (
        REPO_ROOT / "sdk" / "typescript" / "src" / "_generated_models.ts"
    ).read_text()
    retry = _schema_body(source, "RetryConfigRequest")
    for field in (
        "enabled",
        "max_retries",
        "retry_delay_seconds",
        "retry_on_busy",
        "retry_on_no_answer",
        "retry_on_voicemail",
    ):
        assert f"{field}?:" in retry
    schedule = _schema_body(source, "ScheduleConfigRequest")
    assert "enabled?:" in schedule
    assert "timezone?:" in schedule
    assert "slots:" in schedule
    assert "slots?:" not in schedule
    breaker = _schema_body(source, "CircuitBreakerConfigRequest")
    for field in (
        "enabled",
        "failure_threshold",
        "window_seconds",
        "min_calls_in_window",
    ):
        assert f"{field}?:" in breaker
    http = _schema_body(source, "HttpApiConfig")
    assert "timeout_ms?:" in http
    assert "body_format?:" in http
    assert "method:" in http
    assert "method?:" not in http
    assert "url:" in http
    mcp = _schema_body(source, "McpToolConfig")
    assert "timeout_secs?:" in mcp
    assert "sse_read_timeout_secs?:" in mcp
