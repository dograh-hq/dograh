from datetime import datetime, time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from api.db import db_client


class DailyReportService:
    async def get_daily_report(
        self,
        organization_id: int,
        date: str,
        timezone: str,
        workflow_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Get daily report for a specific date and timezone.

        Args:
            organization_id: The organization ID to filter by
            date: Date in YYYY-MM-DD format
            timezone: IANA timezone string (e.g., "America/New_York")
            workflow_id: Optional workflow ID to filter by (None means all workflows)
        """
        # Parse date and timezone
        tz = ZoneInfo(timezone)
        date_obj = datetime.strptime(date, "%Y-%m-%d")

        # Create start and end datetime in the specified timezone
        start_dt = datetime.combine(date_obj, time.min, tzinfo=tz)
        end_dt = datetime.combine(date_obj, time.max, tzinfo=tz)

        # Convert to UTC for database queries
        start_utc = start_dt.astimezone(ZoneInfo("UTC"))
        end_utc = end_dt.astimezone(ZoneInfo("UTC"))

        # Get workflow runs from database (optimized - only required fields)
        runs = await db_client.get_workflow_runs_for_daily_report(
            organization_id=organization_id,
            start_utc=start_utc,
            end_utc=end_utc,
            workflow_id=workflow_id,
        )

        # Calculate metrics
        total_runs = len(runs)
        xfer_count = sum(
            1
            for run in runs
            if run["gathered_context"]
            and run["gathered_context"].get("mapped_call_disposition") == "XFER"
        )

        # Calculate disposition distribution
        disposition_counts = {}
        for run in runs:
            if run["gathered_context"]:
                disposition = run["gathered_context"].get(
                    "mapped_call_disposition", "UNKNOWN"
                )
                disposition_counts[disposition] = (
                    disposition_counts.get(disposition, 0) + 1
                )

        # Sort dispositions by count and get top 5
        sorted_dispositions = sorted(
            disposition_counts.items(), key=lambda x: x[1], reverse=True
        )

        disposition_distribution = []
        other_count = 0

        for i, (disposition, count) in enumerate(sorted_dispositions):
            if i < 5:
                disposition_distribution.append(
                    {
                        "disposition": disposition,
                        "count": count,
                        "percentage": round(
                            (count / total_runs * 100) if total_runs > 0 else 0, 2
                        ),
                    }
                )
            else:
                other_count += count

        # Add "Other" category if there are more than 5 dispositions
        if other_count > 0:
            disposition_distribution.append(
                {
                    "disposition": "Other",
                    "count": other_count,
                    "percentage": round(
                        (other_count / total_runs * 100) if total_runs > 0 else 0, 2
                    ),
                }
            )

        # Calculate call duration distribution
        duration_buckets = {
            "0-10": {"range_start": 0, "range_end": 10, "count": 0},
            "10-30": {"range_start": 10, "range_end": 30, "count": 0},
            "30-60": {"range_start": 30, "range_end": 60, "count": 0},
            "60-120": {"range_start": 60, "range_end": 120, "count": 0},
            "120-180": {"range_start": 120, "range_end": 180, "count": 0},
            ">180": {"range_start": 180, "range_end": None, "count": 0},
        }

        for run in runs:
            if run["usage_info"]:
                duration_str = run["usage_info"].get("call_duration_seconds")
                if duration_str:
                    try:
                        duration = float(duration_str)
                        if duration < 10:
                            duration_buckets["0-10"]["count"] += 1
                        elif duration < 30:
                            duration_buckets["10-30"]["count"] += 1
                        elif duration < 60:
                            duration_buckets["30-60"]["count"] += 1
                        elif duration < 120:
                            duration_buckets["60-120"]["count"] += 1
                        elif duration < 180:
                            duration_buckets["120-180"]["count"] += 1
                        else:
                            duration_buckets[">180"]["count"] += 1
                    except (ValueError, TypeError):
                        pass

        # Format duration distribution
        call_duration_distribution = []
        total_calls_with_duration = sum(b["count"] for b in duration_buckets.values())

        for bucket_name, bucket_data in duration_buckets.items():
            call_duration_distribution.append(
                {
                    "bucket": bucket_name,
                    "range_start": bucket_data["range_start"],
                    "range_end": bucket_data["range_end"],
                    "count": bucket_data["count"],
                    "percentage": round(
                        (bucket_data["count"] / total_calls_with_duration * 100)
                        if total_calls_with_duration > 0
                        else 0,
                        2,
                    ),
                }
            )

        return {
            "date": date,
            "timezone": timezone,
            "workflow_id": workflow_id,
            "metrics": {"total_runs": total_runs, "xfer_count": xfer_count},
            "disposition_distribution": disposition_distribution,
            "call_duration_distribution": call_duration_distribution,
        }

    async def get_workflows_for_organization(
        self, organization_id: int
    ) -> List[Dict[str, Any]]:
        """
        Get all workflows for an organization.
        """
        workflows = await db_client.get_workflows_for_organization(organization_id)

        return [{"id": workflow.id, "name": workflow.name} for workflow in workflows]

    async def get_agent_leaderboard(
        self, organization_id: int, days: int = 7
    ) -> Dict[str, Any]:
        """Per-agent (workflow) performance over the last N days.

        Aggregates every run in the window by workflow: call count, total &
        average duration, transfer rate (disposition == "XFER"), and last run.
        Reuses the optimized JSON-extraction query from the daily report.
        """
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        days = max(1, min(int(days), 90))
        end_utc = datetime.now(ZoneInfo("UTC"))
        start_utc = end_utc - timedelta(days=days)

        runs = await db_client.get_workflow_runs_for_daily_report(
            organization_id=organization_id,
            start_utc=start_utc,
            end_utc=end_utc,
        )

        agents: Dict[int, Dict[str, Any]] = {}
        for run in runs:
            wid = run["workflow_id"]
            agent = agents.setdefault(
                wid,
                {
                    "workflow_id": wid,
                    "workflow_name": run.get("workflow_name") or f"Agent {wid}",
                    "total_runs": 0,
                    "total_seconds": 0.0,
                    "transfers": 0,
                    "last_run_at": None,
                },
            )
            agent["total_runs"] += 1
            try:
                agent["total_seconds"] += float(
                    run["usage_info"]["call_duration_seconds"]
                )
            except (TypeError, ValueError, KeyError):
                pass
            disposition = (run.get("gathered_context") or {}).get(
                "mapped_call_disposition"
            )
            if disposition == "XFER":
                agent["transfers"] += 1
            created = run.get("created_at")
            if created and (
                agent["last_run_at"] is None or created > agent["last_run_at"]
            ):
                agent["last_run_at"] = created

        leaderboard: List[Dict[str, Any]] = []
        for agent in agents.values():
            n = agent["total_runs"] or 1
            leaderboard.append(
                {
                    "workflow_id": agent["workflow_id"],
                    "workflow_name": agent["workflow_name"],
                    "total_runs": agent["total_runs"],
                    "total_minutes": round(agent["total_seconds"] / 60, 1),
                    "avg_duration_seconds": round(agent["total_seconds"] / n, 1),
                    "transfer_rate_percent": round(agent["transfers"] / n * 100, 1),
                    "last_run_at": (
                        agent["last_run_at"].isoformat()
                        if agent["last_run_at"]
                        else None
                    ),
                }
            )

        leaderboard.sort(key=lambda a: a["total_runs"], reverse=True)
        return {"days": days, "agents": leaderboard}

    async def get_daily_runs_detail(
        self,
        organization_id: int,
        date: str,
        timezone: str,
        workflow_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get detailed workflow runs for CSV export.

        Args:
            organization_id: The organization ID to filter by
            date: Date in YYYY-MM-DD format
            timezone: IANA timezone string (e.g., "America/New_York")
            workflow_id: Optional workflow ID to filter by
        """
        # Parse date and timezone
        tz = ZoneInfo(timezone)
        date_obj = datetime.strptime(date, "%Y-%m-%d")

        # Create start and end datetime in the specified timezone
        start_dt = datetime.combine(date_obj, time.min, tzinfo=tz)
        end_dt = datetime.combine(date_obj, time.max, tzinfo=tz)

        # Convert to UTC for database queries
        start_utc = start_dt.astimezone(ZoneInfo("UTC"))
        end_utc = end_dt.astimezone(ZoneInfo("UTC"))

        # Get workflow runs from database (optimized - only required fields)
        runs = await db_client.get_workflow_runs_for_daily_report(
            organization_id=organization_id,
            start_utc=start_utc,
            end_utc=end_utc,
            workflow_id=workflow_id,
        )

        # Format runs for CSV export
        detailed_runs = []
        for run in runs:
            # Phone number is already extracted at the database level
            # Try customer_phone_number first, then fall back to initial_context
            phone_number = run["gathered_context"].get(
                "customer_phone_number", ""
            ) or run["initial_context"].get("phone_number", "")

            # Disposition is already extracted at the database level
            disposition = run["gathered_context"].get("mapped_call_disposition", "")

            # Duration is already extracted at the database level
            duration_seconds = 0
            duration_str = run["usage_info"].get("call_duration_seconds", "0")
            try:
                duration_seconds = float(duration_str)
            except (ValueError, TypeError):
                duration_seconds = 0

            detailed_runs.append(
                {
                    "phone_number": phone_number,
                    "disposition": disposition,
                    "duration_seconds": duration_seconds,
                    "workflow_id": run["workflow_id"],
                    "run_id": run["id"],
                    "workflow_name": run["workflow_name"],
                    "created_at": run["created_at"].isoformat(),
                }
            )

        return detailed_runs
