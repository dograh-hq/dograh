import csv
import io
from datetime import datetime
from typing import Any, List, Optional

from api.constants import BACKEND_API_ENDPOINT
from api.db import db_client


def _artifact_url(token: str | None, artifact: str) -> str:
    if not token:
        return ""
    return f"{BACKEND_API_ENDPOINT}/api/v1/public/download/workflow/{token}/{artifact}"


def _collect_extracted_variable_keys(runs: List[Any]) -> list[str]:
    """Collect all unique extracted variable keys across runs, preserving insertion order."""
    keys: dict[str, None] = {}
    for run in runs:
        gathered = run.gathered_context or {}
        extracted = gathered.get("extracted_variables", {})
        if isinstance(extracted, dict):
            for key in extracted:
                keys.setdefault(key, None)
    return list(keys)


def _build_run_report_csv(runs: List[Any]) -> io.StringIO:
    """Build a CSV from completed workflow runs.

    Shared between campaign-scoped and workflow-scoped reports.
    """
    extracted_var_keys = _collect_extracted_variable_keys(runs)

    output = io.StringIO()
    writer = csv.writer(output)

    pre_headers = [
        "Run ID",
        "Campaign ID",
        "Agent ID",
        "Agent Definition ID",
        "Created At",
        "Phone Number",
        "Call Disposition",
        "Call Duration (s)",
    ]
    post_headers = [
        "Call Tags",
        "Transcript URL",
        "Recording URL",
    ]
    writer.writerow(pre_headers + extracted_var_keys + post_headers)

    for run in runs:
        initial = run.initial_context or {}
        gathered = run.gathered_context or {}
        cost = run.cost_info or {}

        call_tags = gathered.get("call_tags", [])
        if isinstance(call_tags, list):
            call_tags = ", ".join(str(t) for t in call_tags)

        pre_values = [
            run.id,
            run.campaign_id if run.campaign_id is not None else "",
            run.workflow_id,
            run.definition_id if run.definition_id is not None else "",
            run.created_at.isoformat() if run.created_at else "",
            initial.get("phone_number", ""),
            gathered.get("mapped_call_disposition", ""),
            cost.get("call_duration_seconds", ""),
        ]

        extracted = gathered.get("extracted_variables", {})
        if not isinstance(extracted, dict):
            extracted = {}
        extracted_values = [extracted.get(key, "") for key in extracted_var_keys]

        post_values = [
            call_tags,
            _artifact_url(run.public_access_token, "transcript"),
            _artifact_url(run.public_access_token, "recording"),
        ]

        writer.writerow(pre_values + extracted_values + post_values)

    output.seek(0)
    return output


async def generate_campaign_report_csv(
    campaign_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> tuple[io.StringIO, str]:
    """Generate a CSV report for a campaign."""
    runs = await db_client.get_completed_runs_for_report(
        campaign_id=campaign_id, start_date=start_date, end_date=end_date
    )
    return _build_run_report_csv(runs), f"campaign_{campaign_id}_report.csv"


async def generate_workflow_report_csv(
    workflow_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> tuple[io.StringIO, str]:
    """Generate a CSV report for all completed runs of a workflow."""
    runs = await db_client.get_completed_runs_for_report(
        workflow_id=workflow_id, start_date=start_date, end_date=end_date
    )
    return _build_run_report_csv(runs), f"workflow_{workflow_id}_report.csv"
