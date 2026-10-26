from typing import Any, Optional

from loguru import logger
from posthog import Posthog

from api.constants import POSTHOG_API_KEY, POSTHOG_HOST

_posthog_client: Posthog | None = None
POSTHOG_SERVER_GROUP_IDENTIFY_DISTINCT_ID = "server-group-identify"
# Keyed by the organization's provider id (the Stack team id on hosted
# deployments), the tenant identity every deployment and MPS share. The older
# "organization" group was keyed by the local organization id, which is only
# unique within one deployment, and is no longer sent.
POSTHOG_ACCOUNT_GROUP_TYPE = "account"


def get_posthog() -> Posthog | None:
    """Return the lazily-initialised PostHog client, or None if not configured."""
    global _posthog_client
    if _posthog_client is None and POSTHOG_API_KEY:
        _posthog_client = Posthog(POSTHOG_API_KEY, host=POSTHOG_HOST)
    return _posthog_client


def shutdown_posthog() -> None:
    """Flush queued PostHog messages before a short-lived process exits."""
    client = get_posthog()
    if not client:
        return
    try:
        client.shutdown()
    except Exception:
        logger.exception("Failed to shut down PostHog client")


def flush_posthog() -> None:
    """Flush queued PostHog messages without shutting down the client."""
    client = get_posthog()
    if not client:
        return
    try:
        client.flush()
    except Exception:
        logger.exception("Failed to flush PostHog client")


def capture_event(
    distinct_id: Optional[str],
    event: str,
    properties: dict[str, Any] | None = None,
    groups: Optional[dict[str, str]] = None,
) -> None:
    """Fire a PostHog event. Silently no-ops if PostHog is not configured.

    Without a ``distinct_id`` the SDK sends a personless event."""
    client = get_posthog()
    if not client:
        return
    try:
        kwargs: dict[str, Any] = {
            "distinct_id": distinct_id,
            "event": event,
            "properties": properties or {},
        }
        if groups:
            kwargs["groups"] = groups
        client.capture(**kwargs)
    except Exception:
        logger.exception(f"Failed to send PostHog event '{event}'")


def group_identify(
    group_type: str,
    group_key: str,
    properties: dict[str, Any],
    *,
    distinct_id: Optional[str] = None,
) -> bool:
    """Set PostHog group properties. Returns whether the update was queued;
    silently no-ops if PostHog is not configured."""
    client = get_posthog()
    if not client:
        return False
    try:
        client.group_identify(
            group_type,
            group_key,
            properties,
            distinct_id=distinct_id or POSTHOG_SERVER_GROUP_IDENTIFY_DISTINCT_ID,
        )
        return True
    except Exception:
        logger.exception("Failed to identify PostHog group")
        return False


def set_person_properties(distinct_id: str, properties: dict[str, Any]) -> None:
    """Set PostHog person properties. Silently no-ops if PostHog is not configured."""
    client = get_posthog()
    if not client:
        return
    try:
        client.set(distinct_id=distinct_id, properties=properties)
    except Exception:
        logger.exception("Failed to set PostHog person properties")
