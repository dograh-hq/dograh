"""CSV reports built from completed workflow runs.

Shared by campaign-, workflow-, and organization-usage-scoped reports.
The DB client supplies the row set; this module owns the column layout
so every endpoint emits the same shape.
"""

import csv
import io
from datetime import UTC, datetime
from typing import Any, List, Optional

from api.db import db_client
from api.utils.artifacts import artifact_url


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


async def _refresh_public_tokens(runs: List[Any]) -> None:
    """Rotate/mint a valid public token for each run that has a downloadable
    artifact, so the report's URLs point at fresh (unexpired) tokens rather than
    stale or missing ones. Runs with no recording/transcript are skipped so we
    don't mint public tokens for runs with nothing to expose.
    """
    for run in runs:
        if run.recording_url or run.transcript_url:
            run.public_access_token = await db_client.ensure_public_access_token(run.id)


def build_run_report_csv(runs: List[Any]) -> io.StringIO:
    """Build a CSV from completed workflow runs.

    Callers should ``await _refresh_public_tokens(runs)`` first so the emitted
    artifact URLs carry current (unexpired) tokens.
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
        usage = run.usage_info or {}

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
            usage.get("call_duration_seconds", ""),
        ]

        extracted = gathered.get("extracted_variables", {})
        if not isinstance(extracted, dict):
            extracted = {}
        extracted_values = [extracted.get(key, "") for key in extracted_var_keys]

        post_values = [
            call_tags,
            artifact_url(run.public_access_token, "transcript") or "",
            artifact_url(run.public_access_token, "recording") or "",
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
    await _refresh_public_tokens(runs)
    return build_run_report_csv(runs), f"campaign_{campaign_id}_report.csv"


async def generate_workflow_report_csv(
    workflow_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> tuple[io.StringIO, str]:
    """Generate a CSV report for all completed runs of a workflow."""
    runs = await db_client.get_completed_runs_for_report(
        workflow_id=workflow_id, start_date=start_date, end_date=end_date
    )
    await _refresh_public_tokens(runs)
    return build_run_report_csv(runs), f"workflow_{workflow_id}_report.csv"


async def generate_usage_runs_report_csv(
    organization_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    filters: Optional[list[dict]] = None,
) -> tuple[io.StringIO, str]:
    """Generate a CSV report for runs visible on the org-wide usage page.

    Honors the same date / filter inputs as the `/usage/runs` listing.
    """
    runs = await db_client.get_usage_runs_for_report(
        organization_id,
        start_date=start_date,
        end_date=end_date,
        filters=filters,
    )
    await _refresh_public_tokens(runs)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return build_run_report_csv(runs), f"usage_runs_{timestamp}.csv"
