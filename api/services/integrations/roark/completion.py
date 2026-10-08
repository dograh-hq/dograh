from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from loguru import logger

from api.constants import BACKEND_API_ENDPOINT, ROARK_BASE_URL
from api.services.integrations.base import IntegrationCompletionContext
from api.utils.common import get_backend_endpoints, is_local_or_private_url

from .client import RoarkDeliveryConfig, RoarkDeliveryError, create_call
from .node import RoarkNodeData
from .payload import TEXT_MODES, build_call_payload

# Dograh stores the mixed recording as a WAV but serves it from an extensionless
# path. Roark decides a URL is audio from its shape before it fetches anything,
# so the filename it would download is spelled out in the query string. The
# download route ignores unknown query parameters.
_RECORDING_FILENAME = "recording.wav"


async def resolve_public_base_url() -> str:
    """The base URL Roark should fetch the recording from.

    Roark downloads the audio itself, so unlike the other post-call exports this
    URL has to be reachable from the internet. `get_backend_endpoints()` is the
    same resolver telephony webhooks use: it prefers a publicly reachable
    `BACKEND_API_ENDPOINT` and otherwise falls back to the running cloudflared
    tunnel, which is what makes a laptop or a private-network deployment work.
    Its own failure mode is to raise, so a deployment with neither still gets
    the configured address rather than a crash.

    Neither path guarantees an address Roark can safely fetch from: with no
    tunnel running, the resolver returns the configured private one and so does
    this fallback, and nothing here requires it to be HTTPS.
    `unreachable_recording_host` and `insecure_recording_scheme` are what
    decide whether the result is exportable.
    """
    try:
        backend_endpoint, _ws = await get_backend_endpoints()
        return backend_endpoint.rstrip("/")
    except Exception as exc:
        logger.warning(
            f"Roark could not resolve a public backend endpoint ({exc}), "
            f"falling back to BACKEND_API_ENDPOINT"
        )
        return (BACKEND_API_ENDPOINT or "").rstrip("/")


async def build_recording_url(context: IntegrationCompletionContext) -> str | None:
    """A URL Roark can fetch the recording from, or None if there is none.

    The public-token route is used rather than a signed storage URL because
    Roark downloads the audio asynchronously, sometimes long after ingest (a
    job can wait on the organization's credit). The token route re-signs on
    every request, so it does not go stale; a signed URL would.
    """
    workflow_run = context.workflow_run
    if not getattr(workflow_run, "recording_url", None):
        return None
    if not context.public_token:
        return None
    base_url = await resolve_public_base_url()
    return (
        f"{base_url}/api/v1/public/download/workflow/"
        f"{context.public_token}/recording?filename={_RECORDING_FILENAME}"
    )


def unreachable_recording_host(recording_url: str) -> str | None:
    """The host Roark would not be able to fetch the recording from.

    None when the URL is publicly reachable. Roark downloads the audio itself,
    asynchronously, so a private address does not fail the POST: Roark accepts
    the call and the fetch fails later, out of reach of anything this
    integration observes. That would leave a run recorded as `delivered` and a
    call in Roark that can never be transcribed, which is the one outcome worth
    refusing up front. A self-hosted deployment reaches this by running without
    a public `BACKEND_API_ENDPOINT` and without a cloudflared tunnel.
    """
    if not is_local_or_private_url(recording_url):
        return None
    return urlsplit(recording_url).netloc or recording_url


def insecure_recording_scheme(recording_url: str) -> str | None:
    """The scheme that would expose the recording in transit, or None for HTTPS.

    The URL carries the run's public access token in its path, and that token
    is the whole of what stands between the internet and the run's recording
    and transcript. It does not expire by design: the download route re-signs
    storage access on every request, which is why it can be handed to a
    service that fetches hours later. Over plain HTTP, Roark's fetch puts that
    token and the audio on the wire in the clear, and whoever saw it can
    replay it for as long as the run exists.

    Refused rather than exported anyway, for the same reason a private host is:
    the alternative is a disclosure the deployment did not choose and cannot
    take back, in exchange for an export it can have by serving HTTPS.
    """
    scheme = urlsplit(recording_url).scheme.lower()
    if scheme == "https":
        return None
    # A `BACKEND_API_ENDPOINT` configured without a scheme resolves to a URL
    # Roark could not fetch at all. Named rather than reported as an empty
    # string, so the run's annotation still says what was wrong with it.
    return scheme or "(none)"


