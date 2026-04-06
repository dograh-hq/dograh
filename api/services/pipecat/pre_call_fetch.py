"""Pre-call HTTP data fetch for StartCall node.

Executes an HTTP request before a voice call starts to enrich the
call context with data from external systems (CRM, ERP, etc.).
"""

from typing import Any, Dict, Optional

import httpx
from loguru import logger

from api.db import db_client
from api.utils.credential_auth import build_auth_header

PRE_CALL_FETCH_TIMEOUT_SECONDS = 10


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

    if credential_uuid:
        try:
            credential = await db_client.get_credential_by_uuid(
                credential_uuid, organization_id
            )
            if credential:
                headers.update(build_auth_header(credential))
            else:
                logger.warning(
                    f"Pre-call fetch: credential {credential_uuid} not found"
                )
        except Exception as e:
            logger.error(f"Pre-call fetch: failed to resolve credential: {e}")

    logger.info(f"Pre-call fetch: POST {url}")

    try:
        async with httpx.AsyncClient(timeout=PRE_CALL_FETCH_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json=payload)

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

                # Extract dynamic_variables from Retell-compatible response
                # Supports: {call_inbound: {dynamic_variables: {...}}}
                #       or: {dynamic_variables: {...}}
                dynamic_vars = {}
                call_inbound = response_data.get("call_inbound")
                if isinstance(call_inbound, dict):
                    dynamic_vars = call_inbound.get("dynamic_variables", {})
                elif "dynamic_variables" in response_data:
                    dynamic_vars = response_data["dynamic_variables"]

                if not isinstance(dynamic_vars, dict):
                    dynamic_vars = {}

                logger.info(
                    f"Pre-call fetch: success ({response.status_code}), "
                    f"dynamic_variables keys: {list(dynamic_vars.keys())}"
                )
                return dynamic_vars
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
