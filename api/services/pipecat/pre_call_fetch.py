"""Pre-call HTTP data fetch for StartCall node.

Executes an HTTP request before a voice call starts to enrich the
call context with data from external systems (CRM, ERP, etc.).
"""

from typing import Any, Dict, Optional

import asyncio
import httpx
from loguru import logger

from api.db import db_client
from api.services.workflow.initial_context import merge_external_initial_context
from api.utils.credential_auth import build_auth_header

PRE_CALL_FETCH_TIMEOUT_SECONDS = 10


def _extract_initial_context(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the context variables out of a pre-call fetch response.

    The canonical key is ``initial_context``. The legacy ``dynamic_variables``
    key is still accepted for backward compatibility, so existing endpoints
    keep working; ``initial_context`` takes precedence when both are present.

    Either key may appear at the top level or nested under ``call_inbound``:
        {"call_inbound": {"initial_context": {...}}} | {"initial_context": {...}}
        {"call_inbound": {"dynamic_variables": {...}}} | {"dynamic_variables": {...}}
    """
    container = response_data.get("call_inbound")
    if not isinstance(container, dict):
        container = response_data

    for key in ("initial_context", "dynamic_variables"):
        value = container.get(key)
        if isinstance(value, dict):
            return merge_external_initial_context({}, value)

    return {}


async def execute_pre_call_fetch(
    *,
    url: str,
    credential_uuid: Optional[str],
    call_context_vars: Dict[str, Any],
    workflow_id: int,
    organization_id: int,
) -> Dict[str, Any]:
    """Execute a POST request to fetch data before a call starts.

    Sends a standardized payload with call metadata (agent_id, from/to numbers).
    The response JSON is returned as a dict to be merged into initial_context.

    Returns:
        Response JSON dict on success, empty dict on any failure.
        Never raises.
    """
    # Build standardized payload
    payload = {
        "event": "call_inbound",
        "call_inbound": {
            "agent_id": workflow_id,
            "from_number": call_context_vars.get("caller_number", ""),
            "to_number": call_context_vars.get("called_number", ""),
        },
    }

    # Build headers
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    credential = None

    if credential_uuid:
        try:
            credential = await db_client.get_credential_by_uuid(
                credential_uuid, organization_id
            )
            if credential:
                auth = await build_auth_header(credential)
                if auth:
                    headers.update(auth)
            else:
                logger.warning(
                    f"Pre-call fetch: credential {credential_uuid} not found"
                )
        except Exception as e:
            logger.error(f"Pre-call fetch: failed to resolve credential: {e}")
            return {}

    logger.info(f"Pre-call fetch: POST {url}")

    try:
        async with asyncio.timeout(PRE_CALL_FETCH_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=PRE_CALL_FETCH_TIMEOUT_SECONDS) as client:
                response = await client.post(url, headers=headers, json=payload)
            
                # 401 token invalidation and single retry
                if (
                    response.status_code == 401
                    and credential_uuid
                    and credential is not None
                    and getattr(credential, "credential_type", None) == "oauth2_client_credentials"
                ):
                    from api.utils.credential_auth import rebuild_headers_after_401
                    logger.info(f"Invalidated OAuth2 token for credential {credential.credential_uuid} after 401 response in pre-call fetch. Retrying once...")
                    
                    try:
                        await rebuild_headers_after_401(credential, headers)
                        response = await client.post(url, headers=headers, json=payload)
                    except ValueError as reauth_exc:
                        logger.error(
                            f"Pre-call fetch: failed to refresh OAuth2 token: {reauth_exc}"
                        )
                        return {}

                try:
                    response_data = response.json()
                except Exception:
                    response_data = {}

            if response.is_success:
                if not isinstance(response_data, dict):
                    logger.warning(
                        "Pre-call fetch: response is not a JSON object, skipping"
                    )
                    return {}

                # Extract the variables to merge into initial_context. Prefers
                # the canonical `initial_context` key, falling back to the
                # legacy `dynamic_variables` key for backward compatibility.
                initial_context_vars = _extract_initial_context(response_data)

                logger.info(
                    f"Pre-call fetch: success ({response.status_code}), "
                    f"initial_context keys: {list(initial_context_vars.keys())}"
                )
                return initial_context_vars
            else:
                logger.warning(
                    f"Pre-call fetch: HTTP {response.status_code} - "
                    f"{response.text[:200]}"
                )
                return {}

    except httpx.TimeoutException:
        logger.error(
            f"Pre-call fetch: timed out after {PRE_CALL_FETCH_TIMEOUT_SECONDS}s"
        )
        return {}
    except httpx.RequestError as e:
        logger.error(f"Pre-call fetch: request failed: {e}")
        return {}
    except Exception as e:
        logger.error(f"Pre-call fetch: unexpected error: {e}")
        return {}
