"""Dry-run inventory for Stage D; it never uploads, deletes, or mutates data.

Run inside the API container so ``minio`` resolves on the Compose network:
``python scripts/audit_s3_replication_backfill.py``.
"""

import asyncio

from sqlalchemy import func, select

from api.db import db_client
from api.db.models import ArtifactReplicationStatusModel
from api.services.storage import storage_fs


async def main() -> None:
    if not hasattr(storage_fs, "client") or not hasattr(storage_fs, "bucket_name"):
        raise RuntimeError("Dry-run inventory requires the MinIO primary backend")
    objects = await asyncio.to_thread(
        lambda: list(storage_fs.client.list_objects(storage_fs.bucket_name, recursive=True))
    )
    object_count = len(objects)
    object_bytes = sum(int(item.size or 0) for item in objects)
    async with db_client.async_session() as session:
        indexed, unsynced, unsynced_bytes = (
            await session.execute(
                select(
                    func.count(ArtifactReplicationStatusModel.id),
                    func.count(ArtifactReplicationStatusModel.id).filter(
                        ArtifactReplicationStatusModel.s3_saved.is_(False)
                    ),
                    func.coalesce(
                        func.sum(ArtifactReplicationStatusModel.size_bytes).filter(
                            ArtifactReplicationStatusModel.s3_saved.is_(False)
                        ),
                        0,
                    ),
                )
            )
        ).one()
    print({
        "mode": "dry_run",
        "primary_bucket": storage_fs.bucket_name,
        "objects_found": object_count,
        "primary_bytes": object_bytes,
        "objects_already_indexed": indexed,
        "indexed_objects_missing_s3_copy": unsynced,
        "estimated_indexed_bytes_requiring_upload": int(unsynced_bytes or 0),
        "bulk_upload_started": False,
    })


if __name__ == "__main__":
    asyncio.run(main())
