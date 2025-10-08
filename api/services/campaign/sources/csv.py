import csv
import hashlib
from io import StringIO
from typing import Any, Dict, List

import httpx
from loguru import logger

from api.db import db_client
from api.services.campaign.source_sync import CampaignSourceSyncService
from api.services.storage import storage_fs


class CSVSyncService(CampaignSourceSyncService):
    """Implementation for CSV file synchronization"""

    async def sync_source_data(self, campaign_id: int) -> int:
        """
        Fetches data from CSV file in S3/MinIO and creates queued_runs
        """
        # Get campaign
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        # 1. Get download URL using internal endpoint (for container-to-container access)
        file_key = campaign.source_id
        signed_url = await storage_fs.aget_signed_url(
            file_key, expiration=3600, use_internal_endpoint=True
        )

        if not signed_url:
            raise ValueError(f"Failed to generate download URL for file: {file_key}")

        # 2. Download CSV file
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(signed_url)
                response.raise_for_status()
                csv_content = response.text
            except httpx.HTTPError as e:
                logger.error(f"Failed to download CSV file: {e} for url: {signed_url}")
                raise ValueError(f"Failed to download CSV file from storage: {str(e)}")

        # 3. Parse CSV
        csv_data = self._parse_csv(csv_content)

        if not csv_data or len(csv_data) < 2:
            logger.warning(f"No data found in CSV for campaign {campaign_id}")
            return 0

        headers = csv_data[0]  # First row is headers
        rows = csv_data[1:]  # Rest is data

        # 4. Create hash of file_key for consistent source_uuid prefix
        file_hash = hashlib.md5(file_key.encode()).hexdigest()[:8]

        # 5. Convert to queued_runs
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

        # 6. Bulk insert
        if queued_runs:
            await db_client.bulk_create_queued_runs(queued_runs)
            logger.info(
                f"Created {len(queued_runs)} queued runs for campaign {campaign_id}"
            )

        # 7. Update campaign total_rows
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

    async def validate_source_schema(self, source_config: Dict[str, Any]) -> bool:
        """Validate that required columns exist in CSV"""
        required_columns = ["phone_number", "first_name", "last_name"]

        file_key = source_config.get("source_id")
        if not file_key:
            return False

        # Get download URL using internal endpoint
        signed_url = await storage_fs.aget_signed_url(
            file_key, expiration=3600, use_internal_endpoint=True
        )
        if not signed_url:
            return False

        # Download just enough to get headers
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(signed_url)
                response.raise_for_status()

                # Get just the first line for headers
                first_line = response.text.split("\n")[0]
                csv_file = StringIO(first_line)
                reader = csv.reader(csv_file)
                headers = next(reader, [])

                return all(col in headers for col in required_columns)
            except Exception as e:
                logger.error(f"Failed to validate CSV schema: {e}")
                return False
