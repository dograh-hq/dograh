from typing import Any

import aioboto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger

from api.constants import S3_KMS_KEY_ID, S3_SERVER_SIDE_ENCRYPTION

from .base import AsyncReadable, BaseFileSystem


class S3FileSystem(BaseFileSystem):
    """S3 implementation of the filesystem interface."""

    def __init__(
        self,
        bucket_name: str,
        region_name: str = "us-east-1",
        endpoint_url: str | None = None,
        signature_version: str | None = None,
        addressing_style: str | None = None,
    ):
        """Initialize S3 filesystem.

        Args:
            bucket_name: Name of the S3 bucket
            region_name: AWS region name
            endpoint_url: Optional custom S3 endpoint (e.g. for MinIO/rustfs).
                ``None`` uses AWS's default endpoint resolution.
            signature_version: Optional botocore signature version (e.g.
                ``"s3v4"``). ``None`` keeps botocore's default signing behavior.
            addressing_style: Optional S3 addressing style (``"path"`` /
                ``"virtual"`` / ``"auto"``). ``None`` keeps botocore's default.
        """
        self.bucket_name = bucket_name
        self.region_name = region_name
        self.endpoint_url = endpoint_url
        self.session = aioboto3.Session()

        # Build a botocore Config only when an override is requested so that the
        # default behavior is byte-for-byte unchanged when no env vars are set.
        config_kwargs: dict[str, Any] = {}
        if signature_version:
            config_kwargs["signature_version"] = signature_version
        if addressing_style:
            config_kwargs["s3"] = {"addressing_style": addressing_style}
        self._config = Config(**config_kwargs) if config_kwargs else None

    def _log_failure(self, operation: str, file_path: str, error: Exception) -> None:
        """Log safe S3 diagnostics without credentials or artifact content."""
        error_code = None
        if isinstance(error, ClientError):
            error_code = error.response.get("Error", {}).get("Code")
        logger.warning(
            "S3 {} failed bucket={} key={} error_class={} error_code={}",
            operation,
            self.bucket_name,
            file_path,
            type(error).__name__,
            error_code or "",
        )

    def _client_kwargs(self) -> dict[str, Any]:
        """Common kwargs for every ``session.client("s3", ...)`` call.

        Only includes ``endpoint_url`` / ``config`` when configured, so default
        deployments behave exactly as before.
        """
        kwargs: dict[str, Any] = {"region_name": self.region_name}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        if self._config is not None:
            kwargs["config"] = self._config
        return kwargs

    async def acreate_file(self, file_path: str, content: AsyncReadable) -> bool:
        try:
            put_kwargs = {
                "Bucket": self.bucket_name,
                "Key": file_path,
                "Body": await content.read(),
            }
            # Keep recordings private and let the bucket's default encryption
            # policy apply unless an explicit encryption mode is configured.
            # ``aws:kms`` without a customer-managed key is intentionally
            # omitted: it still requires KMS permissions, while an encrypted
            # bucket can safely apply its own S3-managed default.
            # A custom endpoint may be an S3-compatible service rather than
            # AWS. Do not send AWS KMS headers there by default; operators can
            # opt into a compatible server-side encryption value explicitly.
            if (
                S3_SERVER_SIDE_ENCRYPTION
                and (not self.endpoint_url or S3_SERVER_SIDE_ENCRYPTION != "aws:kms")
                and (S3_SERVER_SIDE_ENCRYPTION != "aws:kms" or S3_KMS_KEY_ID)
            ):
                put_kwargs["ServerSideEncryption"] = S3_SERVER_SIDE_ENCRYPTION
            if S3_KMS_KEY_ID and not self.endpoint_url:
                put_kwargs["ServerSideEncryption"] = "aws:kms"
                put_kwargs["SSEKMSKeyId"] = S3_KMS_KEY_ID
            async with self.session.client("s3", **self._client_kwargs()) as s3_client:
                await s3_client.put_object(**put_kwargs)
            return True
        except (BotoCoreError, ClientError) as exc:
            self._log_failure("put_object", file_path, exc)
            return False
        except Exception as exc:  # noqa: BLE001 - preserve boolean storage contract
            self._log_failure("put_object", file_path, exc)
            return False

    async def aupload_file(self, local_path: str, destination_path: str) -> bool:
        try:
            await self.aupload_file_checked(local_path, destination_path)
            return True
        except (BotoCoreError, ClientError) as exc:
            self._log_failure("upload_file", destination_path, exc)
            return False
        except Exception as exc:  # noqa: BLE001 - preserve boolean storage contract
            self._log_failure("upload_file", destination_path, exc)
            return False

    async def aupload_file_checked(
        self, local_path: str, destination_path: str
    ) -> None:
        """Upload an object while preserving the provider error for callers.

        Existing callers use ``aupload_file``'s boolean contract. Secondary
        replication needs the provider error class for safe retry diagnostics,
        so it can opt into this checked variant without duplicating SDK setup.
        """
        async with self.session.client("s3", **self._client_kwargs()) as s3_client:
            await s3_client.upload_file(
                local_path, self.bucket_name, destination_path
            )

    async def aget_signed_url(
        self,
        file_path: str,
        expiration: int = 3600,
        force_inline: bool = False,
        use_internal_endpoint: bool = False,
    ) -> str | None:
        """Generate a presigned GET url for the given object.

        For transcript text files we force the response headers so that the
        browser renders the content **inline** instead of triggering a file
        download.  We do this by asking S3 to override the content type &
        disposition on the response.
        """
        try:
            async with self.session.client("s3", **self._client_kwargs()) as s3_client:
                params = {"Bucket": self.bucket_name, "Key": file_path}

                # Make artifacts viewable inline in the browser when requested
                if force_inline:
                    if file_path.endswith(".txt"):
                        params.update(
                            {
                                "ResponseContentType": "text/plain",
                                "ResponseContentDisposition": "inline",
                            }
                        )
                    elif file_path.endswith(".wav"):
                        params.update(
                            {
                                "ResponseContentType": "audio/wav",
                                "ResponseContentDisposition": "inline",
                            }
                        )
                    elif file_path.endswith(".mp3"):
                        params.update(
                            {
                                "ResponseContentType": "audio/mpeg",
                                "ResponseContentDisposition": "inline",
                            }
                        )

                url = await s3_client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=expiration,
                )
            return url
        except (BotoCoreError, ClientError) as exc:
            self._log_failure("presign_get", file_path, exc)
            return None
        except Exception as exc:  # noqa: BLE001 - preserve download contract
            self._log_failure("presign_get", file_path, exc)
            return None

    async def aget_file_metadata(self, file_path: str) -> dict[str, Any] | None:
        """Get S3 object metadata."""
        try:
            async with self.session.client("s3", **self._client_kwargs()) as s3_client:
                response = await s3_client.head_object(
                    Bucket=self.bucket_name, Key=file_path
                )
                return {
                    "size": response.get("ContentLength"),
                    "created_at": response.get("LastModified"),
                    "modified_at": response.get("LastModified"),
                    "etag": response.get("ETag", "").strip('"'),
                    "content_type": response.get("ContentType"),
                    "storage_class": response.get("StorageClass"),
                }
        except ClientError:
            return None

    async def aget_presigned_put_url(
        self,
        file_path: str,
        expiration: int = 900,
        content_type: str = "text/csv",
        max_size: int = 10_485_760,
    ) -> str | None:
        """Generate a presigned PUT URL for direct file upload."""
        try:
            async with self.session.client("s3", **self._client_kwargs()) as s3_client:
                url = await s3_client.generate_presigned_url(
                    "put_object",
                    Params={
                        "Bucket": self.bucket_name,
                        "Key": file_path,
                        "ContentType": content_type,
                    },
                    ExpiresIn=expiration,
                )
            return url
        except ClientError:
            return None

    async def adownload_file(self, source_path: str, local_path: str) -> bool:
        """Download a file from S3 to local path."""
        try:
            async with self.session.client("s3", **self._client_kwargs()) as s3_client:
                await s3_client.download_file(self.bucket_name, source_path, local_path)
            return True
        except ClientError:
            return False

    async def acopy_file(self, source_path: str, destination_path: str) -> bool:
        """Copy a file within S3 (server-side copy)."""
        try:
            async with self.session.client("s3", **self._client_kwargs()) as s3_client:
                await s3_client.copy_object(
                    Bucket=self.bucket_name,
                    Key=destination_path,
                    CopySource={"Bucket": self.bucket_name, "Key": source_path},
                )
            return True
        except ClientError:
            return False
