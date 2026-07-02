import csv
import hashlib
from io import BytesIO, StringIO
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
    """Implementation for CSV and Excel file synchronization"""

    async def _fetch_csv_data(self, file_key: str) -> List[List[str]]:
        """Download and parse CSV or Excel file from storage. Returns all rows including header."""
        signed_url = await storage_fs.aget_signed_url(
            file_key, expiration=3600, use_internal_endpoint=True
        )

        if not signed_url:
            raise ValueError(f"Failed to access source file: {file_key}")

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(signed_url)
                response.raise_for_status()
                file_content = response.content
            except httpx.HTTPError as e:
                logger.error(f"Failed to download source file: {e} for url: {signed_url}")
                raise ValueError(f"Failed to download source file from storage: {str(e)}")

        ext = file_key.split(".")[-1].lower() if "." in file_key else ""
        if ext == "xlsx":
            return self._parse_xlsx(file_content)
        elif ext == "xls":
            return self._parse_xls(file_content)
        else:
            try:
                csv_content = file_content.decode("utf-8")
            except UnicodeDecodeError:
                csv_content = file_content.decode("latin-1")
            return self._parse_csv(csv_content)

    async def validate_source(
        self,
        source_id: str,
        organization_id: Optional[int] = None,
        column_mapping: Optional[dict] = None,
        default_country_code: Optional[str] = None,
    ) -> ValidationResult:
        """Validate a CSV/Excel source file for campaign creation."""
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
                    message="File must have a header row and at least one data row"
                ),
            )

        headers = csv_data[0]
        data_rows = csv_data[1:]

        return self.validate_source_data(
            headers, data_rows, column_mapping, default_country_code
        )

    async def sync_source_data(self, campaign_id: int) -> int:
        """
        Fetches data from CSV/Excel file in S3/MinIO and creates queued_runs
        """
        # Get campaign
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        file_key = campaign.source_id
        csv_data = await self._fetch_csv_data(file_key)

        if not csv_data or len(csv_data) < 2:
            logger.warning(f"No data found in file for campaign {campaign_id}")
            return 0

        rows = csv_data[1:]
        column_mapping = (campaign.orchestrator_metadata or {}).get("column_mapping")
        default_country_code = (campaign.orchestrator_metadata or {}).get("default_country_code")
        headers = self.apply_column_mapping(csv_data[0], rows, column_mapping)

        # Get phone number column index so we can normalize it during sync
        phone_number_idx = headers.index("phone_number") if "phone_number" in headers else None

        # Create hash of file_key for consistent source_uuid prefix
        file_hash = hashlib.md5(file_key.encode()).hexdigest()[:8]

        # A re-run of this sync (ARQ retries the job if the worker died or was
        # cancelled mid-insert) must not enqueue the same contacts again —
        # queued_runs has no unique constraint, and duplicates mean every
        # contact gets dialed twice. Skip rows already queued.
        existing_uuids = await db_client.get_existing_source_uuids(campaign_id)

        # Convert to queued_runs
        queued_runs = []
        for idx, row_values in enumerate(rows, 1):
            # Pad row to match headers length
            padded_row = row_values + [""] * (len(headers) - len(row_values))

            # Apply phone normalization to the row if country code is configured
            if phone_number_idx is not None and phone_number_idx < len(padded_row):
                phone_val = padded_row[phone_number_idx]
                padded_row[phone_number_idx] = self.normalize_phone_number(
                    phone_val, default_country_code
                )

            # Create context variables dict
            context_vars = dict(zip(headers, padded_row))

            # Skip if no phone number
            if not context_vars.get("phone_number"):
                logger.debug(f"Skipping row {idx}: no phone_number")
                continue

            # Generate unique source UUID: csv_{hash(source_id)}_row_{idx}
            source_uuid = f"csv_{file_hash}_row_{idx}"

            if source_uuid in existing_uuids:
                continue

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

        # Update campaign total_rows. On a partial re-sync the previously
        # inserted rows count too — len(queued_runs) alone would undercount.
        total_rows = len(queued_runs) + len(existing_uuids)
        await db_client.update_campaign(
            campaign_id=campaign_id,
            total_rows=total_rows,
            source_sync_status="completed",
        )

        return total_rows

    def _parse_csv(self, csv_content: str) -> List[List[str]]:
        """Parse CSV content into rows"""
        try:
            csv_file = StringIO(csv_content)
            reader = csv.reader(csv_file)
            return list(reader)
        except Exception as e:
            logger.error(f"Failed to parse CSV: {e}")
            raise ValueError(f"Invalid CSV format: {str(e)}")

    def _parse_xlsx(self, content: bytes) -> List[List[str]]:
        """Parse XLSX content into rows using openpyxl"""
        try:
            import openpyxl
        except ImportError:
            logger.error("openpyxl is not installed but required for xlsx parsing")
            raise ValueError(
                "Excel (.xlsx) support is not installed on this server. Please install openpyxl."
            )

        try:
            wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
            sheet = wb.active
            rows = []
            if sheet:
                for r in sheet.iter_rows(values_only=True):
                    # Convert all cell values to string, handling None
                    row = [str(cell) if cell is not None else "" for cell in r]
                    # Keep row if it has at least one non-empty value
                    if any(cell.strip() != "" for cell in row):
                        rows.append(row)
            return rows
        except Exception as e:
            logger.error(f"Failed to parse XLSX: {e}")
            raise ValueError(f"Invalid XLSX format: {str(e)}")

    def _parse_xls(self, content: bytes) -> List[List[str]]:
        """Parse XLS content into rows using xlrd"""
        try:
            import xlrd
        except ImportError:
            logger.error("xlrd is not installed but required for xls parsing")
            raise ValueError(
                "Legacy Excel (.xls) support is not installed on this server. Please install xlrd."
            )

        try:
            wb = xlrd.open_workbook(file_contents=content)
            sheet = wb.sheet_by_index(0)
            rows = []
            for row_idx in range(sheet.nrows):
                row = []
                for col_idx in range(sheet.ncols):
                    val = sheet.cell_value(row_idx, col_idx)
                    if isinstance(val, float) and val.is_integer():
                        val = int(val)
                    row.append(str(val) if val is not None else "")
                if any(cell.strip() != "" for cell in row):
                    rows.append(row)
            return rows
        except Exception as e:
            logger.error(f"Failed to parse XLS: {e}")
            raise ValueError(f"Invalid XLS format: {str(e)}")
