from types import SimpleNamespace

import httpx
import pytest
from loguru import logger

from api.errors import failure as failure_module
from api.errors.failure import (
    DograhFailure,
    ErrorOwner,
    ErrorSource,
    ErrorType,
    annotate_failure_metadata,
    classify_exception,
    classify_http_response,
    failure_metadata_for_processor,
    log_failure,
    redact_failure_message,
)
from api.services.configuration.registry import REGISTRY, ServiceType


@pytest.mark.parametrize(
    ("status_code", "expected_type", "retryable"),
    [
        (400, ErrorType.CONFIG_ERROR, False),
        (401, ErrorType.CONFIG_ERROR, False),
        (403, ErrorType.CONFIG_ERROR, False),
        (404, ErrorType.CONFIG_ERROR, False),
        (402, ErrorType.QUOTA_ERROR, False),
        (429, ErrorType.QUOTA_ERROR, False),
        (408, ErrorType.PROVIDER_ERROR, True),
        (500, ErrorType.PROVIDER_ERROR, True),
        (503, ErrorType.PROVIDER_ERROR, True),
        (302, ErrorType.SYSTEM_ERROR, None),
    ],
)
def test_classify_http_response_status_bands(status_code, expected_type, retryable):
    failure = classify_http_response(
        status_code,
        f"provider returned HTTP {status_code}",
        source=ErrorSource.STT,
        provider="deepgram",
        error_owner=ErrorOwner.USER,
    )

    assert failure.type == expected_type
    assert failure.code == f"deepgram-{status_code}"
    assert failure.retryable is retryable
    expected_owner = (
        ErrorOwner.OPERATOR
        if expected_type == ErrorType.SYSTEM_ERROR
        else ErrorOwner.USER
    )
    assert failure.error_owner == expected_owner


@pytest.mark.parametrize(
    ("error_type", "requested_owner", "expected_owner"),
    [
        (ErrorType.CONFIG_ERROR, None, ErrorOwner.USER),
        (ErrorType.QUOTA_ERROR, None, ErrorOwner.USER),
        (ErrorType.PROVIDER_ERROR, ErrorOwner.USER, ErrorOwner.USER),
        (ErrorType.PROVIDER_ERROR, ErrorOwner.OPERATOR, ErrorOwner.OPERATOR),
        (ErrorType.PROVIDER_ERROR, None, ErrorOwner.OPERATOR),
        (ErrorType.SYSTEM_ERROR, ErrorOwner.USER, ErrorOwner.OPERATOR),
    ],
)
def test_failure_resolves_binary_owner(error_type, requested_owner, expected_owner):
    failure = DograhFailure(
        source=ErrorSource.PLATFORM,
        type=error_type,
        code="test-failure",
        internal_message="Test failure",
        external_message="Test failure",
        error_owner=requested_owner,
    )

    assert failure.error_owner == expected_owner


class _StatusException(Exception):
    def __init__(self, message, **attrs):
        super().__init__(message)
        for key, value in attrs.items():
            setattr(self, key, value)


def test_status_extraction_skips_vendor_code_and_uses_http_status():
    exc = _StatusException(
        'Status 401: {"code": 20003, "message": "Authenticate"}',
        code=20003,
        status=401,
    )

    failure = classify_exception(exc, source=ErrorSource.TELEPHONY, provider="twilio")

    assert failure.type == ErrorType.CONFIG_ERROR
    assert failure.code == "twilio-401"
    assert failure.provider_error_code == "20003"


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (httpx.ReadTimeout("read timed out"), "custom-http-timeout"),
        (httpx.ConnectError("DNS lookup failed"), "custom-http-connection"),
        (ConnectionError("connection reset"), "custom-http-connection"),
        (OSError("socket closed"), "custom-http-connection"),
    ],
)
def test_network_exceptions_are_provider_errors(exc, expected_code):
    failure = classify_exception(exc, source=ErrorSource.TOOL, provider="custom_http")

    assert failure.type == ErrorType.PROVIDER_ERROR
    assert failure.code == expected_code
    assert failure.retryable is True


def test_ambiguous_exception_defaults_to_system_error():
    failure = classify_exception(
        ValueError("unexpected response shape"),
        source=ErrorSource.LLM,
        provider="openai",
    )

    assert failure.type == ErrorType.SYSTEM_ERROR
    assert failure.code == "openai-unknown"


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        ("elevenlabs", "quota_exceeded for this subscription"),
        ("elevenlabs", "voice_not_found"),
        ("deepgram", "NET-0001 connection lost"),
        ("google_vertex_realtime", "UNAVAILABLE"),
        (
            "sarvam",
            "Text must contain at least one character from the allowed languages",
        ),
    ],
)
def test_unstructured_provider_text_never_determines_attribution(provider, message):
    failure = classify_exception(
        RuntimeError(message), source=ErrorSource.TTS, provider=provider
    )

    assert failure.type == ErrorType.SYSTEM_ERROR
    assert failure.code == f"{provider.replace('_', '-')}-unknown"


