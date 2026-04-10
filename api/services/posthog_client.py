from loguru import logger
from posthog import Posthog

from api.constants import POSTHOG_API_KEY, POSTHOG_HOST

_posthog_client: Posthog | None = None


def get_posthog() -> Posthog | None:
    """Return the lazily-initialised PostHog client, or None if not configured."""
    global _posthog_client
    if _posthog_client is None and POSTHOG_API_KEY:
        _posthog_client = Posthog(POSTHOG_API_KEY, host=POSTHOG_HOST)
    return _posthog_client


def capture_event(
    distinct_id: str,
    event: str,
    properties: dict | None = None,
) -> None:
    """Fire a PostHog event. Silently no-ops if PostHog is not configured."""
    client = get_posthog()
    if not client:
        return
    try:
        client.capture(
            distinct_id=distinct_id, event=event, properties=properties or {}
        )
    except Exception:
        logger.exception(f"Failed to send PostHog event '{event}'")
