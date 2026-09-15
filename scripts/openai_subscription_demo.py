"""Offline demo preparation and an opt-in synthetic HTTP lookup for browser proof."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

DEMO_TOOL_UUID = "00000000-0000-4000-8000-000000000001"
DEMO_URL = "http://127.0.0.1:18765/subscription-demo/lookup"
CATALOG = {"amber": "Bay 4", "cyan": "Bay 9"}


def lookup_response(path: str) -> tuple[int, dict]:
    parsed = urlsplit(path)
    values = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path != "/subscription-demo/lookup":
        return 404, {"error": "unknown_fixture_path"}
    if set(values) != {"item"} or len(values["item"]) != 1:
        return 400, {"error": "one_item_required"}
    item = values["item"][0]
    if item not in CATALOG:
        return 400, {"error": "use_amber_or_cyan"}
    return 200, {"synthetic": True, "item": item, "location": CATALOG[item]}


def tool_definition() -> dict:
    return {
        "name": "subscription_demo_lookup",
        "description": "Look up the synthetic location of item amber or cyan. No real orders or actions.",
        "category": "http_api",
        "definition": {
            "type": "http_api",
            "config": {
                "method": "GET",
                "url": DEMO_URL,
                "timeout_ms": 10000,
                "parameters": [
                    {
                        "name": "item",
                        "type": "string",
                        "required": True,
                        "description": "Exactly amber or cyan; use the user's latest correction.",
                    }
                ],
            },
        },
    }


def workflow_definition(tool_uuid: str) -> dict:
    UUID(tool_uuid)
    common = {
        "allow_interrupt": True,
        "add_global_prompt": False,
        "extraction_enabled": False,
        "tool_uuids": [tool_uuid],
    }
    return {
        "name": "Subscription voice synthetic browser proof",
        "workflow_definition": {
            "nodes": [
                {
                    "id": "subscription-demo-start",
                    "type": "startCall",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        **common,
                        "name": "Synthetic lookup",
                        "is_start": True,
                        "greeting_type": "text",
                        "greeting": "This is a synthetic demo. Which item: amber or cyan?",
                        "prompt": "Ask for amber or cyan, then call subscription_demo_lookup with that item. Never invent a location. If the user corrects the item while the tool runs, call it again with the latest item. Do not present the old result as current. Once the latest lookup completes, move to Review result. Do not book, send, transfer, or access external records.",
                    },
                },
                {
                    "id": "subscription-demo-review",
                    "type": "agentNode",
                    "position": {"x": 360, "y": 0},
                    "data": {
                        **common,
                        "name": "Review result",
                        "prompt": "Say the synthetic item's location from the latest tool result once. Explain briefly that the lookup made no changes to real data. If corrected, call subscription_demo_lookup with the latest item before answering. Accept interruptions. Wait for the user to say finish before moving to End demo.",
                    },
                },
                {
                    "id": "subscription-demo-end",
                    "type": "endCall",
                    "position": {"x": 720, "y": 0},
                    "data": {
                        "name": "End demo",
                        "is_end": True,
                        "prompt": "Say the synthetic demo is complete and end the conversation.",
                        "allow_interrupt": True,
                        "add_global_prompt": False,
                        "extraction_enabled": False,
                    },
                },
            ],
            "edges": [
                {
                    "id": "subscription-demo-review-edge",
                    "source": "subscription-demo-start",
                    "target": "subscription-demo-review",
                    "data": {
                        "label": "Review result",
                        "condition": "The lookup for the user's latest item has completed.",
                    },
                },
                {
                    "id": "subscription-demo-end-edge",
                    "source": "subscription-demo-review",
                    "target": "subscription-demo-end",
                    "data": {
                        "label": "Finish demo",
                        "condition": "The user explicitly says finish.",
                    },
                },
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 0.9},
        },
    }


def evidence_template() -> dict:
    return {
        "result": "NOT_RUN",
        "test_date_utc": None,
        "dograh_sha": None,
        "pipecat_sha": None,
        "subscription_model": "gpt-live-1-codex",
        "voice": "cove",
        "endpoint_contract_observed": None,
        "planned_voice_auth": "subscription",
        "observed_voice_auth": None,
        "planned_reasoning_auth": "subscription",
        "reasoning_model": "gpt-5.6-luna",
        "developer_api_key_required": False,
        "observed_reasoning_auth": None,
        "redacted_configuration_receipt": None,
        "workflow_id": None,
        "run_ids": [],
        "recording_reference": None,
        "checks": {
            name: {"result": "NOT_RUN", "receipt": None}
            for name in (
                "status_and_saved_configuration",
                "audible_greeting_once",
                "latest_correction_used",
                "workflow_tool_receipt",
                "node_transition",
                "interruption_and_resume",
                "end_and_fresh_session",
                "concurrent_session_rejected",
                "invalid_login",
                "network_failure",
                "no_secret_in_user_error",
            )
        },
        "timeline": [],
        "known_limits": [
            "Experimental ChatGPT backend compatibility path; no official API support claimed.",
            "Account entitlement, audible behavior, and telephony compatibility require live evidence.",
            "Explicit embeddings, separate QA providers, and telephony may have their own charges.",
        ],
    }


def validate_fixture(tool_uuid: str = DEMO_TOOL_UUID) -> None:
    # Import only schemas and pure graph checks; never load the app or test DB hooks.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from api.schemas.tool import CreateToolRequest
    from api.services.workflow.audit import audit_definition
    from api.services.workflow.dto import ReactFlowDTO

    CreateToolRequest.model_validate(tool_definition())
    graph = workflow_definition(tool_uuid)["workflow_definition"]
    ReactFlowDTO.model_validate(graph)
    violations = audit_definition(graph["nodes"], graph["edges"])
    if violations:
        raise ValueError(f"Fixture graph violates Dograh constraints: {violations}")


class LookupHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        started = time.monotonic()
        status, result = lookup_response(self.path)
        if status == 200:
            time.sleep(2)
        body = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        print(
            json.dumps(
                {
                    "time_utc": datetime.now(UTC).isoformat(),
                    "event": "synthetic_lookup",
                    "status": status,
                    "item": result.get("item"),
                    "location": result.get("location"),
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                }
            ),
            flush=True,
        )

    def log_message(self, format: str, *args) -> None:
        # Do not capture arbitrary request paths, headers, or browser tokens.
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        "validate", help="Validate the synthetic fixtures offline (default)."
    )
    prepare = commands.add_parser(
        "prepare", help="Write local import JSON and a pending evidence template."
    )
    prepare.add_argument(
        "--tool-uuid",
        required=True,
        help="UUID of the synthetic tool created in your test organization.",
    )
    prepare.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New output directory; existing directories are refused.",
    )
    commands.add_parser(
        "tool", help="Print the synthetic HTTP tool definition; no API requests."
    )
    commands.add_parser(
        "serve",
        help="Explicitly serve synthetic GET lookups on 127.0.0.1:18765; Ctrl+C stops.",
    )
    args = parser.parse_args()
    if args.command in (None, "validate"):
        validate_fixture()
        print(
            "PASS: Dograh workflow DTO, graph constraints, and HTTP tool schema. No provider or database calls."
        )
    elif args.command == "tool":
        print(json.dumps(tool_definition(), indent=2))
    elif args.command == "prepare":
        validate_fixture(args.tool_uuid)
        args.output.mkdir(parents=True, exist_ok=False)
        for name, value in {
            "workflow.json": workflow_definition(args.tool_uuid),
            "tool.json": tool_definition(),
            "evidence.json": evidence_template(),
        }.items():
            (args.output / name).write_text(
                json.dumps(value, indent=2) + "\n", encoding="utf-8"
            )
        print(
            f"Prepared {args.output}. No agent imported and no voice session started."
        )
    elif args.command == "serve":
        with ThreadingHTTPServer(("127.0.0.1", 18765), LookupHandler) as server:
            print(
                f"Synthetic lookup listening at {DEMO_URL}; no outbound requests. Ctrl+C stops.",
                flush=True,
            )
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
