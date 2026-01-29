"""
Source validation for campaign data sources (CSV, Google Sheets).

Validates that:
- phone_number column exists
- All phone numbers include country code (start with '+')
"""

import csv
from dataclasses import dataclass
from io import StringIO
from typing import List, Optional

import httpx
from loguru import logger

from api.services.storage import storage_fs


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


def _validate_source_data(
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
    # Normalize headers to lowercase for comparison
    normalized_headers = [h.strip().lower() for h in headers]

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
    for row_idx, row in enumerate(rows, start=2):  # Start at 2 (1-indexed, skip header)
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

    return ValidationResult(is_valid=True)


async def validate_csv_source(file_key: str) -> ValidationResult:
    """
    Validate a CSV source file for campaign creation.

    Args:
        file_key: S3/MinIO file key for the CSV file

    Returns:
        ValidationResult with is_valid=True if valid, or error details if invalid
    """
    # Get download URL using internal endpoint
    signed_url = await storage_fs.aget_signed_url(
        file_key, expiration=3600, use_internal_endpoint=True
    )

    if not signed_url:
        return ValidationResult(
            is_valid=False,
            error=ValidationError(message=f"Failed to access CSV file: {file_key}"),
        )

    # Download CSV file
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(signed_url)
            response.raise_for_status()
            csv_content = response.text
        except httpx.HTTPError as e:
            logger.error(f"Failed to download CSV file for validation: {e}")
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message="Failed to download CSV file for validation"
                ),
            )

    # Parse CSV
    try:
        csv_file = StringIO(csv_content)
        reader = csv.reader(csv_file)
        rows = list(reader)
    except Exception as e:
        logger.error(f"Failed to parse CSV: {e}")
        return ValidationResult(
            is_valid=False,
            error=ValidationError(message=f"Invalid CSV format: {str(e)}"),
        )

    if not rows or len(rows) < 2:
        return ValidationResult(
            is_valid=False,
            error=ValidationError(
                message="CSV file must have a header row and at least one data row"
            ),
        )

    headers = rows[0]
    data_rows = rows[1:]

    return _validate_source_data(headers, data_rows)


async def validate_google_sheet_source(
    sheet_url: str, organization_id: int
) -> ValidationResult:
    """
    Validate a Google Sheet source for campaign creation.

    Args:
        sheet_url: Google Sheets URL
        organization_id: Organization ID to get integration credentials

    Returns:
        ValidationResult with is_valid=True if valid, or error details if invalid
    """
    import re

    from api.db import db_client
    from api.services.integrations.nango import NangoService

    # Extract sheet ID from URL
    pattern = r"/spreadsheets/d/([a-zA-Z0-9-_]+)"
    match = re.search(pattern, sheet_url)
    if not match:
        return ValidationResult(
            is_valid=False,
            error=ValidationError(message=f"Invalid Google Sheets URL: {sheet_url}"),
        )

    sheet_id = match.group(1)

    # Get Google Sheets integration for the organization
    integrations = await db_client.get_integrations_by_organization_id(organization_id)
    integration = None
    for intg in integrations:
        if intg.provider == "google-sheet" and intg.is_active:
            integration = intg
            break

    if not integration:
        return ValidationResult(
            is_valid=False,
            error=ValidationError(
                message="Google Sheets integration not found or inactive"
            ),
        )

    # Get OAuth token via Nango
    try:
        nango_service = NangoService()
        token_data = await nango_service.get_access_token(
            connection_id=integration.integration_id, provider_config_key="google-sheet"
        )
        access_token = token_data["credentials"]["access_token"]
    except Exception as e:
        logger.error(f"Failed to get Google Sheets access token: {e}")
        return ValidationResult(
            is_valid=False,
            error=ValidationError(message="Failed to authenticate with Google Sheets"),
        )

    # Fetch sheet data
    sheets_api_base = "https://sheets.googleapis.com/v4/spreadsheets"

    async with httpx.AsyncClient() as client:
        try:
            # Get sheet metadata to find the first sheet name
            metadata_url = f"{sheets_api_base}/{sheet_id}"
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(metadata_url, headers=headers)
            response.raise_for_status()
            metadata = response.json()

            if not metadata.get("sheets"):
                return ValidationResult(
                    is_valid=False,
                    error=ValidationError(message="No sheets found in the spreadsheet"),
                )

            sheet_name = metadata["sheets"][0]["properties"]["title"]

            # Fetch all data from sheet
            data_url = f"{sheets_api_base}/{sheet_id}/values/{sheet_name}!A:Z"
            response = await client.get(data_url, headers=headers)
            response.raise_for_status()
            data = response.json()
            rows = data.get("values", [])

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching Google Sheet: {e.response.status_code}")
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Failed to fetch Google Sheet data: {e.response.status_code}"
                ),
            )
        except Exception as e:
            logger.error(f"Error fetching Google Sheet: {e}")
            return ValidationResult(
                is_valid=False,
                error=ValidationError(message="Failed to fetch Google Sheet data"),
            )

    if not rows or len(rows) < 2:
        return ValidationResult(
            is_valid=False,
            error=ValidationError(
                message="Google Sheet must have a header row and at least one data row"
            ),
        )

    headers = rows[0]
    data_rows = rows[1:]

    return _validate_source_data(headers, data_rows)
