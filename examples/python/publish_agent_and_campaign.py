"""Create a voice agent with the Workflow SDK, validate it, and publish.

`create_workflow` stores version 1 as published. Later edits go through
`save_workflow`, which writes a draft. `validate_workflow` checks that
draft, and `publish_workflow` promotes it so runtime uses the new graph.

Campaigns dial a CSV that is already stored in Dograh. Leave
`CAMPAIGN_SOURCE_ID` empty to stop after publish. Set it to also create
and start the campaign.

Requirements:
    pip install -r requirements.txt

Environment variables (loaded from `.env` in this directory):
    DOGRAH_API_ENDPOINT  - Dograh API base URL (e.g. http://localhost:8000)
    DOGRAH_API_TOKEN     - API token sent as X-API-Key

Run:
    python publish_agent_and_campaign.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from dograh_sdk import DograhClient, Workflow
from dograh_sdk._generated_models import CreateCampaignRequest, CreateWorkflowRequest
from dograh_sdk.errors import ApiError

load_dotenv(Path(__file__).parent / ".env")

WORKFLOW_NAME = "SDK publish example"
CAMPAIGN_NAME = "SDK campaign example"

# CSV object key from the Dograh campaign uploader, for example
# "campaigns/<org_id>/<uuid>_contacts.csv". The file needs a phone_number
# column. Leave empty to stop after the agent is published.
CAMPAIGN_SOURCE_ID = ""

GREETING_PROMPT = (
    "# Goal\n"
    "You are a helpful agent having a conversation over voice with a human. "
    "Greet the caller and ask whether they would like to continue."
)
# Applied on the draft so publish promotes a change, not a no-op rewrite.
GREETING_PROMPT_DRAFT = GREETING_PROMPT + "\n\nKeep every reply to one or two sentences."


def build_agent(client: DograhClient, *, greeting_prompt: str) -> Workflow:
    wf = Workflow(client=client, name=WORKFLOW_NAME)
    greeting = wf.add(type="startCall", name="greeting", prompt=greeting_prompt)
    qualify = wf.add(
        type="agentNode",
        name="qualify",
        prompt=(
            "Ask what they need help with, then confirm you heard them correctly "
            "before ending the call."
        ),
    )
    done = wf.add(
        type="endCall",
        name="done",
        prompt="Thank the caller and say goodbye.",
    )
    wf.edge(
        greeting,
        qualify,
        label="interested",
        condition="Caller wants to continue the conversation.",
    )
    wf.edge(
        greeting,
        done,
        label="declined",
        condition="Caller does not want to continue.",
    )
    wf.edge(
        qualify,
        done,
        label="done",
        condition="The caller's request has been confirmed.",
    )
    return wf


def main() -> int:
    api_endpoint = os.environ.get("DOGRAH_API_ENDPOINT", "http://localhost:8000")
    api_token = os.environ.get("DOGRAH_API_TOKEN")
    if not api_token:
        print("DOGRAH_API_TOKEN is required", file=sys.stderr)
        return 1

    with DograhClient(base_url=api_endpoint, api_key=api_token) as client:
        created = client.create_workflow(
            body=CreateWorkflowRequest(
                name=WORKFLOW_NAME,
                workflow_definition=build_agent(
                    client, greeting_prompt=GREETING_PROMPT
                ).to_json(),
            )
        )
        print(f"Created workflow {created.id}: {created.name!r} (status={created.status})")

        saved = client.save_workflow(
            workflow_id=created.id,
            workflow=build_agent(client, greeting_prompt=GREETING_PROMPT_DRAFT),
        )
        print(
            f"Saved draft {saved.id}: version={saved.version_number} "
            f"status={saved.version_status}"
        )

        try:
            validated = client.validate_workflow(created.id)
        except ApiError as exc:
            print(f"Validation failed: {exc}", file=sys.stderr)
            return 1
        print(f"Validated workflow {created.id}: is_valid={validated.is_valid}")

        published = client.publish_workflow(created.id)
        print(
            f"Published workflow {created.id}: version={published.version_number} "
            f"status={published.status}"
        )

        runs = client.list_workflow_runs(created.id, limit=5)
        print(f"Workflow runs so far: {runs.total_count}")
        # Fetch one run with client.get_workflow_run(created.id, run_id)
        # once a call or campaign has produced a run id.

        if not CAMPAIGN_SOURCE_ID:
            print(
                "No CAMPAIGN_SOURCE_ID set. Upload a CSV in Dograh, paste the "
                "source id at the top of this file, and re-run to call "
                "create_campaign and start_campaign. pause_campaign, "
                "resume_campaign, and get_campaign_progress take the same id. "
                "Starting a campaign also requires a telephony configuration "
                "on the organization."
            )
            return 0

        campaign = client.create_campaign(
            body=CreateCampaignRequest(
                name=CAMPAIGN_NAME,
                workflow_id=created.id,
                source_type="csv",
                source_id=CAMPAIGN_SOURCE_ID,
            )
        )
        print(f"Created campaign {campaign.id}: state={campaign.state}")

        started = client.start_campaign(campaign.id)
        progress = client.get_campaign_progress(started.id)
        print(
            f"Started campaign {started.id}: state={progress.state} "
            f"processed={progress.processed_rows}/{progress.total_rows}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
