import csv
import hashlib
from io import StringIO
from typing import List, Optional

import httpx
from loguru import logger

from api.db import db_client
from api.services.campaign.source_sync import (
    CampaignSourceSyncService,
    ValidationError,
    ValidationResult,
)
from api.services.storage import storage_fs


class CSVSyncService(CampaignSourceSyncService):
    """Implementation for CSV file synchronization"""

    async def _fetch_csv_data(self, file_key: str) -> List[List[str]]:
        """Download and parse CSV file from storage. Returns all rows including header."""
        signed_url = await storage_fs.aget_signed_url(
            file_key, expiration=3600, use_internal_endpoint=True
        )

        if not signed_url:
            raise ValueError(f"Failed to access CSV file: {file_key}")

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(signed_url)
                response.raise_for_status()
                csv_content = response.text
            except httpx.HTTPError as e:
                logger.error(f"Failed to download CSV file: {e} for url: {signed_url}")
                raise ValueError(f"Failed to download CSV file from storage: {str(e)}")

        return self._parse_csv(csv_content)

    async def validate_source(
        self, source_id: str, organization_id: Optional[int] = None
    ) -> ValidationResult:
        """Validate a CSV source file for campaign creation."""
        try:
            csv_data = await self._fetch_csv_data(source_id)
        except ValueError as e:
            return ValidationResult(
                is_valid=False,
                error=ValidationError(message=str(e)),
            )

        if not csv_data or len(csv_data) < 2:
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message="CSV file must have a header row and at least one data row"
                ),
            )

        headers = csv_data[0]
        data_rows = csv_data[1:]

        return self.validate_source_data(headers, data_rows)

    async def sync_source_data(self, campaign_id: int) -> int:
        """
        Fetches data from CSV file in S3/MinIO and creates queued_runs
        """
        # Get campaign
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        file_key = campaign.source_id
        csv_data = await self._fetch_csv_data(file_key)

        if not csv_data or len(csv_data) < 2:
            logger.warning(f"No data found in CSV for campaign {campaign_id}")
            return 0

        headers = self.normalize_headers(csv_data[0])
        rows = csv_data[1:]

        # Create hash of file_key for consistent source_uuid prefix
        file_hash = hashlib.md5(file_key.encode()).hexdigest()[:8]

        # Convert to queued_runs
        queued_runs = []
        for idx, row_values in enumerate(rows, 1):
            # Pad row to match headers length
            padded_row = row_values + [""] * (len(headers) - len(row_values))

            # Create context variables dict
            context_vars = dict(zip(headers, padded_row))

            # Skip if no phone number
            if not context_vars.get("phone_number"):
                logger.debug(f"Skipping row {idx}: no phone_number")
                continue

            # Generate unique source UUID: csv_{hash(source_id)}_row_{idx}
            source_uuid = f"csv_{file_hash}_row_{idx}"

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

    def _parse_csv(self, csv_content: str) -> List[List[str]]:
        """Parse CSV content into rows"""
        try:
            csv_file = StringIO(csv_content)
            reader = csv.reader(csv_file)
            return list(reader)
        except Exception as e:
            logger.error(f"Failed to parse CSV: {e}")
            raise ValueError(f"Invalid CSV format: {str(e)}")
