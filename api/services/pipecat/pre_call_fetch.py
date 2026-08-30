"""Pre-call HTTP data fetch for StartCall node.

Executes an HTTP request before a voice call starts to enrich the
call context with data from external systems (CRM, ERP, etc.).
"""

from typing import Any, Dict, Optional

import httpx
from loguru import logger

from api.db import db_client
from api.errors.failure import (
    DograhFailure,
    ErrorSource,
    ErrorType,
    classify_exception,
    classify_http_response,
    log_failure,
    redact_failure_message,
)
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
            # Pre-call fetch may enrich or override call-level context, including
            # the greeting. Only reserved run-owned metadata is discarded.
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

    if credential_uuid:
        try:
            credential = await db_client.get_credential_by_uuid(
                credential_uuid, organization_id
            )
            if credential:
                headers.update(build_auth_header(credential))
            else:
                log_failure(
                    DograhFailure(
                        source=ErrorSource.INTEGRATION,
                        type=ErrorType.CONFIG_ERROR,
                        code="pre-call-fetch-credential-not-found",
                        internal_message="Pre-call fetch credential was not found",
                        external_message="The credential configured for pre-call fetch no longer exists.",
                        provider="pre-call-fetch",
                        error_owner="user",
                        retryable=False,
                    ),
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                )
        except Exception as e:
            log_failure(
                classify_exception(
                    e,
                    source=ErrorSource.INTEGRATION,
                    provider="pre-call-fetch",
                    error_owner="user",
                ),
                organization_id=organization_id,
                workflow_id=workflow_id,
            )

    logger.info(f"Pre-call fetch: POST {redact_failure_message(url)}")

    try:
        async with httpx.AsyncClient(timeout=PRE_CALL_FETCH_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json=payload)

            try:
                response_data = response.json()
            except Exception:
                response_data = {}

            if response.is_success:
                if not isinstance(response_data, dict):
                    log_failure(
                        DograhFailure(
                            source=ErrorSource.INTEGRATION,
                            type=ErrorType.CONFIG_ERROR,
                            code="pre-call-fetch-invalid-response",
                            internal_message="Pre-call fetch response was not a JSON object",
                            external_message="The pre-call fetch endpoint returned an invalid response.",
                            provider="pre-call-fetch",
                            error_owner="user",
                            retryable=False,
                        ),
                        organization_id=organization_id,
                        workflow_id=workflow_id,
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
                log_failure(
                    classify_http_response(
                        response.status_code,
                        f"Pre-call fetch returned HTTP {response.status_code}: "
                        f"{response.text[:200]}",
                        source=ErrorSource.INTEGRATION,
                        provider="pre-call-fetch",
                        error_owner="user",
                    ),
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                )
                return {}

    except httpx.TimeoutException as e:
        log_failure(
            classify_exception(
                e,
                source=ErrorSource.INTEGRATION,
                provider="pre-call-fetch",
                error_owner="user",
            ),
            organization_id=organization_id,
            workflow_id=workflow_id,
        )
        return {}
    except httpx.RequestError as e:
        log_failure(
            classify_exception(
                e,
                source=ErrorSource.INTEGRATION,
                provider="pre-call-fetch",
                error_owner="user",
            ),
            organization_id=organization_id,
            workflow_id=workflow_id,
        )
        return {}
    except Exception as e:
        log_failure(
            classify_exception(
                e,
                source=ErrorSource.INTEGRATION,
                provider="pre-call-fetch",
                error_owner="user",
            ),
            organization_id=organization_id,
            workflow_id=workflow_id,
        )
        return {}
