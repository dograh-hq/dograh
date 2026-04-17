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
    OrganizationModel,
    OrganizationUsageCycleModel,
    UserConfigurationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.schemas.user_configuration import UserConfiguration


class OrganizationUsageClient(BaseDBClient):
    """Client for managing organization usage and quota operations."""

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
        # Get organization to determine quota type
        org_result = await session.execute(
            select(OrganizationModel).where(OrganizationModel.id == organization_id)
        )
        org = org_result.scalar_one()

        # Calculate current period
        period_start, period_end = self._calculate_current_period(org)

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
            quota_dograh_tokens=org.quota_dograh_tokens,
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

    async def check_and_reserve_quota(
        self, organization_id: int, estimated_tokens: int = 0
    ) -> bool:
        """
        Check if organization has sufficient quota and optionally reserve tokens.
        Returns True if quota is available, False otherwise.

        This method is fully atomic and safe for concurrent access from multiple processes.
        """
        async with self.async_session() as session:
            # Get organization
            org_result = await session.execute(
                select(OrganizationModel).where(OrganizationModel.id == organization_id)
            )
            org = org_result.scalar_one_or_none()

            if not org or not org.quota_enabled:
                # No quota enforcement if not enabled
                return True

            # Get or create current cycle within the same session/transaction
            cycle = await self._get_or_create_current_cycle_impl(
                organization_id, session, commit=False
            )

            # Atomic check and update with row-level lock
            result = await session.execute(
                select(OrganizationUsageCycleModel)
                .where(
                    and_(
                        OrganizationUsageCycleModel.id == cycle.id,
                        OrganizationUsageCycleModel.used_dograh_tokens
                        + estimated_tokens
                        <= OrganizationUsageCycleModel.quota_dograh_tokens,
                    )
                )
                .with_for_update(skip_locked=False)
            )

            cycle_locked = result.scalar_one_or_none()
            if cycle_locked:
                # Update the usage atomically
                cycle_locked.used_dograh_tokens += estimated_tokens
                await session.commit()
                return True

            return False

    async def update_usage_after_run(
        self,
        organization_id: int,
        actual_tokens: int,
        duration_seconds: int = 0,
        charge_usd: float = None,
    ) -> None:
        """Update usage after a workflow run completes with actual token count and duration.

        This method is fully atomic and safe for concurrent access from multiple processes.
        """
        async with self.async_session() as session:
            # Get or create current cycle within the same session/transaction
            cycle = await self._get_or_create_current_cycle_impl(
                organization_id, session, commit=False
            )

            # Acquire a row-level lock for atomic update
            result = await session.execute(
                select(OrganizationUsageCycleModel)
                .where(OrganizationUsageCycleModel.id == cycle.id)
                .with_for_update(skip_locked=False)
            )
            cycle_locked = result.scalar_one()

            # Update usage atomically
            cycle_locked.used_dograh_tokens += actual_tokens
            cycle_locked.total_duration_seconds += int(round(duration_seconds))

            # Update USD amount if provided
            if charge_usd is not None:
                if cycle_locked.used_amount_usd is None:
                    cycle_locked.used_amount_usd = 0
                cycle_locked.used_amount_usd += charge_usd

            await session.commit()

    async def get_current_usage(self, organization_id: int) -> dict:
        """Get current period usage information."""
        async with self.async_session() as session:
            # Get organization
            org_result = await session.execute(
                select(OrganizationModel).where(OrganizationModel.id == organization_id)
            )
            org = org_result.scalar_one()

            # Get or create current cycle within the same session
            cycle = await self._get_or_create_current_cycle_impl(
                organization_id, session, commit=False
            )

            # Calculate next refresh date
            if org.quota_type == "monthly":
                next_refresh = cycle.period_end + relativedelta(days=1)
            else:  # annual
                next_refresh = cycle.period_end + relativedelta(days=1)

            result = {
                "period_start": cycle.period_start.isoformat(),
                "period_end": cycle.period_end.isoformat(),
                "used_dograh_tokens": cycle.used_dograh_tokens,
                "quota_dograh_tokens": cycle.quota_dograh_tokens,
                "percentage_used": (
                    round(
                        (cycle.used_dograh_tokens / cycle.quota_dograh_tokens) * 100, 2
                    )
                    if cycle.quota_dograh_tokens > 0
                    else 0
                ),
                "next_refresh_date": next_refresh.date().isoformat(),
                "quota_enabled": org.quota_enabled,
                "total_duration_seconds": cycle.total_duration_seconds,
            }

            # Add USD fields if organization has pricing
            if org.price_per_second_usd is not None:
                result["used_amount_usd"] = cycle.used_amount_usd or 0
                result["quota_amount_usd"] = cycle.quota_amount_usd
                result["currency"] = "USD"
                result["price_per_second_usd"] = org.price_per_second_usd

                # Calculate percentage based on USD if available
                if cycle.quota_amount_usd and cycle.quota_amount_usd > 0:
                    result["percentage_used"] = round(
                        ((cycle.used_amount_usd or 0) / cycle.quota_amount_usd) * 100, 2
                    )

            return result

    async def get_usage_history(
        self,
        organization_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
        filters: Optional[list[dict]] = None,
    ) -> tuple[list[dict], int]:
        """Get paginated workflow runs with usage for an organization."""
        async with self.async_session() as session:
            query = (
                select(WorkflowRunModel)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .join(UserModel, WorkflowModel.user_id == UserModel.id)
                .where(
                    UserModel.selected_organization_id == organization_id,
                    WorkflowRunModel.cost_info.isnot(None),
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
            allowed_filters = {"duration", "dispositionCode", "phoneNumber"}
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
                if run.cost_info:
                    # Try to get dograh_token_usage first (new format)
                    dograh_tokens = run.cost_info.get("dograh_token_usage", 0)
                    # If not present, calculate from total_cost_usd (old format)
                    if dograh_tokens == 0 and "total_cost_usd" in run.cost_info:
                        dograh_tokens = round(
                            float(run.cost_info["total_cost_usd"]) * 100, 2
                        )
                    # Get call duration
                    call_duration = run.cost_info.get("call_duration_seconds", 0)
                else:
                    dograh_tokens = 0
                    call_duration = 0
                total_tokens += dograh_tokens
                total_duration_seconds += int(round(call_duration))

                # Extract phone number from initial_context based on call_type.
                # Inbound runs only have caller_number/called_number; the
                # caller_number is the customer. Outbound runs use the
                # phone_number key written by the dispatchers.
                ic = run.initial_context or {}
                if run.call_type == "inbound":
                    phone_number = ic.get("caller_number")
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
                    "phone_number": phone_number,
                    "call_type": run.call_type,
                    "disposition": disposition,
                    "initial_context": run.initial_context,
                    "gathered_context": run.gathered_context,
                }

                # Add USD cost if available in cost_info
                if run.cost_info and "charge_usd" in run.cost_info:
                    run_data["charge_usd"] = run.cost_info["charge_usd"]

                formatted_runs.append(run_data)

            return formatted_runs, total_count, total_tokens, total_duration_seconds

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
            # Get user timezone if user_id is provided
            user_timezone = "UTC"  # Default timezone
            if user_id:
                config_result = await session.execute(
                    select(UserConfigurationModel).where(
                        UserConfigurationModel.user_id == user_id
                    )
                )
                config_obj = config_result.scalar_one_or_none()
                if config_obj and config_obj.configuration:
                    user_config = UserConfiguration.model_validate(
                        config_obj.configuration
                    )
                    if user_config.timezone:
                        user_timezone = user_config.timezone

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
                        WorkflowRunModel.cost_info["call_duration_seconds"].as_float()
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

    async def update_organization_quota(
        self,
        organization_id: int,
        quota_type: str,
        quota_dograh_tokens: int,
        quota_reset_day: Optional[int] = None,
        quota_start_date: Optional[datetime] = None,
    ) -> OrganizationModel:
        """Update organization quota settings."""
        async with self.async_session() as session:
            result = await session.execute(
                select(OrganizationModel).where(OrganizationModel.id == organization_id)
            )
            org = result.scalar_one()

            org.quota_type = quota_type
            org.quota_dograh_tokens = quota_dograh_tokens
            org.quota_enabled = True

            if quota_type == "monthly" and quota_reset_day:
                org.quota_reset_day = quota_reset_day
            elif quota_type == "annual" and quota_start_date:
                org.quota_start_date = quota_start_date

            await session.commit()
            await session.refresh(org)
            return org

    def _calculate_current_period(
        self, org: OrganizationModel
    ) -> tuple[datetime, datetime]:
        """Calculate the current billing period based on organization settings."""
        now = datetime.now(timezone.utc)

        if org.quota_type == "monthly":
            # Find the start of the current billing month
            reset_day = org.quota_reset_day

            # Handle month boundaries
            if now.day >= reset_day:
                period_start = now.replace(
                    day=reset_day, hour=0, minute=0, second=0, microsecond=0
                )
            else:
                # Previous month
                period_start = (now - relativedelta(months=1)).replace(
                    day=reset_day, hour=0, minute=0, second=0, microsecond=0
                )

            # End is one month later minus 1 second
            period_end = (
                period_start + relativedelta(months=1) - relativedelta(seconds=1)
            )

        else:  # annual
            if not org.quota_start_date:
                # Default to calendar year
                period_start = now.replace(
                    month=1, day=1, hour=0, minute=0, second=0, microsecond=0
                )
                period_end = (
                    period_start + relativedelta(years=1) - relativedelta(seconds=1)
                )
            else:
                # Find current annual period
                start_date = org.quota_start_date.replace(tzinfo=timezone.utc)
                years_diff = now.year - start_date.year

                # Adjust for whether we've passed the anniversary
                if now.month < start_date.month or (
                    now.month == start_date.month and now.day < start_date.day
                ):
                    years_diff -= 1

                period_start = start_date + relativedelta(years=years_diff)
                period_end = (
                    period_start + relativedelta(years=1) - relativedelta(seconds=1)
                )

        return period_start, period_end
