from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger


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


class CampaignSourceSyncService(ABC):
    """Base class for campaign data source synchronization"""

    @staticmethod
    def normalize_headers(headers: List[str]) -> List[str]:
        """Normalize headers by stripping whitespace and lowercasing."""
        return [h.strip().lower() for h in headers]

    @staticmethod
    def validate_source_data(
        headers: List[str], rows: List[List[str]]
    ) -> ValidationResult:
        """
        Validate source data for campaign creation.

        Args:
            headers: List of column headers
            rows: List of data rows (excluding header)

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
        invalid_rows = []
        for row_idx, row in enumerate(
            rows, start=2
        ):  # Start at 2 (1-indexed, skip header)
            if len(row) <= phone_number_idx:
                continue  # Skip rows that don't have enough columns

            phone_number = row[phone_number_idx].strip()
            if phone_number and not phone_number.startswith("+"):
                invalid_rows.append(row_idx)

        if invalid_rows:
            # Limit the number of rows shown in error message
            if len(invalid_rows) > 5:
                rows_str = f"{', '.join(map(str, invalid_rows[:5]))} and {len(invalid_rows) - 5} more"
            else:
                rows_str = ", ".join(map(str, invalid_rows))

            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Invalid phone numbers in rows: {rows_str}. All phone numbers must include country code (start with '+')",
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
            if len(duplicate_rows) > 5:
                rows_str = f"{', '.join(map(str, duplicate_rows[:5]))} and {len(duplicate_rows) - 5} more"
            else:
                rows_str = ", ".join(map(str, duplicate_rows))

            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Duplicate phone numbers found in rows: {rows_str}. Phone numbers in a campaign must be unique.",
                    invalid_rows=duplicate_rows,
                ),
            )

        return ValidationResult(is_valid=True)

    @abstractmethod
    async def validate_source(
        self, source_id: str, organization_id: Optional[int] = None
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
        """Gets OAuth tokens or API credentials via Nango"""
        # This would be implemented to work with Nango service
        # For now, returning placeholder
        logger.info(
            f"Getting credentials for org {organization_id}, source {source_type}"
        )
        return {}
