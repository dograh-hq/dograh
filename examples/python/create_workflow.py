"""Create a new workflow using the Python SDK.

Requirements:
    pip install -r requirements.txt

Environment variables (loaded from `.env` in this directory):
    DOGRAH_API_ENDPOINT  - Dograh API base URL (e.g. http://localhost:8000)
    DOGRAH_API_TOKEN     - API token sent as X-API-Key

Run:
    python create_workflow.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from dograh_sdk import DograhClient
from dograh_sdk._generated_models import CreateWorkflowRequest

load_dotenv(Path(__file__).parent / ".env")

WORKFLOW_NAME = "My SDK-created agent"

# A minimal starter agent with a single `startCall` node that greets the user.
# Open the new agent in the Dograh UI to extend it, or edit this dict and
# re-run to tweak the starting definition.
WORKFLOW_DEFINITION: dict = {
    "nodes": [
        {
            "id": "1",
            "type": "startCall",
            "position": {"x": 271, "y": 4},
            "data": {
                "name": "start call",
                "greeting_type": "text",
                "prompt": (
                    "# Goal\n"
                    "You are a helpful agent having a conversation over voice with a human. "
                    "This is a voice conversation, so transcripts can be error prone.\n\n"
                    "## Rules\n"
                    "- Language: UK English\n"
                    "- Keep responses short — 2-3 sentences max\n"
                    "- If you have to repeat something you said in your previous two turns, "
                    "rephrase while keeping the same meaning.\n\n"
                    "## Speech Handling\n"
                    "- Accept variations: yes/yeah/yep/aye, no/nah/nope\n"
                    "- If user says \"sorry?\" or \"pardon me\" or \"can you repeat\", "
                    "just repeat what you just said.\n\n"
                    "### Flow\n"
                    "Start by saying \"Hi\". Be polite and courteous."
                ),
                "allow_interrupt": False,
                "add_global_prompt": False,
                "delayed_start": False,
                "delayed_start_duration": 2,
                "extraction_enabled": False,
                "pre_call_fetch_enabled": False,
            },
        },
    ],
    "edges": [],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}


def main() -> int:
    api_endpoint = os.environ.get("DOGRAH_API_ENDPOINT", "http://localhost:8000")
    api_token = os.environ.get("DOGRAH_API_TOKEN")

    if not api_token:
        print("DOGRAH_API_TOKEN is required", file=sys.stderr)
        return 1

    with DograhClient(base_url=api_endpoint, api_key=api_token) as client:
        workflow = client.create_workflow(
            body=CreateWorkflowRequest(
                name=WORKFLOW_NAME,
                workflow_definition=WORKFLOW_DEFINITION,
            )
        )
        print(f"Created workflow {workflow.id}: {workflow.name!r} (status={workflow.status})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
