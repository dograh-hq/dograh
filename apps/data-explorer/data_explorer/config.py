"""Configuration for the isolated, read-only Data Explorer service."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_token: str
    audit_log_path: str
    s3_bucket: str | None
    s3_region: str
    s3_endpoint_url: str | None
    presign_expiry_seconds: int
    package_max_bytes: int

    @classmethod
    def from_environment(cls) -> "Settings":
        # This intentionally uses a distinct URL. Deployment must point it at a
        # PostgreSQL role granted SELECT only, never Dograh's application role.
        return cls(
            database_url=_required("DATA_EXPLORER_DATABASE_READONLY_URL"),
            admin_token=_required("DATA_EXPLORER_ADMIN_TOKEN"),
            audit_log_path=os.getenv("DATA_EXPLORER_AUDIT_LOG_PATH", "/var/log/calmos-data-explorer/audit.jsonl"),
            s3_bucket=(os.getenv("DATA_EXPLORER_S3_BUCKET") or os.getenv("AWS_RECORDINGS_BUCKET") or os.getenv("S3_BUCKET")),
            s3_region=os.getenv("AWS_REGION") or os.getenv("S3_REGION", "eu-west-2"),
            s3_endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
            presign_expiry_seconds=int(os.getenv("DATA_EXPLORER_PRESIGN_EXPIRY_SECONDS", "300")),
            package_max_bytes=int(os.getenv("DATA_EXPLORER_PACKAGE_MAX_BYTES", "104857600")),
        )
