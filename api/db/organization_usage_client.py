from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from sqlalchemy import Date, and_, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import joinedload

from api.db.base_client import BaseDBClient
from api.db.filters import apply_workflow_run_filters
from api.db.models import (
    OrganizationConfigurationModel,
    OrganizationModel,
    OrganizationUsageCycleModel,
    UserConfigurationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import OrganizationConfigurationKey
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.utils.recording_artifacts import get_recording_storage_key


class OrganizationUsageClient(BaseDBClient):
    """Client for managing organization usage reporting aggregates."""

    async def get_or_create_current_cycle(
        self, organization_id: int, session=None
    ) -> OrganizationUsageCycleModel:
        """Get or create the current usage cycle for an organization.

        Args:
            organization_id: The organization ID
            session: Optional session to use for the operation. If provided,
                    the caller is responsible for committing.
        """
        if session is None:
            async with self.async_session() as session:
                return await self._get_or_create_current_cycle_impl(
                    organization_id, session, commit=True
                )
        else:
            return await self._get_or_create_current_cycle_impl(
                organization_id, session, commit=False
            )

    async def _get_or_create_current_cycle_impl(
        self, organization_id: int, session, commit: bool
    ) -> OrganizationUsageCycleModel:
        """Internal implementation for get_or_create_current_cycle."""
        period_start, period_end = self._calculate_current_period()

        # Try to get existing cycle
        cycle_result = await session.execute(
            select(OrganizationUsageCycleModel).where(
                and_(
                    OrganizationUsageCycleModel.organization_id == organization_id,
                    OrganizationUsageCycleModel.period_start == period_start,
                    OrganizationUsageCycleModel.period_end == period_end,
                )
            )
        )
        cycle = cycle_result.scalar_one_or_none()

        if cycle:
            return cycle

        # Create new cycle if it doesn't exist
        stmt = insert(OrganizationUsageCycleModel).values(
            organization_id=organization_id,
            period_start=period_start,
            period_end=period_end,
            # Deprecated non-null column retained for historical schema compatibility.
            quota_dograh_tokens=0,
        )
        # Handle concurrent inserts gracefully
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["organization_id", "period_start", "period_end"]
        )

        await session.execute(stmt)

        if commit:
            await session.commit()

        # Fetch the created cycle
        cycle_result = await session.execute(
            select(OrganizationUsageCycleModel).where(
                and_(
                    OrganizationUsageCycleModel.organization_id == organization_id,
                    OrganizationUsageCycleModel.period_start == period_start,
                    OrganizationUsageCycleModel.period_end == period_end,
                )
            )
        )
        return cycle_result.scalar_one()

    async def get_current_usage(self, organization_id: int) -> dict:
        """Get current reporting-period usage information."""
        async with self.async_session() as session:
            org_result = await session.execute(
                select(OrganizationModel).where(OrganizationModel.id == organization_id)
            )
            org = org_result.scalar_one()

            # Get or create current cycle within the same session
            cycle = await self._get_or_create_current_cycle_impl(
                organization_id, session, commit=False
            )

            result = {
                "period_start": cycle.period_start.isoformat(),
                "period_end": cycle.period_end.isoformat(),
                "used_dograh_tokens": cycle.used_dograh_tokens,
                "total_duration_seconds": cycle.total_duration_seconds,
            }

            # Add USD fields if organization has pricing
            if org.price_per_second_usd is not None:
                result["used_amount_usd"] = cycle.used_amount_usd or 0
                result["currency"] = "USD"
                result["price_per_second_usd"] = org.price_per_second_usd

            return result

    async def get_usage_history(
        self,
        organization_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
        filters: Optional[list[dict]] = None,
    ) -> tuple[list[dict], int, float, int]:
        """Get paginated workflow runs with usage for an organization."""
        async with self.async_session() as session:
            query = (
                select(WorkflowRunModel)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .join(UserModel, WorkflowModel.user_id == UserModel.id)
                .where(
                    UserModel.selected_organization_id == organization_id,
                    WorkflowRunModel.usage_info.isnot(None),
                )
                .order_by(WorkflowRunModel.created_at.desc())
            )

            # Apply date filters if provided
            if start_date:
                query = query.where(WorkflowRunModel.created_at >= start_date)
            if end_date:
                query = query.where(WorkflowRunModel.created_at <= end_date)

            # Only allow specific filters for usage history endpoint
            # This ensures security and prevents unexpected filter attributes
            allowed_filters = {
                "duration",
                "dispositionCode",
                "callerNumber",
                "calledNumber",
                "runId",
                "workflowId",
                "campaignId",
            }
            sanitized_filters = []

            if filters:
                for filter_item in filters:
                    attribute = filter_item.get("attribute")

                    # Only process allowed filters
                    if attribute in allowed_filters:
                        sanitized_filters.append(filter_item)

            # Apply filters using the common filter function
            query = apply_workflow_run_filters(query, sanitized_filters)

            # Get total count
            count_result = await session.execute(
                select(func.count()).select_from(query.subquery())
            )
            total_count = count_result.scalar()

            results = await session.execute(
                query.options(joinedload(WorkflowRunModel.workflow))
                .limit(limit)
                .offset(offset)
            )
            runs = results.scalars().all()

            # Format runs
            formatted_runs = []
            total_tokens = 0
            total_duration_seconds = 0
            for run in runs:
                dograh_tokens = 0
                call_duration = (run.usage_info or {}).get("call_duration_seconds", 0)
                total_tokens += dograh_tokens
                total_duration_seconds += int(round(call_duration))

                ic = run.initial_context or {}
                caller_number = ic.get("caller_number")
                called_number = ic.get("called_number") or ic.get("phone_number")
                # DEPRECATED: phone_number — use caller_number/called_number.
                # Inbound runs only have caller_number/called_number; the
                # caller_number is the customer. Outbound runs use the
                # phone_number key written by the dispatchers.
                if run.call_type == "inbound":
                    phone_number = caller_number
                else:
                    phone_number = ic.get("phone_number")

                # Extract disposition from gathered_context
                disposition = None
                if run.gathered_context:
                    disposition = run.gathered_context.get("mapped_call_disposition")

                run_data = {
                    "id": run.id,
                    "workflow_id": run.workflow_id,
                    "workflow_name": run.workflow.name if run.workflow else None,
                    "name": run.name,
                    "created_at": run.created_at.isoformat(),
                    "dograh_token_usage": dograh_tokens,
                    "call_duration_seconds": int(round(call_duration)),
                    "recording_url": run.recording_url,
                    "transcript_url": run.transcript_url,
                    "user_recording_url": get_recording_storage_key(run.extra, "user"),
                    "bot_recording_url": get_recording_storage_key(run.extra, "bot"),
                    "extra": run.extra,
                    "public_access_token": run.public_access_token,
                    "phone_number": phone_number,
                    "caller_number": caller_number,
                    "called_number": called_number,
                    "call_type": run.call_type,
                    "mode": run.mode,
                    "disposition": disposition,
                    "initial_context": run.initial_context,
                    "gathered_context": run.gathered_context,
                }

                # Add USD cost if available in cost_info
                if run.cost_info and "charge_usd" in run.cost_info:
                    run_data["charge_usd"] = run.cost_info["charge_usd"]

                formatted_runs.append(run_data)

            return formatted_runs, total_count, total_tokens, total_duration_seconds

    async def get_usage_runs_for_report(
        self,
        organization_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        filters: Optional[list[dict]] = None,
    ) -> list:
        """Get filtered runs for an organization-scoped usage CSV report.

        Mirrors the filter allowlist used by `get_usage_history`, but selects
        only the columns needed by `build_run_report_csv` and returns every
        matching run (no pagination).
        """
        async with self.async_session() as session:
            query = (
                select(
                    WorkflowRunModel.id,
                    WorkflowRunModel.workflow_id,
                    WorkflowRunModel.definition_id,
                    WorkflowRunModel.campaign_id,
                    WorkflowRunModel.created_at,
                    WorkflowRunModel.initial_context,
                    WorkflowRunModel.gathered_context,
                    WorkflowRunModel.cost_info,
                    WorkflowRunModel.usage_info,
                    WorkflowRunModel.public_access_token,
                )
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .join(UserModel, WorkflowModel.user_id == UserModel.id)
                .where(
                    UserModel.selected_organization_id == organization_id,
                    WorkflowRunModel.usage_info.isnot(None),
                )
                .order_by(WorkflowRunModel.created_at.desc())
            )

            if start_date:
                query = query.where(WorkflowRunModel.created_at >= start_date)
            if end_date:
                query = query.where(WorkflowRunModel.created_at <= end_date)

            allowed_filters = {
                "duration",
                "dispositionCode",
                "callerNumber",
                "calledNumber",
                "runId",
                "workflowId",
                "campaignId",
            }
            sanitized_filters = []
            if filters:
                for filter_item in filters:
                    if filter_item.get("attribute") in allowed_filters:
                        sanitized_filters.append(filter_item)

            query = apply_workflow_run_filters(query, sanitized_filters)

            result = await session.execute(query)
            return list(result.all())

    async def get_daily_usage_breakdown(
        self,
        organization_id: int,
        start_date: datetime,
        end_date: datetime,
        price_per_second_usd: float,
        user_id: Optional[int] = None,
    ) -> dict:
        """Get daily usage breakdown for an organization with pricing."""

        async with self.async_session() as session:
            # Get org timezone preference first, then fall back to legacy user config.
            user_timezone = "UTC"  # Default timezone
            pref_result = await session.execute(
                select(OrganizationConfigurationModel).where(
                    OrganizationConfigurationModel.organization_id == organization_id,
                    OrganizationConfigurationModel.key.in_(
                        [
                            OrganizationConfigurationKey.ORGANIZATION_PREFERENCES.value,
                            OrganizationConfigurationKey.MODEL_CONFIGURATION_PREFERENCES.value,
                        ]
                    ),
                )
            )
            pref_rows = pref_result.scalars().all()
            pref_by_key = {pref.key: pref for pref in pref_rows}
            pref_obj = pref_by_key.get(
                OrganizationConfigurationKey.ORGANIZATION_PREFERENCES.value
            ) or pref_by_key.get(
                OrganizationConfigurationKey.MODEL_CONFIGURATION_PREFERENCES.value
            )
            if pref_obj and pref_obj.value:
                user_timezone = pref_obj.value.get("timezone") or user_timezone

            if user_id:
                config_result = await session.execute(
                    select(UserConfigurationModel).where(
                        UserConfigurationModel.user_id == user_id
                    )
                )
                config_obj = config_result.scalar_one_or_none()
                if config_obj and config_obj.configuration:
                    effective_config = EffectiveAIModelConfiguration.model_validate(
                        config_obj.configuration
                    )
                    if effective_config.timezone and user_timezone == "UTC":
                        user_timezone = effective_config.timezone

            # Validate timezone string
            try:
                # Test if timezone is valid
                ZoneInfo(user_timezone)
            except Exception:
                # Fallback to UTC if timezone is invalid
                user_timezone = "UTC"
            # Query to get daily aggregates
            # Use AT TIME ZONE to convert to user's timezone before grouping by date
            date_expr = cast(
                func.timezone(user_timezone, WorkflowRunModel.created_at), Date
            )

            daily_usage = await session.execute(
                select(
                    date_expr.label("date"),
                    func.sum(
                        WorkflowRunModel.usage_info["call_duration_seconds"].as_float()
                    ).label("total_seconds"),
                    func.count(WorkflowRunModel.id).label("call_count"),
                )
                .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
                .join(UserModel, UserModel.id == WorkflowModel.user_id)
                .where(
                    UserModel.selected_organization_id == organization_id,
                    WorkflowRunModel.created_at >= start_date,
                    WorkflowRunModel.created_at <= end_date,
                    WorkflowRunModel.is_completed == True,
                )
                .group_by(date_expr)
                .order_by(date_expr.desc())
            )

            breakdown = []
            total_minutes = 0
            total_cost_usd = 0
            total_dograh_tokens = 0

            for row in daily_usage:
                seconds = row.total_seconds or 0
                minutes = seconds / 60
                cost_usd = seconds * price_per_second_usd
                dograh_tokens = cost_usd * 100  # 1 cent = 1 token

                total_minutes += minutes
                total_cost_usd += cost_usd
                total_dograh_tokens += dograh_tokens

                breakdown.append(
                    {
                        "date": row.date.isoformat(),
                        "minutes": round(minutes, 1),
                        "cost_usd": round(cost_usd, 2),
                        "dograh_tokens": round(dograh_tokens, 0),
                        "call_count": row.call_count,
                    }
                )

            return {
                "breakdown": breakdown,
                "total_minutes": round(total_minutes, 1),
                "total_cost_usd": round(total_cost_usd, 2),
                "total_dograh_tokens": round(total_dograh_tokens, 0),
                "currency": "USD",
            }

    def _calculate_current_period(self) -> tuple[datetime, datetime]:
        """Calculate the current calendar-month reporting period."""
        now = datetime.now(timezone.utc)

        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_end = period_start + relativedelta(months=1) - relativedelta(seconds=1)

        return period_start, period_end
