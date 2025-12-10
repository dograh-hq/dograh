"""Common filter utilities for database queries."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Integer, and_, cast, func
from sqlalchemy.dialects.postgresql import JSONB

from api.db.models import WorkflowRunModel

# Mapping of attribute names to database fields
ATTRIBUTE_FIELD_MAPPING = {
    "dateRange": "created_at",
    "dispositionCode": "gathered_context.mapped_call_disposition",
    "duration": "usage_info.call_duration_seconds",
    "status": "is_completed",
    "tokenUsage": "cost_info.total_cost_usd",
    "runId": "id",
    "workflowId": "workflow_id",
    "callTags": "gathered_context.call_tags",
    "phoneNumber": "initial_context.phone",
}


def apply_workflow_run_filters(
    base_query,
    filters: Optional[List[Dict[str, Any]]] = None,
):
    """
    Apply filters to a workflow run query.

    Supports filtering by:
    - dateRange: Filter by created_at date range
    - dispositionCode: Filter by gathered_context.mapped_call_disposition
    - duration: Filter by usage_info.call_duration_seconds range
    - status: Filter by is_completed status
    - tokenUsage: Filter by cost_info.total_cost_usd range
    - runId: Filter by workflow run ID (exact match)
    - workflowId: Filter by workflow ID (exact match)
    - callTags: Filter by gathered_context.call_tags (array of strings)
    - phoneNumber: Filter by initial_context.phone (text search)

    Args:
        base_query: The base SQLAlchemy query to apply filters to
        filters: List of filter dictionaries with structure:
            {"attribute": "filterName", "type": "filterType", "value": {...}}

            Where type is one of:
            - "dateRange": Date range filter with {"from": ..., "to": ...}
            - "multiSelect": Multi-select filter with {"codes": [...]}
            - "numberRange": Number range filter with {"min": ..., "max": ...}
            - "number": Exact number filter with {"value": number}
            - "text": Text search filter with {"value": string}
            - "radio": Radio/status filter with {"status": ...}
            - "tags": Tags filter with {"codes": [...]}

    Returns:
        The query with filters applied
    """

    if not filters:
        return base_query

    filter_conditions = []

    for filter_item in filters:
        attribute = filter_item.get("attribute")
        filter_type = filter_item.get("type")
        value = filter_item.get("value", {})

        # Resolve field from attribute mapping
        field = ATTRIBUTE_FIELD_MAPPING.get(attribute)
        if not field:
            # Skip unknown attributes
            continue

        # Apply the filter based on provided type
        if field and filter_type:
            if filter_type == "number" and field == "id":
                # Filter by exact workflow run ID
                if value.get("value") is not None:
                    filter_conditions.append(WorkflowRunModel.id == value["value"])

            elif filter_type == "number" and field == "workflow_id":
                # Filter by exact workflow ID
                if value.get("value") is not None:
                    filter_conditions.append(
                        WorkflowRunModel.workflow_id == value["value"]
                    )

            elif filter_type == "dateRange" and field == "created_at":
                # Same as attribute-based dateRange
                if value.get("from"):
                    filter_conditions.append(
                        WorkflowRunModel.created_at
                        >= datetime.fromisoformat(value["from"])
                    )
                if value.get("to"):
                    filter_conditions.append(
                        WorkflowRunModel.created_at
                        <= datetime.fromisoformat(value["to"])
                    )

            elif (
                filter_type == "multiSelect"
                and field == "gathered_context.mapped_call_disposition"
            ):
                codes = value.get("codes", [])
                if codes:
                    filter_conditions.append(
                        cast(WorkflowRunModel.gathered_context, JSONB)[
                            "mapped_call_disposition"
                        ]
                        .as_string()
                        .in_(codes)
                    )

            elif filter_type == "radio" and field == "is_completed":
                status = value.get("status")
                if status == "completed":
                    filter_conditions.append(WorkflowRunModel.is_completed == True)
                elif status == "in_progress":
                    filter_conditions.append(WorkflowRunModel.is_completed == False)

            elif (
                filter_type in ("tags", "multiSelect")
                and field == "gathered_context.call_tags"
            ):
                tags = value.get("codes", [])
                if tags:
                    # The gathered_context column is JSON type (not JSONB)
                    # JSON type doesn't support subscripting, so we must cast to JSONB first
                    # Then extract call_tags and check containment with @>
                    gathered_context_jsonb = cast(
                        WorkflowRunModel.gathered_context, JSONB
                    )
                    # Use -> operator with literal text key to get call_tags as JSONB
                    call_tags = gathered_context_jsonb.op("->")("call_tags")
                    filter_conditions.append(call_tags.op("@>")(func.cast(tags, JSONB)))

            elif filter_type == "text" and field == "initial_context.phone":
                # Filter by phone number (contains search)
                phone = value.get("value", "").strip()
                if phone:
                    filter_conditions.append(
                        cast(WorkflowRunModel.initial_context, JSONB)["phone"]
                        .as_string()
                        .contains(phone)
                    )

            elif filter_type == "numberRange":
                min_val = value.get("min")
                max_val = value.get("max")

                if field == "usage_info.call_duration_seconds":
                    if min_val is not None:
                        filter_conditions.append(
                            cast(
                                cast(WorkflowRunModel.usage_info, JSONB)[
                                    "call_duration_seconds"
                                ],
                                Integer,
                            )
                            >= min_val
                        )
                    if max_val is not None:
                        filter_conditions.append(
                            cast(
                                cast(WorkflowRunModel.usage_info, JSONB)[
                                    "call_duration_seconds"
                                ],
                                Integer,
                            )
                            <= max_val
                        )

                elif field == "cost_info.total_cost_usd":
                    if min_val is not None:
                        filter_conditions.append(
                            cast(
                                cast(WorkflowRunModel.cost_info, JSONB)[
                                    "total_cost_usd"
                                ],
                                Integer,
                            )
                            >= min_val
                        )
                    if max_val is not None:
                        filter_conditions.append(
                            cast(
                                cast(WorkflowRunModel.cost_info, JSONB)[
                                    "total_cost_usd"
                                ],
                                Integer,
                            )
                            <= max_val
                        )

    if filter_conditions:
        base_query = base_query.where(and_(*filter_conditions))

    return base_query
