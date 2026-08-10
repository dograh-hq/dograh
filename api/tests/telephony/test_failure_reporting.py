from types import SimpleNamespace

import pytest

from api.errors.failure import DograhFailure, ErrorSource, ErrorType
from api.errors.telephony_errors import (
    TelephonyError,
    failure_from_telephony_error,
)
from api.services.telephony import ari_manager
from api.services.telephony.failure_reporting import instrument_telephony_provider


def test_ari_failure_reporting_preserves_repeated_occurrences(monkeypatch):
    captured = []
    failure = DograhFailure(
        source=ErrorSource.TELEPHONY,
        type=ErrorType.CONFIG_ERROR,
        code="ari-401",
        internal_message="ARI rejected the configured credentials",
        external_message="Check the ARI credentials.",
        provider="ari",
        error_owner="user",
        retryable=False,
    )
    monkeypatch.setattr(
        ari_manager,
        "log_failure",
        lambda reported, **context: captured.append((reported, context)),
    )

    ari_manager._log_ari_failure(
        failure,
        organization_id=42,
        telephony_configuration_id=7,
    )
    ari_manager._log_ari_failure(
        failure,
        organization_id=42,
        telephony_configuration_id=7,
    )

    assert captured == [
        (
            failure,
            {"organization_id": 42, "telephony_configuration_id": 7},
        ),
        (
            failure,
            {"organization_id": 42, "telephony_configuration_id": 7},
        ),
    ]


@pytest.mark.asyncio
async def test_outbound_instrumentation_classifies_and_reraises(monkeypatch):
    captured = []

    class ProviderError(Exception):
        status_code = 401

    error = ProviderError("Twilio rejected credentials with code 20003")

    async def initiate_call(*_args, **_kwargs):
        raise error

    provider = SimpleNamespace(
        PROVIDER_NAME="twilio",
        initiate_call=initiate_call,
    )
    monkeypatch.setattr(
        "api.services.telephony.failure_reporting.log_failure",
        lambda failure, **context: captured.append((failure, context)),
    )
    instrument_telephony_provider(provider)

    with pytest.raises(ProviderError) as raised:
        await provider.initiate_call(
            "+14155550123",
            "https://dograh.test/webhook",
            workflow_run_id=88,
            organization_id=42,
        )

    assert raised.value is error
    failure, context = captured[0]
    assert failure.type == ErrorType.CONFIG_ERROR
    assert failure.code == "twilio-401"
    assert failure.provider_error_code is None
    assert failure.error_owner.value == "user"
    assert context["organization_id"] == 42
    assert context["workflow_run_id"] == 88


@pytest.mark.asyncio
async def test_outbound_instrumentation_preserves_success_result():
    result = object()

    async def initiate_call(*_args, **_kwargs):
        return result

    provider = SimpleNamespace(
        PROVIDER_NAME="telnyx",
        initiate_call=initiate_call,
    )

    instrument_telephony_provider(provider)

    assert await provider.initiate_call("to", "webhook") is result


def test_inbound_validation_mapping_separates_config_and_quota():
    config = failure_from_telephony_error(
        TelephonyError.ACCOUNT_VALIDATION_FAILED,
        provider="plivo",
    )
    quota = failure_from_telephony_error(
        TelephonyError.CONCURRENT_CALL_LIMIT,
        provider="plivo",
    )

    assert config is not None
    assert config.type == ErrorType.CONFIG_ERROR
    assert config.code == "plivo-account-validation-failed"
    assert quota is not None
    assert quota.type == ErrorType.QUOTA_ERROR
    assert quota.code == "plivo-concurrent-call-limit"


def test_signature_failure_is_not_customer_notification_eligible():
    failure = failure_from_telephony_error(
        TelephonyError.SIGNATURE_VALIDATION_FAILED,
        provider="twilio",
    )

    assert failure is not None
    assert failure.context["notification_eligible"] is False
    assert failure.context["tenant_attribution_trusted"] is False


def test_unknown_inbound_validation_defaults_to_system_error():
    failure = failure_from_telephony_error("NOT_A_REAL_ERROR", provider="twilio")

    assert failure is not None
    assert failure.type == ErrorType.SYSTEM_ERROR
    assert failure.code == "twilio-unknown-validation-error"
    assert failure.context["notification_eligible"] is False


@pytest.mark.asyncio
async def test_explicit_provider_configuration_failure_is_config_error(monkeypatch):
    captured = []

    async def initiate_call(*_args, **_kwargs):
        raise ValueError("Twilio provider not properly configured")

    provider = SimpleNamespace(
        PROVIDER_NAME="twilio",
        initiate_call=initiate_call,
    )
    monkeypatch.setattr(
        "api.services.telephony.failure_reporting.log_failure",
        lambda failure, **_context: captured.append(failure),
    )
    instrument_telephony_provider(provider)

    with pytest.raises(ValueError):
        await provider.initiate_call("to", "webhook")

    assert captured[0].type == ErrorType.CONFIG_ERROR
    assert captured[0].code == "twilio-invalid-config"
