"""Authentication and authorization helpers for public embed sessions."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from loguru import logger

from api.db import db_client
from api.db.models import EmbedSessionModel, EmbedTokenModel
from api.services.quota_service import QuotaCheckResult, authorize_workflow_run_start


class EmbedSessionValidationError(Exception):
    """Raised when a public embed session cannot authenticate the request."""


class EmbedSessionNotFoundError(EmbedSessionValidationError):
    """Raised when a public embed session token does not exist."""


class EmbedSessionExpiredError(EmbedSessionValidationError):
    """Raised when a public embed session is expired."""


class EmbedTokenNotFoundError(EmbedSessionValidationError):
    """Raised when the token backing a public embed session does not exist."""


class EmbedTokenInactiveError(EmbedSessionValidationError):
    """Raised when the token backing a public embed session is inactive."""


class EmbedOriginNotAllowedError(EmbedSessionValidationError):
    """Raised when the request origin is outside the token allowlist."""


def validate_embed_origin(origin: str, allowed_domains: list) -> bool:
    """Return whether an embed request origin matches the token allowlist."""
    if not allowed_domains:
        return True

    domain, origin_port = _parse_origin_host_port(origin)
    if not domain:
        return False

    def normalize_www(value: str) -> tuple[str, str]:
        if value.startswith("www."):
            return value, value[4:]
        return value, f"www.{value}"

    domain_variants = normalize_www(domain)

    for allowed in allowed_domains:
        allowed = str(allowed).strip().lower()
        if allowed == "*":
            return True
        allowed_domain, allowed_port = _parse_origin_host_port(allowed)
        if not allowed_domain:
            continue
        if allowed_port is not None and allowed_port != origin_port:
            continue

        if allowed_domain.startswith("*."):
            base_domain = allowed_domain[2:]
            if domain == base_domain or domain.endswith("." + base_domain):
                return True
        else:
            allowed_variants = normalize_www(allowed_domain)
            if any(
                domain_variant in allowed_variants or allowed_variant in domain_variants
                for domain_variant in domain_variants
                for allowed_variant in allowed_variants
            ):
                return True

    return False


def _parse_origin_host_port(value: str) -> tuple[str, str | None]:
    candidate = value.strip().lower()
    if not candidate:
        return "", None

    if "://" not in candidate and not candidate.startswith("//"):
        candidate = f"//{candidate}"

    parsed = urlsplit(candidate)
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None

    port = str(parsed_port) if parsed_port is not None else None
    return (parsed.hostname or "").rstrip("."), port


async def resolve_embed_session(
    session_token: str, origin: str
) -> tuple[EmbedSessionModel, EmbedTokenModel]:
    """Authenticate a session token and its complete embed-token chain."""
    embed_session = await db_client.get_embed_session_by_token(session_token)
    if not embed_session:
        raise EmbedSessionNotFoundError("Invalid session token")

    if embed_session.expires_at and embed_session.expires_at < datetime.now(UTC):
        raise EmbedSessionExpiredError("Session expired")

    embed_token = await db_client.get_embed_token_by_id(embed_session.embed_token_id)
    if not embed_token:
        raise EmbedTokenNotFoundError("Invalid embed token")

    if not embed_token.is_active:
        raise EmbedTokenInactiveError("Embed token is inactive")

    if not validate_embed_origin(origin, embed_token.allowed_domains or []):
        logger.warning(
            f"Domain validation failed: {origin} not in {embed_token.allowed_domains}"
        )
        raise EmbedOriginNotAllowedError(f"Domain not allowed: {origin}")

    return embed_session, embed_token


async def authorize_embed_workflow_run_start(
    *, embed_token: EmbedTokenModel, workflow_run_id: int
) -> QuotaCheckResult:
    """Authorize an embed run using the token creator as the scoped actor."""
    actor_user = await db_client.get_user_by_id(embed_token.created_by)
    return await authorize_workflow_run_start(
        workflow_id=embed_token.workflow_id,
        organization_id=embed_token.organization_id,
        workflow_run_id=workflow_run_id,
        actor_user=actor_user,
    )
