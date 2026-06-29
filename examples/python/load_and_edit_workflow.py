"""Load an existing workflow, edit a node prompt, and save it as a draft.

Requirements:
    pip install -r requirements.txt

Environment variables (loaded from `.env` in this directory):
    DOGRAH_API_ENDPOINT  - Dograh API base URL (e.g. http://localhost:8000)
    DOGRAH_API_TOKEN     - API token sent as X-API-Key

Run:
    python load_and_edit_workflow.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from dograh_sdk import DograhClient
from dograh_sdk._generated_models import UpdateWorkflowRequest

load_dotenv(Path(__file__).parent / ".env")

# Replace with the numeric ID of an existing agent in your Dograh account.
WORKFLOW_ID = 0

# Sentence appended to the startCall node's prompt when the script runs.
PROMPT_SUFFIX = " Please be concise — keep all responses under two sentences."


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
        print(f"Loaded workflow {existing.id}: {existing.name!r} (status={existing.status})")

        definition = dict(existing.workflow_definition)

        for node in definition.get("nodes", []):
            if node.get("type") == "startCall":
                data = dict(node.get("data") or {})
                data["prompt"] = (data.get("prompt") or "") + PROMPT_SUFFIX
                node["data"] = data
                break

        result = client.update_workflow(
            WORKFLOW_ID,
            body=UpdateWorkflowRequest(
                name=existing.name,
                workflow_definition=definition,
            ),
        )
        print(
            f"Saved draft for workflow {result.id}: {result.name!r} "
            f"(version={result.version_number}, status={result.version_status})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
