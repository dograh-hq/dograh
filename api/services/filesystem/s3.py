from typing import Any, BinaryIO, Dict, Optional

import aioboto3
from botocore.exceptions import ClientError

from .base import BaseFileSystem


class S3FileSystem(BaseFileSystem):
    """S3 implementation of the filesystem interface."""

    def __init__(self, bucket_name: str, region_name: str = "us-east-1"):
        """Initialize S3 filesystem.

        Args:
            bucket_name: Name of the S3 bucket
            region_name: AWS region name
        """
        self.bucket_name = bucket_name
        self.region_name = region_name
        self.session = aioboto3.Session()

    async def acreate_file(self, file_path: str, content: BinaryIO) -> bool:
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                await s3_client.put_object(
                    Bucket=self.bucket_name, Key=file_path, Body=await content.read()
                )
            return True
        except ClientError:
            return False

    async def aupload_file(self, local_path: str, destination_path: str) -> bool:
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                await s3_client.upload_file(
                    local_path, self.bucket_name, destination_path
                )
            return True
        except ClientError:
            return False

    async def aget_signed_url(
        self,
        file_path: str,
        expiration: int = 3600,
        force_inline: bool = False,
        use_internal_endpoint: bool = False,
    ) -> Optional[str]:
        """Generate a presigned GET url for the given object.

        For transcript text files we force the response headers so that the
        browser renders the content **inline** instead of triggering a file
        download.  We do this by asking S3 to override the content type &
        disposition on the response.
        """
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                params = {"Bucket": self.bucket_name, "Key": file_path}

                # Make transcripts viewable inline in the browser when requested
                if force_inline and file_path.endswith(".txt"):
                    params.update(
                        {
                            "ResponseContentType": "text/plain",
                            "ResponseContentDisposition": "inline",
                        }
                    )

                url = await s3_client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=expiration,
                )
            return url
        except ClientError:
            return None

    async def aget_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get S3 object metadata."""
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
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
    ) -> Optional[str]:
        """Generate a presigned PUT URL for direct file upload."""
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
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
