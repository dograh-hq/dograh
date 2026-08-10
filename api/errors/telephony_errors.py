"""
Telephony error constants and messages for inbound call validation.
Centralizes error handling across all telephony providers.
"""

from enum import Enum

from api.errors.failure import DograhFailure, ErrorSource, ErrorType, log_failure


class TelephonyError(Enum):
    """Telephony validation error types"""

    PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    ACCOUNT_VALIDATION_FAILED = "ACCOUNT_VALIDATION_FAILED"
    PHONE_NUMBER_NOT_CONFIGURED = "PHONE_NUMBER_NOT_CONFIGURED"
    SIGNATURE_VALIDATION_FAILED = "SIGNATURE_VALIDATION_FAILED"
    CONCURRENT_CALL_LIMIT = "CONCURRENT_CALL_LIMIT"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    GENERAL_AUTH_FAILED = "GENERAL_AUTH_FAILED"
    VALID = "VALID"


# Error messages for organizations (debugging-focused)
TELEPHONY_ERROR_MESSAGES = {
    TelephonyError.PROVIDER_MISMATCH: "Configuration error: This phone number is configured for a different telephony provider. Please check your dashboard settings and update your webhook URL configuration.",
    TelephonyError.WORKFLOW_NOT_FOUND: "Workflow not found. Please verify the workflow ID in your webhook URL is correct and the workflow exists in your dashboard.",
    TelephonyError.ACCOUNT_VALIDATION_FAILED: "Authentication error: Account credentials do not match. Please verify your account SID configuration in the dashboard matches your telephony provider settings.",
    TelephonyError.PHONE_NUMBER_NOT_CONFIGURED: "Phone number not configured: This number is not set up for inbound calls in your account. Please add this number to your telephony configuration.",
    TelephonyError.SIGNATURE_VALIDATION_FAILED: "Security error: Webhook signature validation failed. Please verify your auth token configuration and ensure requests are coming from your telephony provider.",
    TelephonyError.CONCURRENT_CALL_LIMIT: "Service temporarily unavailable: Your account has reached its concurrent call limit. Please try again later.",
    TelephonyError.QUOTA_EXCEEDED: "Service temporarily unavailable: Your account has exceeded usage limits. Please contact your administrator or upgrade your plan to continue receiving calls.",
    TelephonyError.GENERAL_AUTH_FAILED: "Authentication failed: Please check your webhook URL configuration and ensure your telephony provider settings match your dashboard configuration.",
}


_QUOTA_ERRORS = {
    TelephonyError.CONCURRENT_CALL_LIMIT,
    TelephonyError.QUOTA_EXCEEDED,
}


def failure_from_telephony_error(
    error: TelephonyError,
    *,
    provider: str | None = None,
) -> DograhFailure | None:
    """Map an inbound validation result onto the stable failure taxonomy."""

    if not isinstance(error, TelephonyError):
        try:
            error = TelephonyError(error)
        except (TypeError, ValueError):
            provider_code = (provider or "telephony").replace("_", "-")
            return DograhFailure(
                source=ErrorSource.TELEPHONY,
                type=ErrorType.SYSTEM_ERROR,
                code=f"{provider_code}-unknown-validation-error",
                internal_message=f"Unknown inbound telephony validation result: {error}",
                external_message="Dograh could not process the inbound telephony validation result.",
                provider=provider,
                retryable=None,
                context={
                    "notification_eligible": False,
                    "tenant_attribution_trusted": False,
                },
            )
    if error == TelephonyError.VALID:
        return None

    error_type = (
        ErrorType.QUOTA_ERROR if error in _QUOTA_ERRORS else ErrorType.CONFIG_ERROR
    )
    provider_code = (provider or "telephony").replace("_", "-")
    external_message = TELEPHONY_ERROR_MESSAGES.get(
        error, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
    )
    return DograhFailure(
        source=ErrorSource.TELEPHONY,
        type=error_type,
        code=f"{provider_code}-{error.value.lower().replace('_', '-')}",
        internal_message=f"Inbound telephony validation failed: {error.value}",
        external_message=external_message,
        provider=provider,
        error_owner="user",
        retryable=False,
        context={
            # A failed signature can be sent by an attacker and must not become a
            # tenant notification until attribution has been established.
            "notification_eligible": error
            != TelephonyError.SIGNATURE_VALIDATION_FAILED,
            "tenant_attribution_trusted": error
            != TelephonyError.SIGNATURE_VALIDATION_FAILED,
        },
    )


def log_telephony_error(
    error: TelephonyError,
    *,
    provider: str | None = None,
    **context,
) -> None:
    failure = failure_from_telephony_error(error, provider=provider)
    if failure is not None:
        log_failure(failure, **context)
