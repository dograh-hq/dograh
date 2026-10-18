"""WhatsApp template messaging tool execution during live calls."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from api.db import db_client
from api.errors.failure import DograhFailure, ErrorSource, ErrorType, log_failure
from api.services.telephony.factory import get_telephony_provider_by_id
from api.utils.template_renderer import render_template

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _clean_phone_number(val: Any) -> str:
    """Normalize phone number and verify E.164 digit length (7-15 digits)."""
    digits = re.sub(r"\D", "", str(val or ""))
    if 7 <= len(digits) <= 15:
        return digits
    return ""


async def execute_whatsapp_tool(
    tool: Any,
    arguments: dict[str, Any],
    call_context_vars: dict[str, Any] | None = None,
    gathered_context_vars: dict[str, Any] | None = None,
    organization_id: int | None = None,
) -> dict[str, Any]:
    """Execute an approved WhatsApp template tool during a call.

    Args:
        tool: ToolModel instance.
        arguments: Arguments passed by the LLM.
        call_context_vars: Initial/call context (direction, caller_number, called_number, etc.).
        gathered_context_vars: Extracted context variables.
        organization_id: Organization ID for credential lookup.

    Returns:
        Result dictionary for LLM tool call response.
    """
    definition = tool.definition or {}
    config = definition.get("config", {})

    initial_context = dict(call_context_vars or {})
    call_direction = str(
        initial_context.get("direction") or initial_context.get("call_direction") or ""
    ).lower()
    gathered_context = dict(gathered_context_vars or {})
    render_context: dict[str, Any] = {
        **initial_context,
        **gathered_context,
        "initial_context": initial_context,
        "gathered_context": gathered_context,
    }

    # 1. Resolve Multi-Template Configuration first so template overrides are known
    templates = config.get("templates") or []
    if not templates and config.get("template_name"):
        templates = [
            {
                "id": config.get("template_name"),
                "name": config.get("template_name"),
                "description": "",
                "template_name": config.get("template_name"),
                "language_code": config.get("language_code", "en_US"),
                "from_phone_number_id": config.get("from_phone_number_id"),
                "fallback_recipient_phone_number": config.get("fallback_recipient_phone_number"),
                "variable_mappings": config.get("variable_mappings") or [],
            }
        ]

    target_template: dict[str, Any] | None = None
    requested_template_id = arguments.get("template_id") or arguments.get("template_name")

    if requested_template_id and templates:
        req_str = str(requested_template_id).strip().lower()
        target_template = next(
            (
                t
                for idx, t in enumerate(templates)
                if str(t.get("id", "")).strip().lower() == req_str
                or str(idx + 1) == req_str
                or str(t.get("template_name", "")).strip().lower() == req_str
                or str(t.get("name", "")).strip().lower() == req_str
            ),
            None,
        )

    if not target_template and templates:
        target_template = templates[0]

    if target_template:
        template_name = target_template.get("template_name")
        language_code = target_template.get("language_code", "en_US")
        variable_mappings = target_template.get("variable_mappings") or []
        from_phone_number_id = target_template.get("from_phone_number_id")
    else:
        template_name = requested_template_id
        language_code = "en_US"
        variable_mappings = []
        from_phone_number_id = None

    if not template_name:
        err_msg = "No WhatsApp template was selected or provided to send."
        logger.warning(f"WhatsApp tool '{tool.name}': {err_msg}")
        return {"status": "error", "error": err_msg}

    # 2. Automatic Recipient Phone Number Resolution
    # Priority: explicit argument -> call context (caller/called) -> template fallback -> tool fallback
    raw_recipient: str | None = (
        arguments.get("recipient_phone_number")
        or arguments.get("recipient_phone")
        or arguments.get("to_number")
    )

    if not raw_recipient:
        if call_direction == "outbound":
            # Outbound call: the lead is called_number
            raw_recipient = (
                initial_context.get("called_number")
                or initial_context.get("to")
                or initial_context.get("phone_number")
                or initial_context.get("to_number")
                or initial_context.get("customer_phone_number")
            )
        else:
            # Inbound call: the caller is caller_number
            raw_recipient = (
                initial_context.get("caller_number")
                or initial_context.get("from_number")
                or initial_context.get("phone_number")
            )

    clean_recipient = _clean_phone_number(raw_recipient)
    if not clean_recipient:
        # Check configured template fallback or tool fallback recipient phone number
        fallback_recipient = (
            (target_template.get("fallback_recipient_phone_number") if target_template else None)
            or config.get("fallback_recipient_phone_number")
            or config.get("test_recipient_phone_number")
        )
        if fallback_recipient:
            clean_recipient = _clean_phone_number(fallback_recipient)

    if not clean_recipient:
        err_msg = (
            f"Could not resolve a valid recipient phone number automatically from call context "
            f"(raw value: {raw_recipient}). If testing via web browser or if caller ID is unknown, "
            f"please ask the user for their phone number, configure a Fallback Recipient Phone Number "
            f"in Delivery Settings, or provide recipient_phone_number in E.164 format."
        )
        logger.warning(f"WhatsApp tool '{tool.name}': {err_msg}")
        return {"status": "error", "error": err_msg}

    # 3. Resolve Placeholder Parameters ({{1}}, {{2}}, etc.)
    param_list: list[str] = []

    if variable_mappings:
        sorted_mappings = sorted(variable_mappings, key=lambda m: m.get("index", 1))
        for mapping in sorted_mappings:
            source = mapping.get("source", "llm")
            mapping_val = mapping.get("value", "")
            fallback_val = mapping.get("fallback_value")
            param_name = mapping.get("name") or f"variable_{mapping.get('index')}"

            if source == "context":
                rendered = render_template(mapping_val, render_context)
                if rendered is not None and str(rendered).strip():
                    param_list.append(str(rendered).strip())
                elif fallback_val is not None and str(fallback_val).strip():
                    param_list.append(str(fallback_val).strip())
                else:
                    if param_name in ("template_id", "template_name", "recipient_phone_number", "parameters"):
                        llm_arg_val = arguments.get(f"param_{param_name}") or arguments.get(f"var_{param_name}", "")
                    else:
                        llm_arg_val = arguments.get(param_name)
                    if str(llm_arg_val or "").strip():
                        param_list.append(str(llm_arg_val).strip())
                    else:
                        err_msg = (
                            f"Missing required parameter '{param_name}' for template '{template_name}'. "
                            f"Context variable '{mapping_val}' was not found, no fallback was configured, "
                            f"and no value was supplied by LLM."
                        )
                        logger.warning(f"WhatsApp tool '{tool.name}': {err_msg}")
                        return {"status": "error", "error": err_msg}
            elif source in ("static", "literal"):
                if mapping_val is not None and str(mapping_val).strip():
                    param_list.append(str(mapping_val).strip())
                elif fallback_val is not None and str(fallback_val).strip():
                    param_list.append(str(fallback_val).strip())
                else:
                    err_msg = f"Missing static text value for variable '{param_name}' in template '{template_name}'."
                    logger.warning(f"WhatsApp tool '{tool.name}': {err_msg}")
                    return {"status": "error", "error": err_msg}
            elif source == "lead_phone":
                target_lead_phone = None
                if call_direction == "outbound":
                    target_lead_phone = _clean_phone_number(
                        initial_context.get("called_number")
                        or initial_context.get("to")
                        or initial_context.get("to_number")
                        or initial_context.get("customer_phone_number")
                    )
                else:
                    target_lead_phone = _clean_phone_number(
                        initial_context.get("caller_number")
                        or initial_context.get("from_number")
                        or initial_context.get("phone_number")
                    )
                if target_lead_phone:
                    param_list.append(target_lead_phone)
                elif fallback_val is not None and str(fallback_val).strip():
                    param_list.append(str(fallback_val).strip())
                elif clean_recipient:
                    param_list.append(clean_recipient)
                else:
                    err_msg = f"Missing lead phone number for variable '{param_name}' in template '{template_name}'."
                    logger.warning(f"WhatsApp tool '{tool.name}': {err_msg}")
                    return {"status": "error", "error": err_msg}
            else:  # "llm"
                if param_name in ("template_id", "template_name", "recipient_phone_number", "parameters"):
                    llm_arg_val = arguments.get(f"param_{param_name}") or arguments.get(f"var_{param_name}", "")
                else:
                    llm_arg_val = arguments.get(param_name)
                if str(llm_arg_val or "").strip():
                    param_list.append(str(llm_arg_val).strip())
                elif fallback_val is not None and str(fallback_val).strip():
                    param_list.append(str(fallback_val).strip())
                else:
                    err_msg = f"Missing required parameter '{param_name}' for template '{template_name}'."
                    logger.warning(f"WhatsApp tool '{tool.name}': {err_msg}")
                    return {"status": "error", "error": err_msg}
    elif "parameters" in arguments and isinstance(arguments["parameters"], list):
        param_list = [str(p if str(p).strip() else "-") for p in arguments["parameters"]]

    # 4. Resolve WhatsApp Provider Instance
    if not organization_id:
        return {
            "status": "error",
            "error": "Organization ID is required to execute WhatsApp tools.",
        }

    try:
        config_id = config.get("telephony_configuration_id")
        if not config_id:
            configs = await db_client.list_telephony_configurations_by_provider(
                organization_id, "whatsapp", active_only=True
            )
            if not configs:
                return {
                    "status": "error",
                    "error": (
                        "No active WhatsApp telephony configuration found for this "
                        "organization. Please connect WhatsApp in Settings."
                    ),
                }
            config_id = configs[0].id

        provider = await get_telephony_provider_by_id(config_id, organization_id)
    except Exception as e:
        logger.error(f"Failed to resolve WhatsApp provider for tool '{tool.name}': {e}")
        return {
            "status": "error",
            "error": f"Failed to initialize WhatsApp provider: {e}",
        }

    execution_mode = config.get("execution_mode", "async")

    masked_recipient = f"...{clean_recipient[-4:]}" if len(clean_recipient) >= 4 else "***"
    logger.info(
        f"Executing WhatsApp tool '{tool.name}' ({execution_mode}) -> "
        f"template '{template_name}' from {from_phone_number_id or 'default'} to {masked_recipient} param_count={len(param_list)}"
    )

    # 5. Execute Dispatch (Async vs Sync)
    async def _send_msg():
        try:
            return await provider.send_template_message(
                to_number=clean_recipient,
                template_name=template_name,
                language_code=language_code,
                parameters=param_list,
                from_phone_number_id=from_phone_number_id,
            )
        except Exception as e:
            logger.error(f"WhatsApp delivery failed in background: {e}")
            log_failure(
                DograhFailure(
                    source=ErrorSource.TOOL,
                    type=ErrorType.PROVIDER_ERROR,
                    code="whatsapp-delivery-failed",
                    internal_message=f"WhatsApp delivery failed: {e}",
                    external_message="Failed to deliver WhatsApp message.",
                    provider="whatsapp",
                    error_owner="user",
                    retryable=True,
                ),
                organization_id=organization_id,
                tool_name=tool.name,
            )
            if execution_mode != "async":
                raise

    if execution_mode == "async":
        task = asyncio.create_task(_send_msg())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return {
            "status": "queued",
            "message": (
                f"WhatsApp template '{template_name}' queued for delivery to {clean_recipient}."
            ),
        }

    # Sync mode: await Meta Graph API response
    try:
        res = await _send_msg()
        return {
            "status": "delivered",
            "data": res,
            "message": f"WhatsApp template '{template_name}' sent successfully.",
        }
    except Exception as e:
        return {
            "status": "error",
            "error": f"WhatsApp delivery failed: {e}",
        }
