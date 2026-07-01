import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

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
    headers: Optional[List[str]] = field(default=None, repr=False)
    rows: Optional[List[List[str]]] = field(default=None, repr=False)


class CampaignSourceSyncService(ABC):
    """Base class for campaign data source synchronization"""

    @staticmethod
    def normalize_headers(headers: List[str]) -> List[str]:
        """Normalize headers by stripping whitespace and lowercasing."""
        return [h.strip().lower() for h in headers]

    # Header names that clearly denote a phone/mobile column.
    PHONE_HEADER_HINTS = (
        "phone_number", "phone", "phonenumber", "phone_no", "phoneno",
        "mobile", "mobile_number", "mobileno", "mobile_no", "cell",
        "contact", "contact_number", "contact_no", "number", "msisdn",
        "whatsapp", "tel", "telephone",
    )

    @staticmethod
    def _looks_like_phone(value: str) -> bool:
        """Loose phone-shape check: 7-15 digits, optional leading '+'."""
        v = (value or "").strip()
        if not v:
            return False
        digits = re.sub(r"[\s\-()]", "", v)
        if digits.startswith("+"):
            digits = digits[1:]
        return digits.isdigit() and 7 <= len(digits) <= 15

    @staticmethod
    def detect_phone_column(
        headers: List[str], rows: List[List[str]], sample: int = 25
    ) -> Optional[int]:
        """Best-guess the phone column index: exact known name, then a hint
        token in the name, then value shape. None when nothing looks like one."""
        norm = CampaignSourceSyncService.normalize_headers(headers)
        for i, h in enumerate(norm):
            if h in CampaignSourceSyncService.PHONE_HEADER_HINTS:
                return i
        for i, h in enumerate(norm):
            if any(tok in h for tok in ("phone", "mobile", "msisdn", "whatsapp")):
                return i
        best_idx, best_hits = None, 0
        checked = rows[:sample]
        for i in range(len(norm)):
            hits = sum(
                1
                for row in checked
                if len(row) > i and CampaignSourceSyncService._looks_like_phone(row[i])
            )
            if hits > best_hits:
                best_idx, best_hits = i, hits
        if best_idx is not None and best_hits >= max(1, len(checked) // 2):
            return best_idx
        return None

    @staticmethod
    def apply_column_mapping(
        headers: List[str],
        rows: List[List[str]],
        column_mapping: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Normalized headers with the user's CSV-header -> variable mapping
        applied, and the phone column renamed to 'phone_number' (auto-detected
        when the mapping didn't already provide one). Backward compatible: with
        no mapping and a literal 'phone_number' header, headers are unchanged."""
        norm = CampaignSourceSyncService.normalize_headers(headers)
        mapping = {
            k.strip().lower(): str(v).strip().lower()
            for k, v in (column_mapping or {}).items()
            if v and str(v).strip()
        }
        effective = [mapping.get(h, h) for h in norm]
        if "phone_number" not in effective:
            idx = CampaignSourceSyncService.detect_phone_column(headers, rows)
            if idx is not None:
                effective[idx] = "phone_number"
        return effective

    @staticmethod
    def validate_source_data(
        headers: List[str],
        rows: List[List[str]],
        column_mapping: Optional[Dict[str, str]] = None,
    ) -> ValidationResult:
        """
        Validate source data for campaign creation.

        Args:
            headers: List of column headers
            rows: List of data rows (excluding header)
            column_mapping: optional CSV-header -> variable overrides; the phone
                column is auto-detected when not mapped.

        Returns:
            ValidationResult with is_valid=True if valid, or error details if invalid
        """
        normalized_headers = CampaignSourceSyncService.apply_column_mapping(
            headers, rows, column_mapping
        )

        # Phone column (mapped or auto-detected above)
        if "phone_number" not in normalized_headers:
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message="Couldn't find a phone-number column. Map one of your "
                    "columns to the phone number, or include a 'phone_number' column."
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
        column_mapping: Optional[Dict[str, str]] = None,
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
