"""Built-in workflow templates seeded into the database on startup.

Each entry produces a row in `workflow_templates` so self-hosted users get
working starter flows without needing the hosted MPS workflow generator.

The shapes mirror what `/api/v1/workflow/templates/duplicate` consumes —
ReactFlow JSON with nodes (startCall / agentNode / endCall) and edges
keyed by `condition`. Keep these in sync with `api/services/workflow/dto.py`.
"""

from __future__ import annotations

from typing import Any


def _node(
    node_id: str,
    node_type: str,
    x: float,
    y: float,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": x, "y": y},
        "data": data,
    }


def _edge(
    edge_id: str,
    source: str,
    target: str,
    label: str,
    condition: str,
) -> dict[str, Any]:
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "data": {"label": label, "condition": condition},
    }


def lead_qualification_template() -> dict[str, Any]:
    """Inbound lead-qualification agent matching the Better Stack demo.

    Greets the caller, collects what they want to build, asks about
    company size, industry, and approximate call volume / budget, then
    routes qualified leads to a human handoff and politely closes
    unqualified leads.
    """

    nodes = [
        _node(
            "start",
            "startCall",
            x=0,
            y=0,
            data={
                "name": "Greeting",
                "is_start": True,
                "greeting_type": "text",
                "greeting": (
                    "Hi, this is Sarah from Dograh AI. Thanks for reaching out — "
                    "how can I help you today?"
                ),
                "prompt": (
                    "You are Sarah, a friendly inbound voice agent for Dograh AI. "
                    "Open the call warmly. Ask the caller what they're looking to "
                    "build or solve with a voice AI agent. Listen for the high-"
                    "level use case and acknowledge it before moving on."
                ),
                "allow_interrupt": True,
                "add_global_prompt": True,
                "extraction_enabled": True,
                "extraction_prompt": (
                    "Capture the caller's stated use case in one short phrase."
                ),
                "extraction_variables": [
                    {
                        "name": "use_case",
                        "type": "string",
                        "prompt": (
                            "Short phrase describing what the caller wants to "
                            "build, e.g. 'inbound demo qualification'."
                        ),
                    }
                ],
            },
        ),
        _node(
            "qualify",
            "agentNode",
            x=420,
            y=0,
            data={
                "name": "Qualify Lead",
                "prompt": (
                    "Qualify the lead. Ask, in order:\n"
                    "  1. Their company name and industry.\n"
                    "  2. Approximate company size (employees).\n"
                    "  3. Estimated monthly voice-agent minutes or rough budget.\n"
                    "Acknowledge each answer briefly. Do not pitch product."
                ),
                "allow_interrupt": True,
                "add_global_prompt": True,
                "extraction_enabled": True,
                "extraction_prompt": (
                    "Extract the qualification fields from the caller's answers."
                ),
                "extraction_variables": [
                    {
                        "name": "company_name",
                        "type": "string",
                        "prompt": "Company name stated by the caller.",
                    },
                    {
                        "name": "industry",
                        "type": "string",
                        "prompt": "Industry or vertical (e.g. healthcare, fintech).",
                    },
                    {
                        "name": "company_size",
                        "type": "number",
                        "prompt": "Approximate employee count.",
                    },
                    {
                        "name": "monthly_minutes",
                        "type": "number",
                        "prompt": (
                            "Estimated monthly call minutes the caller expects "
                            "to run through a voice agent. Convert phrases like "
                            "'around twenty thousand minutes' to 20000."
                        ),
                    },
                ],
            },
        ),
        _node(
            "qualified_handoff",
            "endCall",
            x=900,
            y=-160,
            data={
                "name": "Qualified — Hand Off",
                "is_end": True,
                "prompt": (
                    "Tell {{company_name}} that they qualify and that a Dograh "
                    "specialist will reach out by email within one business day "
                    "to schedule a demo. Confirm the best email address, thank "
                    "the caller, and end the call politely."
                ),
                "allow_interrupt": False,
                "add_global_prompt": True,
            },
        ),
        _node(
            "unqualified_close",
            "endCall",
            x=900,
            y=160,
            data={
                "name": "Unqualified — Polite Close",
                "is_end": True,
                "prompt": (
                    "Thank the caller for their time, mention that Dograh's "
                    "open-source self-hosted option may be a great fit for them, "
                    "and point them to docs.dograh.com. End the call politely."
                ),
                "allow_interrupt": False,
                "add_global_prompt": True,
            },
        ),
    ]

    edges = [
        _edge(
            "e1",
            "start",
            "qualify",
            label="Caller shared a use case",
            condition=(
                "The caller has described what they want to build with a voice "
                "AI agent at any level of detail."
            ),
        ),
        _edge(
            "e2",
            "qualify",
            "qualified_handoff",
            label="Qualified lead",
            condition=(
                "The caller works at a company with at least 20 employees AND "
                "either monthly_minutes is 5000 or higher OR they indicated a "
                "non-trivial budget."
            ),
        ),
        _edge(
            "e3",
            "qualify",
            "unqualified_close",
            label="Not qualified",
            condition=(
                "The caller does not meet the qualification bar (very small "
                "team, no budget, or no concrete use case)."
            ),
        ),
    ]

    return {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}


BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "template_name": "Lead Qualification (Inbound)",
        "template_description": (
            "Inbound voice agent that greets callers, asks what they want to "
            "build, qualifies on company size and call volume, and routes "
            "qualified leads to a human handoff."
        ),
        "template_json": lead_qualification_template(),
    },
]
