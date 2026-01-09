"""
Telephony error constants and messages for inbound call validation.
Centralizes error handling across all telephony providers.
"""

from enum import Enum


class TelephonyError(Enum):
    """Telephony validation error types"""

    PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    ACCOUNT_VALIDATION_FAILED = "ACCOUNT_VALIDATION_FAILED"
    PHONE_NUMBER_NOT_CONFIGURED = "PHONE_NUMBER_NOT_CONFIGURED"
    SIGNATURE_VALIDATION_FAILED = "SIGNATURE_VALIDATION_FAILED"
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
    TelephonyError.QUOTA_EXCEEDED: "Service temporarily unavailable: Your account has exceeded usage limits. Please contact your administrator or upgrade your plan to continue receiving calls.",
    TelephonyError.GENERAL_AUTH_FAILED: "Authentication failed: Please check your webhook URL configuration and ensure your telephony provider settings match your dashboard configuration.",
}
