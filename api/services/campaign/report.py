import csv
import io
from datetime import datetime
from typing import Any, List, Optional

from api.constants import BACKEND_API_ENDPOINT
from api.db import db_client
from api.utils.transcript import generate_transcript_text


def _transcript_from_logs(logs: dict | None) -> str:
    """Extract transcript text from workflow run logs JSON."""
    if not logs:
        return ""
    events = logs.get("realtime_feedback_events", [])
    return generate_transcript_text(events).strip()


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


async def generate_campaign_report_csv(
    campaign_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> tuple[io.StringIO, str]:
    """Generate a CSV report for a campaign.

    Returns a tuple of (csv_output, filename).
    """
    runs = await db_client.get_completed_runs_for_report(
        campaign_id, start_date=start_date, end_date=end_date
    )

    # Collect dynamic extracted variable columns
    extracted_var_keys = _collect_extracted_variable_keys(runs)

    output = io.StringIO()
    writer = csv.writer(output)

    pre_headers = [
        "Run ID",
        "Created At",
        "Phone Number",
        "Call Disposition",
        "Call Duration (s)",
    ]
    post_headers = [
        "Call Tags",
        "Transcript",
        "Recording URL",
    ]
    writer.writerow(pre_headers + extracted_var_keys + post_headers)

    for run in runs:
        initial = run.initial_context or {}
        gathered = run.gathered_context or {}
        cost = run.cost_info or {}

        recording_url = ""
        if run.public_access_token:
            recording_url = (
                f"{BACKEND_API_ENDPOINT}/api/v1/public/download/workflow"
                f"/{run.public_access_token}/recording"
            )

        call_tags = gathered.get("call_tags", [])
        if isinstance(call_tags, list):
            call_tags = ", ".join(str(t) for t in call_tags)

        pre_values = [
            run.id,
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
            _transcript_from_logs(run.logs),
            recording_url,
        ]

        writer.writerow(pre_values + extracted_values + post_values)

    output.seek(0)
    filename = f"campaign_{campaign_id}_report.csv"
    return output, filename
