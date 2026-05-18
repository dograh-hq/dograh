"""GENERATED — do not edit by hand.

Regenerate with `python -m dograh_sdk.codegen` against the target
Dograh backend. Source of truth: each node's NodeSpec in the backend's
`api/services/workflow/node_specs/` directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional

from dograh_sdk.typed._base import TypedNode


@dataclass(kw_only=True)
class Webhook_Custom_headersRow:
    """
    Additional HTTP headers to include with the request.
    """

    key: str
    """
    HTTP header name (e.g., 'X-Source').
    """
    value: str
    """
    Header value (supports {{template_variables}}).
    """

@dataclass(kw_only=True)
class Webhook(TypedNode):
    """
    Send HTTP request after the workflow completes.  LLM hint: Sends an HTTP
    request to an external system after the workflow completes. The payload
    is a Jinja-templated JSON body with access to `workflow_run_id`,
    `initial_context`, `gathered_context`, `annotations`, and call metadata.
    """

    type: ClassVar[str] = 'webhook'

    name: str = 'Webhook'
    """
    Short identifier shown in the canvas and run logs.
    """

    enabled: bool = True
    """
    When false, the webhook is skipped at run time.
    """

    http_method: Literal['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] = 'POST'
    """
    HTTP verb used for the outbound request.
    """

    endpoint_url: Optional[str] = None
    """
    URL the request is sent to.
    """

    credential_uuid: Optional[str] = None
    """
    Optional credential applied as the Authorization header.
    """

    custom_headers: list[Webhook_Custom_headersRow] = field(default_factory=list)
    """
    Additional HTTP headers to include with the request.
    """

    payload_template: dict[str, Any] = field(default_factory=lambda: {'call_id': '{{workflow_run_id}}', 'first_name': '{{initial_context.first_name}}', 'rsvp': '{{gathered_context.rsvp}}', 'duration': '{{cost_info.call_duration_seconds}}', 'recording_url': '{{recording_url}}', 'transcript_url': '{{transcript_url}}'})
    """
    JSON body of the request. Values are Jinja-rendered against the run
    context — `{{workflow_run_id}}`, `{{gathered_context.foo}}`,
    `{{annotations.qa_xxx}}`, etc.
    """

