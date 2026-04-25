"""Fetch a workflow by ID and place a test phone call using the Python SDK.

Requirements:
    pip install -r requirements.txt

Environment variables (loaded from `.env` in this directory):
    DOGRAH_API_ENDPOINT  - Dograh API base URL (e.g. http://localhost:8000)
    DOGRAH_API_TOKEN     - API token sent as X-API-Key

Run:
    python fetch_workflow_and_call.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from dograh_sdk import DograhClient
from dograh_sdk._generated_models import InitiateCallRequest

load_dotenv(Path(__file__).parent / ".env")

# Numeric workflow ID to fetch and call with.
WORKFLOW_ID = 1
# E.164 destination number — set this to the number you want to call.
PHONE_NUMBER = "+1113144411"


def main() -> int:
    api_endpoint = os.environ.get("DOGRAH_API_ENDPOINT", "http://localhost:8000")
    api_token = os.environ.get("DOGRAH_API_TOKEN")

    if not api_token:
        print("DOGRAH_API_TOKEN is required", file=sys.stderr)
        return 1

    with DograhClient(base_url=api_endpoint, api_key=api_token) as client:
        workflow = client.get_workflow(WORKFLOW_ID)
        print(f"Fetched workflow {workflow.id}: {workflow.name!r} (status={workflow.status})")

        response = client.test_phone_call(
            body=InitiateCallRequest(
                workflow_id=WORKFLOW_ID,
                phone_number=PHONE_NUMBER,
            )
        )
        print(f"Call initiated: {response}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
