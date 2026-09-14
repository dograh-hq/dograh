"""Shared rules for merging externally supplied workflow-run context."""

from collections.abc import Mapping
from typing import Any

from api.services.managed_model_services import MPS_CORRELATION_ID_CONTEXT_KEY

# The Dograh workflow run id, exposed to prompts and in-call tools so records
# written mid-call can be correlated back to the run. Named to match the
# top-level key post-run webhook nodes already render (see
# api/tasks/run_integrations.py::_build_render_context).
WORKFLOW_RUN_ID_CONTEXT_KEY = "workflow_run_id"

# These values describe or authorize the run itself. External context may add
# prompt variables, but it must never supply or replace run-owned metadata.
RESERVED_INITIAL_CONTEXT_KEYS = frozenset(
    {
        "provider",
        "runtime_configuration",
        MPS_CORRELATION_ID_CONTEXT_KEY,
        WORKFLOW_RUN_ID_CONTEXT_KEY,
    }
)

GREETING_OVERRIDE_CONTEXT_KEY = "greeting_override"

# Run-owned names that tool templates may address at the top level, without the
# ``initial_context.`` prefix. Only reserved keys belong here: external context
# cannot supply them, so they cannot shadow a caller-supplied prompt variable.
RUN_OWNED_TEMPLATE_KEYS = (WORKFLOW_RUN_ID_CONTEXT_KEY,)


def run_owned_template_vars(
    call_context_vars: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the run-owned subset of the call context for top-level rendering.

    The URL and preset-parameter contexts spread the whole call context at the
    top level, but the body context reserves the top level for LLM and preset
    arguments. Layering these keys underneath the arguments keeps the unprefixed
    spelling (``{{workflow_run_id}}``) working in a body template too, so the
    same template text means the same thing on every surface.
    """
    context = call_context_vars or {}
    return {key: context[key] for key in RUN_OWNED_TEMPLATE_KEYS if key in context}


def merge_external_initial_context(
    initial_context: Mapping[str, Any] | None,
    external_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge external variables without accepting reserved run-owned keys."""
    merged = dict(initial_context or {})
    if not external_context:
        return merged

    merged.update(
        {
            key: value
            for key, value in external_context.items()
            if key not in RESERVED_INITIAL_CONTEXT_KEYS
        }
    )
    return merged
