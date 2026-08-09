"""Shared shadow-mode failure reporting for telephony provider operations."""

from functools import wraps
from typing import TYPE_CHECKING, Any

from api.errors.failure import (
    DograhFailure,
    ErrorSource,
    ErrorType,
    classify_exception,
    log_failure,
)

if TYPE_CHECKING:
    from api.services.telephony.base import TelephonyProvider


def classify_telephony_exception(
    exc: BaseException, *, provider: object | None
) -> DograhFailure:
    """Apply config-aware rules available only at the telephony boundary."""

    try:
        raw_message = str(exc)
    except Exception:
        raw_message = type(exc).__name__
    message = raw_message.lower()
    if isinstance(exc, ValueError) and any(
        marker in message
        for marker in (
            "not properly configured",
            "missing required",
            "configuration is required",
        )
    ):
        provider_value = getattr(provider, "value", provider) or "telephony"
        return DograhFailure(
            source=ErrorSource.TELEPHONY,
            type=ErrorType.CONFIG_ERROR,
            code=f"{provider_value}-invalid-config",
            internal_message=raw_message,
            external_message="Check the telephony provider configuration and credentials.",
            provider=str(provider_value),
            error_owner="user",
            retryable=False,
        )
    return classify_exception(
        exc,
        source=ErrorSource.TELEPHONY,
        provider=provider,
        error_owner="user",
    )


def instrument_telephony_provider(provider: "TelephonyProvider") -> "TelephonyProvider":
    """Classify outbound initiation errors without changing propagation semantics."""

    if getattr(provider, "_dograh_failure_reporting_instrumented", False):
        return provider

    original = provider.initiate_call
    provider_name = getattr(provider, "PROVIDER_NAME", None)

    @wraps(original)
    async def initiate_call_with_failure_reporting(*args: Any, **kwargs: Any):
        workflow_run_id = kwargs.get("workflow_run_id")
        if workflow_run_id is None and len(args) > 2:
            workflow_run_id = args[2]
        organization_id = kwargs.get("organization_id")
        try:
            return await original(*args, **kwargs)
        except Exception as exc:
            log_failure(
                classify_telephony_exception(exc, provider=provider_name),
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                operation="initiate call",
            )
            raise

    try:
        provider.initiate_call = initiate_call_with_failure_reporting
        provider._dograh_failure_reporting_instrumented = True
    except Exception:
        # Instrumentation must not make a valid provider unusable. All current
        # providers are mutable Python classes, but retain a safe fallback for
        # future slotted third-party implementations.
        pass
    return provider
