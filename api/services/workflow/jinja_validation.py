from api.services.workflow.errors import ItemKind, WorkflowError
from api.utils.jinja_engine import validate_jinja_syntax


def validate_workflow_jinja_templates(
    workflow_definition: dict | None,
) -> list[WorkflowError]:
    """Validate that prompt, greeting, and extraction fields in workflow definition contain valid Jinja templates."""
    if not isinstance(workflow_definition, dict):
        return []
    nodes = workflow_definition.get("nodes")
    if not isinstance(nodes, list):
        return []
    errors: list[WorkflowError] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        for field_name in ("prompt", "greeting", "extraction_prompt"):
            val = data.get(field_name)
            if isinstance(val, str) and ("{{" in val or "{%" in val):
                is_valid, err_msg = validate_jinja_syntax(val)
                if not is_valid:
                    errors.append(
                        WorkflowError(
                            kind=ItemKind.node,
                            id=node_id,
                            field=f"data.{field_name}",
                            message=f"Invalid Jinja template in {field_name}: {err_msg}",
                        )
                    )
        extraction_vars = data.get("extraction_variables")
        if isinstance(extraction_vars, list):
            for idx, ev in enumerate(extraction_vars):
                if isinstance(ev, dict):
                    ev_prompt = ev.get("prompt")
                    if isinstance(ev_prompt, str) and (
                        "{{" in ev_prompt or "{%" in ev_prompt
                    ):
                        is_valid, err_msg = validate_jinja_syntax(ev_prompt)
                        if not is_valid:
                            errors.append(
                                WorkflowError(
                                    kind=ItemKind.node,
                                    id=node_id,
                                    field=f"data.extraction_variables[{idx}].prompt",
                                    message=f"Invalid Jinja template in extraction variable: {err_msg}",
                                )
                            )
    return errors
