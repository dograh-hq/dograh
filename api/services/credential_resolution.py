"""Resolve stored webhook credentials for outbound HTTP calls.

Webhook delivery, custom tools, transfer resolvers, pre-call fetch, and MCP
sessions all fetch the same organization-scoped credential row and then apply
their own policy for a missing credential or a failed lookup. The fetch itself
and the structured-failure construction are centralized here so every caller
agrees on when a credential counts as missing versus errored; each caller keeps
its own not-found and error policy.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from api.db import db_client
from api.errors.failure import DograhFailure, ErrorSource, ErrorType, classify_exception

if TYPE_CHECKING:
    from api.db.models import ExternalCredentialModel


@dataclass(frozen=True)
class CredentialResolution:
    """Outcome of an organization-scoped credential lookup.

    ``credential`` is the row when found, otherwise None. ``failure`` carries a
    ready-to-log DograhFailure for callers with structured-failure plumbing; it
    is set when the credential is missing or the fetch raised. ``fetch_error``
    preserves the original exception for callers that degrade differently.
    """

    credential: Optional["ExternalCredentialModel"] = None
    failure: Optional[DograhFailure] = None
    fetch_error: Optional[BaseException] = None


async def resolve_credential(
    credential_uuid: Optional[str],
    organization_id: Optional[int],
    *,
    source: Optional[ErrorSource] = None,
    provider: str = "",
    missing_code: str = "",
    missing_internal_message: str = "",
    missing_external_message: str = "",
    error_owner: str = "user",
    propagate_errors: bool = False,
) -> CredentialResolution:
    """Fetch a credential by UUID, scoped to an organization.

    Args:
        credential_uuid: The credential UUID configured on the calling feature.
        organization_id: The caller's organization. Credentials belong to an
            organization, so an empty value short-circuits to "not configured"
            the same way an empty UUID does.
        source: ErrorSource for the structured not-found/classified failure.
            Callers without failure plumbing omit it and read ``credential`` /
            ``fetch_error`` directly.
        provider: Provider tag on the structured failure.
        missing_code: Failure code used when the credential does not exist.
        missing_internal_message: Internal message for the missing case.
        missing_external_message: User-facing message for the missing case.
        error_owner: Owner tag on the structured failure.
        propagate_errors: When True, lookup exceptions propagate unchanged for
            callers whose surrounding error handling expects them; when False
            (default) they are classified into ``failure`` and returned.

    Returns:
        CredentialResolution with the credential and/or the failure and raw
        fetch error, per the caller's configuration.
    """
    if not credential_uuid or not organization_id:
        return CredentialResolution()

    try:
        credential = await db_client.get_credential_by_uuid(
            credential_uuid, organization_id
        )
    except Exception as e:
        if propagate_errors:
            raise
        failure = (
            classify_exception(
                e, source=source, provider=provider, error_owner=error_owner
            )
            if source is not None
            else None
        )
        return CredentialResolution(failure=failure, fetch_error=e)

    if credential is None and source is not None:
        return CredentialResolution(
            failure=DograhFailure(
                source=source,
                type=ErrorType.CONFIG_ERROR,
                code=missing_code,
                internal_message=missing_internal_message,
                external_message=missing_external_message,
                provider=provider,
                error_owner=error_owner,
                retryable=False,
            )
        )
    return CredentialResolution(credential=credential)
