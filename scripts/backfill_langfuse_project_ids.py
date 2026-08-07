"""Backfill ``project_id`` into org Langfuse credentials.

Langfuse v4 trace links are project-scoped, and the runtime deliberately does
not call Langfuse to discover the project id — it reads it from config. This
one-off fills that field in for orgs configured before it was required.

For each org holding LANGFUSE_CREDENTIALS it calls ``GET /api/public/projects``
with the org's own key pair. Langfuse keys are project-scoped, so a valid pair
returns exactly one project. Orgs whose credentials Langfuse rejects (or that
return an ambiguous number of projects) are reported as droppable, but deleting
them needs ``--drop-invalid`` on top of ``--apply``. Deleting is not neutral:
``_OrgRoutingExporter`` sends spans with no registered org exporter to the
default one, so a dropped org's traces start landing in the deployment's own
Langfuse project rather than going nowhere.

The stored host is normalized at the same time — some orgs pasted a full
trace-page URL, which yields an unusable OTLP endpoint.

Dry run (default) prints the plan and changes nothing::

    source venv/bin/activate && set -a && source api/.env && set +a \
        && python -m scripts.backfill_langfuse_project_ids

Apply it::

    ... && python -m scripts.backfill_langfuse_project_ids --apply
"""

import argparse
import asyncio
import sys

import httpx

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.pipecat.tracing_config import normalize_langfuse_host


async def resolve_project_id(client, host, public_key, secret_key):
    """Return (project_id, note). ``project_id`` is None when unresolvable."""
    base = normalize_langfuse_host(host)
    if not all([base, public_key, secret_key]):
        return None, "incomplete credentials"
    try:
        response = await client.get(
            f"{base}/api/public/projects", auth=(public_key, secret_key), timeout=15.0
        )
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:80]}"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    projects = response.json().get("data", [])
    if len(projects) != 1:
        return None, f"{len(projects)} projects returned, cannot disambiguate"
    return projects[0].get("id"), projects[0].get("name", "")


async def main(apply: bool, drop_invalid: bool):
    configs = await db_client.get_all_configurations_by_key(
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
    )
    print(f"{len(configs)} org(s) with Langfuse credentials\n")

    backfill, drop, unchanged = [], [], []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for config in configs:
            org_id = config["organization_id"]
            value = config["value"] or {}
            project_id, note = await resolve_project_id(
                client,
                value.get("host"),
                value.get("public_key"),
                value.get("secret_key"),
            )

            if not project_id:
                drop.append((org_id, note))
                print(f"  org={org_id:>6}  DROP        {note}")
                continue

            normalized_host = normalize_langfuse_host(value.get("host"))
            already = (
                value.get("project_id") == project_id
                and value.get("host") == normalized_host
            )
            if already:
                unchanged.append(org_id)
                print(f"  org={org_id:>6}  unchanged   {project_id}")
                continue

            new_value = {
                **value,
                "host": normalized_host,
                "project_id": project_id,
            }
            backfill.append((org_id, new_value))
            host_note = (
                f"  host {value.get('host')!r} -> {normalized_host!r}"
                if value.get("host") != normalized_host
                else ""
            )
            print(f"  org={org_id:>6}  BACKFILL    {project_id} ({note}){host_note}")

    print(
        f"\nPlan: {len(backfill)} backfill, {len(drop)} drop, "
        f"{len(unchanged)} already correct"
    )

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply to commit.")
        return

    for org_id, new_value in backfill:
        await db_client.upsert_configuration(
            org_id,
            OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
            new_value,
        )

    dropped = 0
    if drop_invalid:
        for org_id, _ in drop:
            await db_client.delete_configuration(
                org_id,
                OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
            )
            dropped += 1
    elif drop:
        print(
            f"\n{len(drop)} org(s) with unusable credentials left in place. "
            "Pass --drop-invalid to delete them — note their spans then fall "
            "back to this deployment's own Langfuse project."
        )

    print(f"\nApplied: {len(backfill)} backfilled, {dropped} dropped.")
    print("Restart the API workers so exporters reload from the updated config.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without it the script only prints the plan.",
    )
    parser.add_argument(
        "--drop-invalid",
        action="store_true",
        help=(
            "Also delete configs whose credentials Langfuse rejects. Their spans "
            "then fall back to this deployment's own Langfuse project."
        ),
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.apply, args.drop_invalid)) or 0)
