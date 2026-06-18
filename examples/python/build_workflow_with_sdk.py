"""Build a multi-node voice agent using the Workflow SDK and save it as a draft.

Requirements:
    pip install -r requirements.txt

Environment variables (loaded from `.env` in this directory):
    DOGRAH_API_ENDPOINT  - Dograh API base URL (e.g. http://localhost:8000)
    DOGRAH_API_TOKEN     - API token sent as X-API-Key

Run:
    python build_workflow_with_sdk.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from dograh_sdk import DograhClient, Workflow

load_dotenv(Path(__file__).parent / ".env")

# Replace with the numeric ID of an existing agent in your Dograh account.
# Create one via the UI or with create_workflow.py if you don't have one yet.
WORKFLOW_ID = 0


def main() -> int:
    api_endpoint = os.environ.get("DOGRAH_API_ENDPOINT", "http://localhost:8000")
    api_token = os.environ.get("DOGRAH_API_TOKEN")

    if not api_token:
        print("DOGRAH_API_TOKEN is required", file=sys.stderr)
        return 1

    if WORKFLOW_ID == 0:
        print("Set WORKFLOW_ID at the top of this file to an existing workflow ID", file=sys.stderr)
        return 1

    with DograhClient(base_url=api_endpoint, api_key=api_token) as client:
        existing = client.get_workflow(WORKFLOW_ID)
        # Preserve the live workflow name; save_workflow sends name with the draft update.
        wf = Workflow(client=client, name=existing.name)

        greeting = wf.add(
            type="startCall",
            name="greeting",
            prompt=(
                "# Goal\n"
                "You are a helpful agent having a conversation over voice with a human. "
                "This is a voice conversation, so transcripts can be error prone.\n\n"
                "## Flow\n"
                "Greet the caller warmly and ask whether they would like to continue."
            ),
        )
        qualify = wf.add(
            type="agentNode",
            name="qualify",
            prompt=(
                "# Goal\n"
                "Qualify the lead by asking about their needs, budget, and timeline.\n\n"
                "## Rules\n"
                "- Keep responses short — 2-3 sentences max\n"
                "- Confirm all three answers before moving on"
            ),
        )
        done = wf.add(
            type="endCall",
            name="done",
            prompt="Thank the caller for their time and let them know the team will follow up shortly.",
        )

        wf.edge(
            greeting,
            qualify,
            label="interested",
            condition="Caller confirms they want to continue.",
        )
        wf.edge(
            qualify,
            done,
            label="qualified",
            condition="All qualification questions have been answered.",
        )

        result = client.save_workflow(workflow_id=WORKFLOW_ID, workflow=wf)
        node_count = len(result.workflow_definition.get("nodes", []))
        print(
            f"Saved workflow {result.id}: {result.name!r} "
            f"(version={result.version_number}, status={result.version_status}, "
            f"nodes={node_count})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
