import asyncio
import json
from typing import Any, BinaryIO, Dict, Optional

from loguru import logger
from minio import Minio
from minio.error import S3Error

from .base import BaseFileSystem


class MinioFileSystem(BaseFileSystem):
    """MinIO implementation of the filesystem interface for OSS users.

    Handles both internal (container-to-container) and external (browser) access:
    - endpoint: Used for API operations (uploads, downloads from code)
    - public_endpoint: Used for generating browser-accessible presigned URLs

    Auto-detection logic:
    1. If MINIO_PUBLIC_ENDPOINT env var is set, use it (for production/custom domains)
    2. If endpoint is "minio:9000" (Docker internal), auto-use "localhost:9000" for browser
    3. Otherwise, endpoint works for both (e.g., "localhost:9000" in local non-Docker setup)
    """

    def __init__(
        self,
        endpoint: str = "localhost:9000",
        access_key: str = "minioadmin",
        secret_key: str = "minioadmin",
        bucket_name: str = "voice-audio",
        secure: bool = False,
        public_endpoint: Optional[str] = None,
    ):
        self.bucket_name = bucket_name
        self.endpoint = endpoint
        self.public_endpoint = public_endpoint or endpoint
        self.secure = secure
        self.access_key = access_key
        self.secret_key = secret_key

        # Client for internal operations (uploads, etc.)
        self.client = Minio(
            endpoint, access_key=access_key, secret_key=secret_key, secure=secure
        )

        # Ensure bucket exists and configure anonymous access (using internal client)
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)

            # Set public read/write policy for local development
            # This allows:
            # 1. Anonymous downloads (s3:GetObject)
            # 2. Anonymous uploads (s3:PutObject) - bypasses presigned URL signature issues
            # 3. List bucket contents (s3:ListBucket) for debugging
            # Note: This is set on every initialization to ensure policy is correct
            # WARNING: Only use in local development, not production!
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": "*"},
                        "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                        "Resource": [f"arn:aws:s3:::{self.bucket_name}/*"],
                    },
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": "*"},
                        "Action": ["s3:ListBucket"],
                        "Resource": [f"arn:aws:s3:::{self.bucket_name}"],
                    },
                ],
            }

            self.client.set_bucket_policy(self.bucket_name, json.dumps(policy))
        except Exception as e:
            # Bucket might already exist or we might be in a restricted environment
            logger.debug(f"Bucket setup note: {e}")
            pass

    async def acreate_file(self, file_path: str, content: BinaryIO) -> bool:
        try:
            data = await content.read()

            def _put():
                self.client.put_object(
                    self.bucket_name,
                    file_path,
                    data=bytes(data),
                    length=len(data),
                )

            await asyncio.to_thread(_put)
            return True
        except S3Error:
            return False

    async def aupload_file(self, local_path: str, destination_path: str) -> bool:
        try:

            def _fput():
                self.client.fput_object(self.bucket_name, destination_path, local_path)

            await asyncio.to_thread(_fput)
            return True
        except S3Error:
            return False

    async def aget_signed_url(
        self,
        file_path: str,
        expiration: int = 3600,
        force_inline: bool = False,
        use_internal_endpoint: bool = False,
    ) -> Optional[str]:
        try:
            # For MinIO in local development, return unsigned URLs
            # This avoids signature mismatch issues when endpoint differs
            # MinIO must be configured to allow anonymous read access
            protocol = "https" if self.secure else "http"
            endpoint = self.endpoint if use_internal_endpoint else self.public_endpoint
            url = f"{protocol}://{endpoint}/{self.bucket_name}/{file_path}"
            return url
        except Exception as e:
            logger.error(f"Error generating MinIO URL: {e}")
            return None

    async def aget_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get MinIO object metadata."""
        try:

            def _stat():
                return self.client.stat_object(self.bucket_name, file_path)

            stat = await asyncio.to_thread(_stat)
            return {
                "size": stat.size,
                "created_at": stat.last_modified,
                "modified_at": stat.last_modified,
                "etag": stat.etag.strip('"') if stat.etag else None,
                "content_type": stat.content_type,
                "storage_class": None,  # MinIO doesn't have storage classes like S3
            }
        except S3Error:
            return None

    async def aget_presigned_put_url(
        self,
        file_path: str,
        expiration: int = 900,
        content_type: str = "text/csv",
        max_size: int = 10_485_760,
    ) -> Optional[str]:
        """Generate an unsigned URL for direct file upload.

        For local MinIO development with anonymous upload enabled, we return
        a simple unsigned URL instead of a presigned URL. This avoids signature
        mismatch issues when the internal endpoint (minio:9000) differs from
        the public endpoint (localhost:9000).

        The bucket policy allows anonymous s3:PutObject, so no signature is needed.
        """
        try:
            # Return unsigned URL for anonymous upload
            protocol = "https" if self.secure else "http"
            url = f"{protocol}://{self.public_endpoint}/{self.bucket_name}/{file_path}"
            logger.debug(f"Generated unsigned upload URL: {url}")
            return url
        except Exception as e:
            logger.error(f"Error generating MinIO upload URL: {e}")
            return None
