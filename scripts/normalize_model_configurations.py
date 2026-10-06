"""Migrate organization and workflow model settings into the model catalog.

Creates provider_connections and named model_configurations from organization
defaults and released workflow overrides, then adds V3 UUID bindings to current
workflows and all drafts. The original model values stay in place for audit;
an explicit empty binding selects inheritance without reading retired values.
Identical connections/configurations are reused within each organization.

Source api/.env for the intended database before invoking this module. The
operator must take a database snapshot immediately before --apply in production.
Without --apply every transaction is read-only. Reports never contain source
configurations, credentials, customer names, prompts, or secret fingerprints.
No provider requests, key provisioning, or embedding reindexing are performed.
"""

from __future__ import annotations

import argparse
import asyncio
import json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="Preview only (the default)."
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Commit each organization atomically; take a database snapshot first.",
    )
    parser.add_argument(
        "--organization-id",
        action="append",
        type=int,
        dest="organization_ids",
        help="Limit to these org IDs; may be repeated.",
    )
    parser.add_argument(
        "--after-organization-id",
        type=int,
        default=0,
        help="Resume after this org ID (exclusive).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum organizations in this invocation (1–1000; default 100).",
    )
    return parser


async def run(args) -> int:
    # Imports happen only after argument validation, allowing --help without a
    # configured database and avoiding accidental environment printing.
    from api.db import db_client
    from api.services.configuration.model_configuration_migration import (
        normalize_organization_model_configurations,
    )

    try:
        organizations = (
            await db_client.list_model_configuration_migration_organization_ids(
                organization_ids=args.organization_ids,
                after_organization_id=args.after_organization_id,
                limit=args.limit,
            )
        )
    except Exception:  # noqa: BLE001 - sanitize all credential-bearing failures
        print(json.dumps({"status": "failed", "reason": "organization_listing_failed"}))
        return 1
    failure_count = 0
    for organization_id in organizations:
        try:
            report = await normalize_organization_model_configurations(
                organization_id, apply=args.apply
            )
            failure_count += int(report["status"] == "blocked")
        except Exception:  # noqa: BLE001 - sanitize all credential-bearing failures
            # Raw DB/Pydantic exceptions may include credentials. Keep only the
            # ID and a fixed code; the transaction has already rolled back.
            report = {
                "organization_id": organization_id,
                "status": "failed",
                "reason": "organization_transaction_failed",
            }
            failure_count += 1
        print(
            json.dumps(
                {"mode": "apply" if args.apply else "dry_run", **report}, sort_keys=True
            )
        )
    print(
        json.dumps(
            {
                "status": "complete",
                "organizations_examined": len(organizations),
                "organizations_blocked_or_failed": failure_count,
                "last_organization_id": organizations[-1] if organizations else None,
                "mode": "apply" if args.apply else "dry_run",
            },
            sort_keys=True,
        )
    )
    return 1 if failure_count else 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    if args.after_organization_id < 0 or any(
        organization_id <= 0 for organization_id in args.organization_ids or []
    ):
        parser.error(
            "organization IDs must be positive; --after-organization-id may be zero"
        )
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
