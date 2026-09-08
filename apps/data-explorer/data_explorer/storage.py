"""Private object-store access using the AWS SDK default credential chain."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath

import aioboto3
from botocore.exceptions import BotoCoreError, ClientError

from .config import Settings


class ObjectNotAvailable(Exception):
    pass


def safe_filename(value: str) -> str:
    """Return a single, portable filename; never trust an object key as a path."""
    name = PurePath(value.replace("\\", "/")).name
    safe = "".join(char for char in name if char.isalnum() or char in "._-")
    return safe or "file"


@dataclass(frozen=True)
class ObjectMetadata:
    content_type: str | None
    size_bytes: int | None


class S3ObjectStore:
    def __init__(self, settings: Settings):
        self.bucket = settings.s3_bucket
        self.region = settings.s3_region
        self.endpoint_url = settings.s3_endpoint_url
        self.expiry = settings.presign_expiry_seconds
        self.session = aioboto3.Session()

    def _client_kwargs(self) -> dict:
        values = {"region_name": self.region}
        if self.endpoint_url:
            values["endpoint_url"] = self.endpoint_url
        return values

    def _require_bucket(self) -> str:
        if not self.bucket:
            raise ObjectNotAvailable("Object storage is not configured")
        return self.bucket

    async def metadata(self, key: str) -> ObjectMetadata:
        try:
            async with self.session.client("s3", **self._client_kwargs()) as client:
                result = await client.head_object(Bucket=self._require_bucket(), Key=key)
            return ObjectMetadata(result.get("ContentType"), result.get("ContentLength"))
        except (BotoCoreError, ClientError) as exc:
            raise ObjectNotAvailable("Associated object is unavailable") from exc

    async def presigned_download(self, key: str, filename: str, *, inline: bool) -> str:
        try:
            async with self.session.client("s3", **self._client_kwargs()) as client:
                return await client.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": self._require_bucket(),
                        "Key": key,
                        "ResponseContentDisposition": f'{"inline" if inline else "attachment"}; filename="{safe_filename(filename)}"',
                    },
                    ExpiresIn=self.expiry,
                )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectNotAvailable("Associated object is unavailable") from exc

    async def bytes(self, key: str, max_bytes: int) -> bytes:
        try:
            async with self.session.client("s3", **self._client_kwargs()) as client:
                response = await client.get_object(Bucket=self._require_bucket(), Key=key)
                size = response.get("ContentLength")
                if size is not None and size > max_bytes:
                    raise ObjectNotAvailable("Object exceeds package size limit")
                body = await response["Body"].read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ObjectNotAvailable("Object exceeds package size limit")
                return body
        except ObjectNotAvailable:
            raise
        except (BotoCoreError, ClientError) as exc:
            raise ObjectNotAvailable("Associated object is unavailable") from exc