def test_structured_provider_code_is_metadata_only():
    failure = classify_exception(
        _StatusException("connection lost", error_code="NET-0001"),
        source=ErrorSource.STT,
        provider="deepgram",
    )

    assert failure.type == ErrorType.SYSTEM_ERROR
    assert failure.code == "deepgram-unknown"
    assert failure.provider_error_code == "net-0001"


def test_http_status_determines_type_while_vendor_code_remains_metadata():
    failure = classify_exception(
        _StatusException(
            "subscription limit reached",
            status_code=429,
            error_code="quota_exceeded",
        ),
        source=ErrorSource.TTS,
        provider="elevenlabs",
    )

    assert failure.type == ErrorType.QUOTA_ERROR
    assert failure.code == "elevenlabs-429"
    assert failure.provider_error_code == "quota-exceeded"


def test_google_http_statuses_are_distinct_without_message_parsing():
    not_found = classify_exception(
        _StatusException("NOT_FOUND: bad model", code=404, status="NOT_FOUND"),
        source=ErrorSource.LLM,
        provider="google",
    )
    unavailable = classify_exception(
        _StatusException("UNAVAILABLE", code=503, status="UNAVAILABLE"),
        source=ErrorSource.LLM,
        provider="google",
    )

    assert not_found.type == ErrorType.CONFIG_ERROR
    assert not_found.code == "google-404"
    assert unavailable.type == ErrorType.PROVIDER_ERROR
    assert unavailable.code == "google-503"


def test_dograh_boundary_changes_attribution():
    quota = classify_http_response(
        402,
        "Insufficient Dograh credits",
        source=ErrorSource.LLM,
        provider="dograh",
    )
    managed_key_failure = classify_http_response(
        401,
        "Managed provider rejected the service key",
        source=ErrorSource.LLM,
        provider="dograh",
    )

    assert quota.type == ErrorType.QUOTA_ERROR
    assert quota.error_owner == ErrorOwner.USER
    assert quota.code == "dograh-insufficient-credits"
    assert managed_key_failure.type == ErrorType.SYSTEM_ERROR
    assert managed_key_failure.error_owner == ErrorOwner.OPERATOR


def test_dograh_credit_prose_does_not_override_safe_default():
    failure = classify_exception(
        RuntimeError("insufficient credits"),
        source=ErrorSource.PLATFORM,
        provider="dograh",
    )

    assert failure.type == ErrorType.SYSTEM_ERROR
    assert failure.error_owner == ErrorOwner.OPERATOR
    assert failure.code == "dograh-unknown"


def test_dograh_transport_failure_stays_system_error_but_keeps_retry_hint():
    failure = classify_exception(
        httpx.ConnectError("MPS connection refused"),
        source=ErrorSource.PLATFORM,
        provider="dograh",
    )

    assert failure.type == ErrorType.SYSTEM_ERROR
    assert failure.error_owner == ErrorOwner.OPERATOR
    assert failure.code == "dograh-connection"
    assert failure.retryable is True


def test_redaction_removes_url_query_userinfo_and_auth_secrets():
    message = (
        "wss://alice:password@pbx.example/ari/events?api_key=user:pass&token=abc "
        "Authorization: Bearer header-token "
        "{'X-Auth-Token': 'provider-secret', \"private_key\": \"pem-secret\"}"
    )

    redacted = redact_failure_message(message)

    assert "password" not in redacted
    assert "user:pass" not in redacted
    assert "header-token" not in redacted
    assert "provider-secret" not in redacted
    assert "pem-secret" not in redacted
    assert redacted.count("[REDACTED]") >= 5


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            r"payload={'client_secret': 'alpha\'omega', 'safe': 'visible'}",
            "payload={'client_secret': '[REDACTED]', 'safe': 'visible'}",
        ),
        (
            r'payload={"api_key": "alpha\"omega", "safe": "visible"}',
            'payload={"api_key": "[REDACTED]", "safe": "visible"}',
        ),
        (
            'payload={"private_key": "line-one\nline-two", "safe": "visible"}',
            'payload={"private_key": "[REDACTED]", "safe": "visible"}',
        ),
    ],
)
def test_redaction_consumes_entire_escaped_or_multiline_quoted_secret(
    message, expected
):
    assert redact_failure_message(message) == expected


def test_factory_metadata_is_authoritative_over_wrapper_class_name():
    service = type("DograhOpenAIRealtimeLLMService", (), {})()
    annotate_failure_metadata(
        service,
        source=ErrorSource.LLM,
        provider="openai_realtime",
        error_owner=ErrorOwner.USER,
    )

    metadata = failure_metadata_for_processor(service)

    assert metadata.source == ErrorSource.LLM
    assert metadata.provider == "openai-realtime"
    assert metadata.error_owner == ErrorOwner.USER


