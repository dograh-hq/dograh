from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from api.utils.telephony_address import validate_destination_address


@dataclass
class ValidationError:
    """Represents a validation error with details."""

    message: str
    invalid_rows: Optional[List[int]] = None


@dataclass
class ValidationResult:
    """Result of source validation."""

    is_valid: bool
    error: Optional[ValidationError] = None
    headers: Optional[List[str]] = field(default=None, repr=False)
    rows: Optional[List[List[str]]] = field(default=None, repr=False)


class CampaignSourceSyncService(ABC):
    """Base class for campaign data source synchronization"""

    @staticmethod
    def normalize_headers(headers: List[str]) -> List[str]:
        """Normalize headers by stripping whitespace and lowercasing."""
        return [h.strip().lower() for h in headers]

    @staticmethod
    def _format_rows(row_indexes: List[int]) -> str:
        """Name the offending rows without pasting a whole column back."""
        if len(row_indexes) > 5:
            return (
                f"{', '.join(map(str, row_indexes[:5]))} "
                f"and {len(row_indexes) - 5} more"
            )
        return ", ".join(map(str, row_indexes))

    @staticmethod
    def validate_source_data(
        headers: List[str],
        rows: List[List[str]],
        *,
        require_e164: bool = True,
    ) -> ValidationResult:
        """
        Validate source data for campaign creation.

        Args:
            headers: List of column headers
            rows: List of data rows (excluding header)
            require_e164: Whether destinations must be E.164 numbers. Comes
                from the provider that will dial them, because a PBX reaches
                extensions and SIP URIs that no carrier would accept.

        Returns:
            ValidationResult with is_valid=True if valid, or error details if invalid
        """
        normalized_headers = CampaignSourceSyncService.normalize_headers(headers)

        # Check for phone_number column
        if "phone_number" not in normalized_headers:
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message="Source must contain a 'phone_number' column"
                ),
            )

        phone_number_idx = normalized_headers.index("phone_number")

        # Validate phone numbers in all data rows
        invalid_rows: List[int] = []
        first_reason: str | None = None
        for row_idx, row in enumerate(
            rows, start=2
        ):  # Start at 2 (1-indexed, skip header)
            if len(row) <= phone_number_idx:
                continue  # Skip rows that don't have enough columns

            phone_number = row[phone_number_idx].strip()
            if not phone_number:
                continue

            reason = validate_destination_address(
                phone_number, require_e164=require_e164
            )
            if reason:
                invalid_rows.append(row_idx)
                first_reason = first_reason or reason

        if invalid_rows:
            rows_str = CampaignSourceSyncService._format_rows(invalid_rows)
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=(
                        f"Invalid phone numbers in rows: {rows_str}. "
                        f"Every phone number {first_reason}."
                    ),
                    invalid_rows=invalid_rows,
                ),
            )

        # Check for duplicate phone numbers
        seen_phones: dict[str, int] = {}  # phone -> first row where it appeared
        duplicate_rows = []
        for row_idx, row in enumerate(rows, start=2):
            if len(row) <= phone_number_idx:
                continue

            phone_number = row[phone_number_idx].strip()
            if not phone_number:
                continue

            if phone_number in seen_phones:
                duplicate_rows.append(row_idx)
            else:
                seen_phones[phone_number] = row_idx

        if duplicate_rows:
            rows_str = CampaignSourceSyncService._format_rows(duplicate_rows)

            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Duplicate phone numbers found in rows: {rows_str}. Phone numbers in a campaign must be unique.",
                    invalid_rows=duplicate_rows,
                ),
            )

        return ValidationResult(is_valid=True, headers=normalized_headers, rows=rows)

    @staticmethod
    def validate_template_columns(
        headers: List[str],
        rows: List[List[str]],
        required_columns: Set[str],
    ) -> ValidationResult:
        """Validate that template variable columns exist and are non-empty in all rows."""
        normalized_headers = CampaignSourceSyncService.normalize_headers(headers)

        # Check for missing columns
        missing = required_columns - set(normalized_headers)
        if missing:
            missing_str = ", ".join(f"'{c}'" for c in sorted(missing))
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Workflow uses template variables that are missing from the source data: {missing_str}. "
                    "Add the missing columns or remove them from the workflow."
                ),
            )

        # Check for empty values in required columns
        col_indices = {col: normalized_headers.index(col) for col in required_columns}

        for col, idx in col_indices.items():
            empty_rows = []
            for row_idx, row in enumerate(rows, start=2):
                if len(row) <= idx or not row[idx].strip():
                    empty_rows.append(row_idx)

            if empty_rows:
                if len(empty_rows) > 5:
                    rows_str = f"{', '.join(map(str, empty_rows[:5]))} and {len(empty_rows) - 5} more"
                else:
                    rows_str = ", ".join(map(str, empty_rows))

                return ValidationResult(
                    is_valid=False,
                    error=ValidationError(
                        message=f"Template variable '{col}' is empty in rows: {rows_str}. "
                        "All template variables used in the workflow must have values in every row.",
                        invalid_rows=empty_rows,
                    ),
                )

        return ValidationResult(is_valid=True)

    @abstractmethod
    async def validate_source(
        self,
        source_id: str,
        organization_id: Optional[int] = None,
        *,
        require_e164: bool = True,
    ) -> ValidationResult:
        """Validate source data before campaign creation."""
        pass

    @abstractmethod
    async def sync_source_data(self, campaign_id: int) -> int:
        """
        Fetches data from source and creates queued_runs
        Each record gets a unique source_uuid based on source type
        Returns: number of records synced
        """
        pass

    async def get_source_credentials(
        self, organization_id: int, source_type: str
    ) -> Dict[str, Any]:
        """Gets source credentials when a sync service requires them."""
        logger.info(
            f"Getting credentials for org {organization_id}, source {source_type}"
        )
        return {}
