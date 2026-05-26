"""Helpers for workflow run artifact access."""

from api.constants import BACKEND_API_ENDPOINT


def artifact_url(
    token: str | None, artifact: str, fallback: str | None = None
) -> str | None:
    if not token:
        return fallback
    return f"{BACKEND_API_ENDPOINT}/api/v1/public/download/workflow/{token}/{artifact}"