def describe_validation_failure(exc: Exception) -> str:
    """Why a node failed validation, without quoting what it contained.

    Stringifying a pydantic error embeds the offending input, and for a
    model-level validator that input is the whole node, including
    ``roark_api_key``. Only the field and the message are reported, so a
    misconfigured node cannot put a customer's key in the logs.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return type(exc).__name__
    try:
        reported = errors()
    except Exception:
        return type(exc).__name__

    parts = []
    for error in reported:
        field = ".".join(str(part) for part in error.get("loc") or ()) or "node"
        parts.append(f"{field}: {error.get('msg', 'invalid')}")
    return "; ".join(parts) or type(exc).__name__


async def run_completion(
    nodes: list[dict[str, Any]],
    context: IntegrationCompletionContext,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    recording_url = await build_recording_url(context)
    mode = getattr(context.workflow_run, "mode", None)

    for node in nodes:
        node_id = node.get("id", "unknown")
        result_key = f"roark_{node_id}"

        try:
            roark_data = RoarkNodeData.model_validate(node.get("data", {}))
        except Exception as exc:
            detail = describe_validation_failure(exc)
            logger.warning(
                "Roark node #{} failed validation, skipping: {}",
                node_id,
                detail,
            )
            results[result_key] = {
                "error": "validation_failed",
                # The same safe description the log carries: field names and
                # pydantic's own messages, never the value that failed. A node
                # is otherwise only diagnosable by someone with log access.
                "detail": detail,
            }
            continue

        if not roark_data.roark_enabled:
            logger.debug(f"Roark node '{roark_data.name}' is disabled, skipping")
            continue

        # A text chat has no audio, and Roark models it as a chat rather than a
        # call. Exporting one as a call would be wrong, not merely incomplete.
        if mode in TEXT_MODES:
            logger.info(
                f"Roark node '{roark_data.name}' skipped: run mode '{mode}' has no recording"
            )
            results[result_key] = {"error": "unsupported_run_mode", "mode": mode}
            continue

        if not recording_url:
            # Info, not a warning: a call that never connected (no answer,
            # busy) reaches here on every attempt and there is nothing wrong
            # with the configuration to act on.
            logger.info(
                f"Roark node '{roark_data.name}' (#{node_id}) has no recording to export"
            )
            results[result_key] = {"error": "missing_recording"}
            continue

        unreachable_host = unreachable_recording_host(recording_url)
        if unreachable_host:
            logger.warning(
                f"Roark node '{roark_data.name}' (#{node_id}) skipped: the recording "
                f"would be served from '{unreachable_host}', which Roark cannot reach. "
                f"Set BACKEND_API_ENDPOINT to a public address or run a cloudflared tunnel."
            )
            results[result_key] = {
                "error": "recording_url_not_public",
                "recording_host": unreachable_host,
            }
            continue

        insecure_scheme = insecure_recording_scheme(recording_url)
        if insecure_scheme:
            logger.warning(
                f"Roark node '{roark_data.name}' (#{node_id}) skipped: the recording "
                f"would be served over '{insecure_scheme}', which puts the run's "
                f"public access token and its audio on the wire in the clear. Serve "
                f"BACKEND_API_ENDPOINT over HTTPS, or run a cloudflared tunnel."
            )
            results[result_key] = {
                "error": "recording_url_not_https",
                "recording_scheme": insecure_scheme,
            }
            continue

        payload = build_call_payload(
            workflow_run=context.workflow_run,
            definition_id=context.definition_id,
            recording_url=recording_url,
            # Present whenever `recording_url` is: both come from it.
            public_token=context.public_token or "",
            agent_id=roark_data.roark_agent_id,
            agent_name=roark_data.roark_agent_name,
            send_transcript=roark_data.roark_send_transcript,
            send_gathered_context=roark_data.roark_send_gathered_context,
        )

        try:
            config = RoarkDeliveryConfig(
                base_url=ROARK_BASE_URL,
                api_key=roark_data.roark_api_key or "",
            )
            delivery = await create_call(config, payload)
            results[result_key] = {
                **delivery,
                "external_id": payload["externalId"],
                "exported_at": datetime.now(UTC).isoformat(),
            }
        except RoarkDeliveryError as exc:
            logger.error(f"Roark export failed for node '{roark_data.name}': {exc}")
            results[result_key] = {
                "error": exc.detail,
                "status_code": exc.status_code,
            }
        except Exception as exc:
            # `str(exc)` is empty on an httpx timeout, which would otherwise
            # annotate the run with `{"error": ""}` and say nothing at all.
            detail = str(exc) or type(exc).__name__
            logger.error(f"Roark export failed for node '{roark_data.name}': {detail}")
            results[result_key] = {"error": detail}

    return results
