"""Structurally-accurate GPC retail reference workflow.

Used as the few-shot example in the Claude agent-builder CONTRACT. The prompt
bodies here are short placeholders on purpose — what matters is that every
node/edge/tool key matches ``api.services.workflow.dto`` exactly, so Claude
copies the *shape*, not the prose.

The example bundles the same three top-level keys the generator expects back
from Claude:

    {
      "name": <str>,
      "workflow_definition": {"nodes": [...], "edges": [...], "viewport": {...}},
      "tools": [ {name, description, node_ids, config{...}} ],
      "model_config": { ... v2 override ... }
    }

``tools[].node_ids`` lists the node ids that should reference the created tool;
the generator creates the tool, then writes its uuid into each named node's
``data.tool_uuids``. Nodes therefore ship with ``tool_uuids: []`` — the
generator fills them in.
"""

from __future__ import annotations

from typing import Any, Dict

# A sensible default model configuration (BYOK realtime: google_realtime
# speech-to-speech + a google LLM for reasoning). Stored verbatim under the
# workflow-configuration v2 override key. Keys/secrets are intentionally
# absent — the draft opens in the editor where the user supplies them.
DEFAULT_MODEL_CONFIG: Dict[str, Any] = {
    "version": 2,
    "mode": "byok",
    "byok": {
        "mode": "realtime",
        "realtime": {
            "realtime": {
                "provider": "google_realtime",
                "model": "gemini-3.1-flash-live-preview",
                "voice": "Puck",
                "language": "en",
            },
            "llm": {
                "provider": "google",
                "model": "gemini-2.5-flash",
            },
        },
    },
}


GPC_REFERENCE: Dict[str, Any] = {
    "name": "GPC Retail Order Agent",
    "workflow_definition": {
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "nodes": [
            {
                "id": "persona",
                "type": "globalNode",
                "position": {"x": 0, "y": -200},
                "data": {
                    "name": "Persona",
                    "prompt": (
                        "You are Asha, a warm GPC retail sales assistant. Speak "
                        "in short, natural sentences. Never use special "
                        "characters that cannot be spoken aloud."
                    ),
                },
            },
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Greeting",
                    "prompt": "Greet the customer and ask what they shop for today.",
                    "greeting_type": "text",
                    "greeting": "Hi {{first_name}}, this is Asha from GPC Retail.",
                    "allow_interrupt": True,
                    "add_global_prompt": True,
                    "is_start": True,
                    "tool_uuids": [],
                },
            },
            {
                "id": "probe",
                "type": "agentNode",
                "position": {"x": 0, "y": 250},
                "data": {
                    "name": "Probe Needs",
                    "prompt": "Ask what category and budget the customer wants.",
                    "allow_interrupt": True,
                    "add_global_prompt": True,
                    "extraction_enabled": True,
                    "extraction_prompt": "Capture the product category and budget.",
                    "extraction_variables": [
                        {
                            "name": "category",
                            "type": "string",
                            "prompt": "Product category the customer is after",
                        },
                        {
                            "name": "budget_inr",
                            "type": "number",
                            "prompt": "Approximate budget in INR",
                        },
                    ],
                },
            },
            {
                "id": "recommend",
                "type": "agentNode",
                "position": {"x": 0, "y": 500},
                "data": {
                    "name": "Recommend & Order",
                    "prompt": (
                        "Recommend a matching product and, once the customer "
                        "agrees, place the order using the place_order tool."
                    ),
                    "allow_interrupt": True,
                    "add_global_prompt": True,
                    "tool_uuids": [],
                },
            },
            {
                "id": "kyc",
                "type": "agentNode",
                "position": {"x": 0, "y": 750},
                "data": {
                    "name": "Rate / KYC",
                    "prompt": "Confirm name and delivery address for the order.",
                    "allow_interrupt": True,
                    "add_global_prompt": True,
                    "extraction_enabled": True,
                    "extraction_prompt": "Capture the confirmed delivery address.",
                    "extraction_variables": [
                        {
                            "name": "delivery_address",
                            "type": "string",
                            "prompt": "Full delivery address the customer confirmed",
                        }
                    ],
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 0, "y": 1000},
                "data": {
                    "name": "Successful Close",
                    "prompt": "Thank the customer, confirm the order, and end the call.",
                    "add_global_prompt": False,
                    "is_end": True,
                },
            },
            {
                "id": "crm-webhook",
                "type": "webhook",
                "position": {"x": 400, "y": 1000},
                "data": {
                    "name": "Notify CRM",
                    "enabled": True,
                    "http_method": "POST",
                    "endpoint_url": "https://crm.example.com/calls",
                    "payload_template": {
                        "call_id": "{{workflow_run_id}}",
                        "category": "{{gathered_context.category}}",
                        "address": "{{gathered_context.delivery_address}}",
                    },
                },
            },
        ],
        "edges": [
            {
                "id": "e-start-probe",
                "source": "start",
                "target": "probe",
                "data": {
                    "label": "Begin",
                    "condition": "The customer responded to the greeting.",
                },
            },
            {
                "id": "e-probe-recommend",
                "source": "probe",
                "target": "recommend",
                "data": {
                    "label": "Needs captured",
                    "condition": "Category and budget have been captured.",
                },
            },
            {
                "id": "e-recommend-kyc",
                "source": "recommend",
                "target": "kyc",
                "data": {
                    "label": "Order agreed",
                    "condition": "The customer agreed to place an order.",
                },
            },
            {
                "id": "e-kyc-end",
                "source": "kyc",
                "target": "end",
                "data": {
                    "label": "Confirmed",
                    "condition": "The delivery address has been confirmed.",
                },
            },
        ],
    },
    "tools": [
        {
            "name": "place_order",
            "description": (
                "Place a retail order once the customer confirms the product "
                "and quantity. Call this from the Recommend step."
            ),
            "node_ids": ["recommend"],
            "config": {
                "method": "POST",
                "url": "https://api.example.com/orders",
                "parameters": [
                    {
                        "name": "sku",
                        "type": "string",
                        "description": "SKU of the product the customer agreed to buy",
                        "required": True,
                    },
                    {
                        "name": "quantity",
                        "type": "number",
                        "description": "Number of units to order",
                        "required": True,
                    },
                ],
                "preset_parameters": [
                    {
                        "name": "phone_number",
                        "type": "string",
                        "value_template": "{{initial_context.phone_number}}",
                        "required": True,
                    }
                ],
            },
        }
    ],
    "model_config": DEFAULT_MODEL_CONFIG,
}
