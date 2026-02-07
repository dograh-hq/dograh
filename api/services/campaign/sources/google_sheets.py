import re
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from api.db import db_client
from api.services.campaign.source_sync import (
    CampaignSourceSyncService,
    ValidationError,
    ValidationResult,
)
from api.services.integrations.nango import NangoService


class GoogleSheetsSyncService(CampaignSourceSyncService):
    """Implementation for Google Sheets synchronization"""

    def __init__(self):
        self.nango_service = NangoService()
        self.sheets_api_base = "https://sheets.googleapis.com/v4/spreadsheets"

    async def _get_access_token(self, organization_id: int) -> str:
        """Get OAuth access token for Google Sheets via Nango."""
        integrations = await db_client.get_integrations_by_organization_id(
            organization_id
        )
        integration = None
        for intg in integrations:
            if intg.provider == "google-sheet" and intg.is_active:
                integration = intg
                break

        if not integration:
            raise ValueError("Google Sheets integration not found or inactive")

        token_data = await self.nango_service.get_access_token(
            connection_id=integration.integration_id, provider_config_key="google-sheet"
        )
        return token_data["credentials"]["access_token"]

    async def _fetch_all_sheet_data(
        self, sheet_url: str, organization_id: int
    ) -> List[List[str]]:
        """Fetch all data from a Google Sheet. Returns all rows including header."""
        access_token = await self._get_access_token(organization_id)
        sheet_id = self._extract_sheet_id(sheet_url)

        metadata = await self._get_sheet_metadata(sheet_id, access_token)
        if not metadata.get("sheets"):
            raise ValueError("No sheets found in the spreadsheet")

        sheet_name = metadata["sheets"][0]["properties"]["title"]

        return await self._fetch_sheet_data(sheet_id, f"{sheet_name}!A:Z", access_token)

    async def validate_source(
        self, source_id: str, organization_id: Optional[int] = None
    ) -> ValidationResult:
        """Validate a Google Sheet source for campaign creation."""
        if organization_id is None:
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message="Organization ID is required for Google Sheets validation"
                ),
            )

        # Validate URL format first
        pattern = r"/spreadsheets/d/([a-zA-Z0-9-_]+)"
        if not re.search(pattern, source_id):
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Invalid Google Sheets URL: {source_id}"
                ),
            )

        try:
            rows = await self._fetch_all_sheet_data(source_id, organization_id)
        except ValueError as e:
            return ValidationResult(
                is_valid=False,
                error=ValidationError(message=str(e)),
            )
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

        return self.validate_source_data(headers, data_rows)

    async def sync_source_data(self, campaign_id: int) -> int:
        """
        Fetches data from Google Sheets and creates queued_runs
        """
        # Get campaign
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        rows = await self._fetch_all_sheet_data(
            campaign.source_id, campaign.organization_id
        )

        if not rows or len(rows) < 2:
            logger.warning(f"No data found in sheet for campaign {campaign_id}")
            return 0

        headers = self.normalize_headers(rows[0])
        data_rows = rows[1:]

        sheet_id = self._extract_sheet_id(campaign.source_id)

        queued_runs = []
        for idx, row_values in enumerate(data_rows, 1):
            # Pad row to match headers length
            padded_row = row_values + [""] * (len(headers) - len(row_values))

            # Create context variables dict
            context_vars = dict(zip(headers, padded_row))

            # Skip if no phone number
            if not context_vars.get("phone_number"):
                logger.debug(f"Skipping row {idx}: no phone_number")
                continue

            # Generate unique source UUID
            source_uuid = f"sheet_{sheet_id}_row_{idx}"

            queued_runs.append(
                {
                    "campaign_id": campaign_id,
                    "source_uuid": source_uuid,
                    "context_variables": context_vars,
                    "state": "queued",
                }
            )

        # Bulk insert
        if queued_runs:
            await db_client.bulk_create_queued_runs(queued_runs)
            logger.info(
                f"Created {len(queued_runs)} queued runs for campaign {campaign_id}"
            )

        # Update campaign total_rows
        await db_client.update_campaign(
            campaign_id=campaign_id,
            total_rows=len(queued_runs),
            source_sync_status="completed",
        )

        return len(queued_runs)

    async def _fetch_sheet_data(
        self, sheet_id: str, range: str, access_token: str
    ) -> List[List[str]]:
        """Fetch data from Google Sheets API"""
        url = f"{self.sheets_api_base}/{sheet_id}/values/{range}"
        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            data = response.json()
            return data.get("values", [])

    async def _get_sheet_metadata(
        self, sheet_id: str, access_token: str
    ) -> Dict[str, Any]:
        """Get sheet metadata including sheet names"""
        url = f"{self.sheets_api_base}/{sheet_id}"
        headers = {"Authorization": f"Bearer {access_token}"}

        logger.debug(f"Fetching sheet metadata from URL: {url}")
        logger.debug(f"Using sheet_id: {sheet_id}")

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error {e.response.status_code} for URL: {url}")
                logger.error(f"Response body: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Error fetching sheet metadata: {e}")
                raise

    def _extract_sheet_id(self, sheet_url: str) -> str:
        """
        Extract sheet ID from various Google Sheets URL formats:
        - https://docs.google.com/spreadsheets/d/{id}/edit
        - https://docs.google.com/spreadsheets/d/{id}/edit#gid=0
        """
        pattern = r"/spreadsheets/d/([a-zA-Z0-9-_]+)"
        match = re.search(pattern, sheet_url)
        if match:
            return match.group(1)
        raise ValueError(f"Invalid Google Sheets URL: {sheet_url}")
