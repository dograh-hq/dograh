"""Rewrite dead legacy Langfuse trace URLs on workflow runs.

Langfuse v4 dropped the trace entity. Its ``/trace/<id>`` page resolves the
project by looking the trace up in the legacy ``traces`` table, so the link
still works for anything ingested before a deployment's v4 cutover and 404s for
everything after it. Runs recorded between the cutover and the deploy of the
project-scoped URL fix therefore hold links that go nowhere.

This rewrites those to ``<host>/project/<project_id>/traces/<trace_id>``. The
trace id is unchanged — the data was always fine, only the link was wrong.

Every candidate is probed before being touched: a legacy URL whose page returns
``notFound`` is rewritten, one that still redirects is left alone. That keeps
the run set exact rather than trusting a timestamp boundary, and makes the
script safe to re-run.

The project id comes from the same place the app now reads it: the org's
LANGFUSE_CREDENTIALS config for runs on an org-specific host, or
LANGFUSE_PROJECT_ID for runs on the deployment's default host.

Dry run (default)::

    source venv/bin/activate && set -a && source api/.env && set +a \
        && python -m scripts.fix_broken_langfuse_trace_urls

Apply::

    ... && python -m scripts.fix_broken_langfuse_trace_urls --apply
"""

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from api.constants import LANGFUSE_HOST, LANGFUSE_PROJECT_ID
from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.pipecat.tracing_config import normalize_langfuse_host

LEGACY_URL = re.compile(r"^(?P<host>https?://[^/]+)/trace/(?P<trace_id>[0-9a-fA-F]+)$")

# The v4 cutover on this deployment, bisected from real runs: run 620288 at
# 09:09:21 still redirects, run 620289 at 09:15:02 is the first that 404s.
# Links written before this are still resolvable and are left alone.
DEFAULT_SINCE = "2026-08-07T09:12:00"


async def load_org_project_ids():
    """Map org id -> configured Langfuse (host, project_id)."""
    configs = await db_client.get_all_configurations_by_key(
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
    )
    out = {}
    for config in configs:
        value = config["value"] or {}
        out[config["organization_id"]] = (
            normalize_langfuse_host(value.get("host")),
            value.get("project_id"),
        )
    return out


async def is_dead(client, url):
    """True when the legacy trace page 404s (no legacy row to resolve)."""
    try:
        response = await client.get(url, timeout=20.0)
    except Exception:
        return None  # unreachable — cannot classify, leave alone
    return '"notFound":true' in response.text


async def main(apply: bool, since):
    default_host = normalize_langfuse_host(LANGFUSE_HOST)
    org_project_ids = await load_org_project_ids()

    async with db_client.async_session() as session:
        result = await session.execute(
            text("""
                SELECT r.id, r.gathered_context->>'trace_url' AS trace_url,
                       w.organization_id
                FROM workflow_runs r
                JOIN workflows w ON w.id = r.workflow_id
                WHERE r.gathered_context->>'trace_url' LIKE '%/trace/%'
                  AND r.gathered_context->>'trace_url' NOT LIKE '%/project/%'
                  AND r.created_at >= :since
                ORDER BY r.id
            """),
            {"since": since},
        )
        rows = list(result.mappings())

    print(f"{len(rows)} run(s) still holding a legacy trace URL\n")

    planned, skipped, alive, unresolved = [], 0, 0, 0

    async with httpx.AsyncClient(follow_redirects=False) as client:
        for row in rows:
            match = LEGACY_URL.match(row["trace_url"] or "")
            if not match:
                skipped += 1
                continue
            host = match.group("host")
            trace_id = match.group("trace_id")

            # Only touch links that are actually dead.
            dead = await is_dead(client, row["trace_url"])
            if dead is None:
                skipped += 1
                continue
            if not dead:
                alive += 1
                continue

            org_host, org_project = org_project_ids.get(
                row["organization_id"], (None, None)
            )
            if normalize_langfuse_host(host) == org_host and org_project:
                project_id = org_project
            elif normalize_langfuse_host(host) == default_host:
                project_id = LANGFUSE_PROJECT_ID
            else:
                project_id = None

            if not project_id:
                unresolved += 1
                print(f"  run={row['id']:>8}  NO PROJECT ID for host {host}")
                continue

            planned.append(
                (row["id"], f"{host}/project/{project_id}/traces/{trace_id}")
            )

    print(f"  {len(planned)} dead link(s) to rewrite")
    print(f"  {alive} still resolving, left alone")
    if unresolved:
        print(f"  {unresolved} dead but no project id available — NOT rewritten")
    if skipped:
        print(f"  {skipped} unparseable or unreachable, skipped")

    for run_id, new_url in planned[:5]:
        print(f"    e.g. run={run_id} -> {new_url}")
    if len(planned) > 5:
        print(f"    ... and {len(planned) - 5} more")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply to commit.")
        return

    async with db_client.async_session() as session:
        for run_id, new_url in planned:
            await session.execute(
                text("""
                    UPDATE workflow_runs
                    SET gathered_context = cast(
                        jsonb_set(
                            cast(gathered_context as jsonb),
                            '{trace_url}',
                            to_jsonb(cast(:u as text))
                        ) as json)
                    WHERE id = :id
                """),
                {"u": new_url, "id": run_id},
            )
        await session.commit()

    print(f"\nRewrote {len(planned)} trace URL(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without it the script only prints the plan.",
    )
    parser.add_argument(
        "--since",
        default=DEFAULT_SINCE,
        help=(
            "Only consider runs created at or after this UTC timestamp. Defaults "
            f"to {DEFAULT_SINCE} (the v4 cutover). Every candidate in the window "
            "is probed anyway, so widening this is safe but slower — one HTTP "
            "request per row."
        ),
    )
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    sys.exit(asyncio.run(main(args.apply, since)) or 0)