def test_processor_class_name_is_only_a_fallback_hint():
    processor = type("DeepgramSTTService", (), {})()

    metadata = failure_metadata_for_processor(processor)

    assert metadata.source == ErrorSource.STT
    assert metadata.provider == "deepgram"
    assert metadata.error_owner == ErrorOwner.OPERATOR


def test_processor_provider_fallback_comes_from_live_registry(monkeypatch):
    monkeypatch.setitem(REGISTRY[ServiceType.STT], "acme_voice", object())
    processor = type("AcmeVoiceSTTService", (), {})()

    metadata = failure_metadata_for_processor(processor)

    assert metadata.source == ErrorSource.STT
    assert metadata.provider == "acme-voice"


def test_processor_provider_fallback_prefers_most_specific_registration():
    processor = type("OpenAIRealtimeLLMService", (), {})()

    metadata = failure_metadata_for_processor(processor)

    assert metadata.source == ErrorSource.LLM
    assert metadata.provider == "openai-realtime"


def test_log_failure_emits_structured_and_inline_classification():
    records = []
    sink_id = logger.add(lambda message: records.append(message.record))
    try:
        log_failure(
            DograhFailure(
                source=ErrorSource.WEBHOOK,
                type=ErrorType.CONFIG_ERROR,
                code="webhook-404",
                internal_message="Endpoint returned 404",
                external_message="Check the endpoint.",
                provider="webhook",
                provider_error_code="endpoint_missing",
            ),
            organization_id=7,
            workflow_run_id=88,
        )
    finally:
        logger.remove(sink_id)

    record = records[-1]
    assert "DOGRAH_FAILURE [src=webhook type=config_error code=webhook-404]" in str(
        record["message"]
    )
    assert record["extra"]["classified"] is True
    assert record["extra"]["classification_mode"] == "explicit"
    assert record["extra"]["error_owner"] == "user"
    assert "credential_owner" not in record["extra"]
    assert record["extra"]["provider_error_code"] == "endpoint-missing"
    assert record["extra"]["organization_id"] == 7
    assert record["extra"]["workflow_run_id"] == 88
    assert record["file"].name == "test_failure_classification.py"
    assert "[owner=user]" in str(record["message"])


def test_log_failure_does_not_add_a_workflow_run_id_alias():
    records = []
    sink_id = logger.add(lambda message: records.append(message.record))
    try:
        log_failure(
            DograhFailure(
                source=ErrorSource.PLATFORM,
                type=ErrorType.SYSTEM_ERROR,
                code="platform-test",
                internal_message="Test failure",
                external_message="Test failure",
            ),
            fatal=True,
        )
    finally:
        logger.remove(sink_id)

    record = records[-1]
    assert record["extra"]["fatal"] is True
    assert "workflow_run_id" not in record["extra"]


def test_classified_exception_keeps_safe_debugging_context():
    try:
        raise RuntimeError("token=provider-secret")
    except RuntimeError as exc:
        failure = classify_exception(
            exc,
            source=ErrorSource.INTEGRATION,
            provider="crm",
        )

    assert failure.context["exception_type"] == "RuntimeError"
    assert (
        "test_classified_exception_keeps_safe_debugging_context"
        in failure.context["exception_traceback"]
    )
    assert "provider-secret" not in failure.internal_message
    assert "provider-secret" not in failure.context["exception_traceback"]


def test_log_failure_never_raises_when_logger_fails(monkeypatch):
    class _BrokenLogger:
        def bind(self, **_kwargs):
            raise RuntimeError("logger unavailable")

    monkeypatch.setattr(failure_module, "logger", _BrokenLogger())

    log_failure(
        DograhFailure(
            source=ErrorSource.PLATFORM,
            type=ErrorType.SYSTEM_ERROR,
            code="platform-test",
            internal_message="test",
            external_message="test",
        )
    )


def test_status_extraction_uses_response_without_mistaking_vendor_code():
    exc = _StatusException(
        "request failed", code=20003, response=SimpleNamespace(status_code=503)
    )

    failure = classify_exception(exc, source=ErrorSource.INTEGRATION, provider="crm")

    assert failure.type == ErrorType.PROVIDER_ERROR
    assert failure.code == "crm-503"
    assert failure.provider_error_code == "20003"


@pytest.mark.parametrize(
    ("message", "expected_type", "expected_code"),
    [
        (
            "server rejected WebSocket connection: HTTP 401",
            ErrorType.CONFIG_ERROR,
            "ari-401",
        ),
        (
            "server rejected WebSocket connection: HTTP 530",
            ErrorType.PROVIDER_ERROR,
            "ari-530",
        ),
    ],
)
def test_http_status_embedded_in_exception_text_is_classified(
    message, expected_type, expected_code
):
    failure = classify_exception(
        RuntimeError(message), source=ErrorSource.TELEPHONY, provider="ari"
    )

    assert failure.type == expected_type
    assert failure.code == expected_code
