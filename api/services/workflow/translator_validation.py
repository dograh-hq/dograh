"""Publish-time predicate that gates workflows wired to the Gemini Live
Translate (preview) realtime provider.

Live Translate is a translation-only model: it ignores LLM prompts,
cannot invoke tools, and has no conversational context to retrieve
documents into. Workflows that *think* they're sending an agent-style
configuration to this provider will appear to "work" at runtime — the
audio will translate — but every prompt/tool/document is silently
dropped. Publishing such a workflow is misleading at best and a
correctness bug at worst, so we reject it here.

This predicate is intentionally **publish-only** and is NOT invoked
from ``/validate`` (draft-shape audit). Draft validation must remain
provider-agnostic so the editor can save mid-edit without errors that
only appear after the user has wired up a realtime provider.
"""

from __future__ import annotations

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.registry import ServiceProviders
from api.services.workflow.dto import NodeType
from api.services.workflow.errors import ItemKind, WorkflowError

_FIX_HINT_TEMPLATE = (
    "Live Translate workflows ignore {field}. Either remove {field} from "
    "this node, or switch the workflow's realtime provider to a Gemini "
    "Live model that supports {field}."
)


def _is_translator_workflow_publishable(
    workflow_def: dict,
    effective_config: EffectiveAIModelConfiguration,
) -> list[WorkflowError]:
    """Return WorkflowErrors blocking publish when a workflow uses Live Translate.

    For any other realtime provider — or for non-realtime workflows —
    returns ``[]`` immediately so the caller never pays the node-walk
    cost.

    Args:
        workflow_def: The workflow JSON dict from the draft being
            published.
        effective_config: The compiled effective AI-model configuration
            for the workflow. Callers should resolve this via
            ``get_effective_ai_model_configuration_for_workflow`` so the
            workflow-level v2 override (if any) takes precedence over
            the organization default.

    Returns:
        Empty list when the workflow is OK to publish; otherwise one
        :class:`WorkflowError` per (node, violating field) pair, using
        :class:`ItemKind.node` so the editor UI surfaces them in the
        existing per-node error channel.
    """
    if not effective_config.is_realtime or effective_config.realtime is None:
        return []
    if (
        effective_config.realtime.provider
        != ServiceProviders.GOOGLE_REALTIME_TRANSLATE.value
    ):
        return []

    errors: list[WorkflowError] = []
    for node in workflow_def.get("nodes") or []:
        node_id = node.get("id")
        data = node.get("data") or {}
        node_type = node.get("type")

        if node_type == NodeType.globalNode.value:
            # Global nodes exist purely to inject a shared prompt; they
            # have no other purpose, so we reject the whole node rather
            # than its prompt field.
            errors.append(
                WorkflowError(
                    kind=ItemKind.node,
                    id=node_id,
                    field="type",
                    message=_FIX_HINT_TEMPLATE.format(field="global nodes"),
                )
            )
            continue

        if data.get("prompt"):
            errors.append(
                WorkflowError(
                    kind=ItemKind.node,
                    id=node_id,
                    field="data.prompt",
                    message=_FIX_HINT_TEMPLATE.format(field="prompts"),
                )
            )
        if data.get("tool_uuids"):
            errors.append(
                WorkflowError(
                    kind=ItemKind.node,
                    id=node_id,
                    field="data.tool_uuids",
                    message=_FIX_HINT_TEMPLATE.format(field="tools"),
                )
            )
        if data.get("document_uuids"):
            errors.append(
                WorkflowError(
                    kind=ItemKind.node,
                    id=node_id,
                    field="data.document_uuids",
                    message=_FIX_HINT_TEMPLATE.format(field="documents"),
                )
            )

    return errors
