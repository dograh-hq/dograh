from typing import Any

from api.services.workflow.errors import ItemKind, WorkflowError
from api.utils.jinja_engine import validate_jinja_syntax


def _check_jinja(
    text: Any, kind: ItemKind, item_id: str, field: str, label: str
) -> WorkflowError | None:
    if isinstance(text, str) and ("{{" in text or "{%" in text):
        is_valid, err_msg = validate_jinja_syntax(text)
        if not is_valid:
            return WorkflowError(
                kind=kind,
                id=item_id,
                field=field,
                message=f"Invalid Jinja template in {label}: {err_msg}",
            )
    return None


def validate_workflow_jinja_templates(
    workflow_definition: dict | None,
) -> list[WorkflowError]:
    """Validate that prompts, greetings, announcements, extractions, tool messages,
    disposition descriptions, and edge transitions contain valid, safe Jinja templates."""
    if not isinstance(workflow_definition, dict):
        return []

    errors: list[WorkflowError] = []

    nodes = workflow_definition.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or "")
            data = node.get("data")
            if not isinstance(data, dict):
                continue

            for field_name in (
                "prompt",
                "greeting",
                "extraction_prompt",
                "announcement",
                "introduction",
            ):
                err = _check_jinja(
                    data.get(field_name),
                    ItemKind.node,
                    node_id,
                    f"data.{field_name}",
                    field_name,
                )
                if err:
                    errors.append(err)

            extraction_vars = data.get("extraction_variables")
            if isinstance(extraction_vars, list):
                for idx, ev in enumerate(extraction_vars):
                    if isinstance(ev, dict):
                        err = _check_jinja(
                            ev.get("prompt"),
                            ItemKind.node,
                            node_id,
                            f"data.extraction_variables[{idx}].prompt",
                            f"extraction variable prompt ({ev.get('name', idx)})",
                        )
                        if err:
                            errors.append(err)

            custom_tools = data.get("custom_tools")
            if isinstance(custom_tools, list):
                for t_idx, tool in enumerate(custom_tools):
                    if not isinstance(tool, dict):
                        continue
                    for tool_field in ("introduction", "announcement"):
                        err = _check_jinja(
                            tool.get(tool_field),
                            ItemKind.node,
                            node_id,
                            f"data.custom_tools[{t_idx}].{tool_field}",
                            f"tool {tool_field}",
                        )
                        if err:
                            errors.append(err)
                    messages = tool.get("messages")
                    if isinstance(messages, list):
                        for m_idx, msg in enumerate(messages):
                            if isinstance(msg, dict):
                                err = _check_jinja(
                                    msg.get("text"),
                                    ItemKind.node,
                                    node_id,
                                    f"data.custom_tools[{t_idx}].messages[{m_idx}].text",
                                    "tool message text",
                                )
                                if err:
                                    errors.append(err)

            disposition_opts = data.get("disposition_options")
            if isinstance(disposition_opts, list):
                for d_idx, opt in enumerate(disposition_opts):
                    if isinstance(opt, dict):
                        err = _check_jinja(
                            opt.get("description"),
                            ItemKind.node,
                            node_id,
                            f"data.disposition_options[{d_idx}].description",
                            f"disposition option description ({opt.get('name') or opt.get('code') or d_idx})",
                        )
                        if err:
                            errors.append(err)

    edges = workflow_definition.get("edges")
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            edge_id = str(edge.get("id") or "")
            speech = edge.get("transition_speech")
            if speech is None and isinstance(edge.get("data"), dict):
                speech = edge["data"].get("transition_speech")
            err = _check_jinja(
                speech,
                ItemKind.edge,
                edge_id,
                "transition_speech",
                "edge transition speech",
            )
            if err:
                errors.append(err)

    return errors
