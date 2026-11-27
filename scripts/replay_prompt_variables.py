"""Compare both prompt layouts on recent calls without executing tools.

Source api/.env with the intended database, then:
  python -m scripts.replay_prompt_variables --organization-id 1 --workflow-id 115 --output /tmp/replay.json

This performs paid LLM inference using the workflow's configured connection.
The optimized layout automatically uses explicit caching on eligible providers;
provider cache storage charges may also apply.
The output contains paired replies and tool arguments for local review.
"""

import argparse
import asyncio
import json
from pathlib import Path


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        not 1 <= args.limit <= 500
        or not 1 <= args.max_turns <= 500
        or not 1 <= args.concurrency <= 8
    ):
        parser.error("limit must be 1–500, max-turns 1–500, and concurrency 1–8")
    from api.db import db_client
    from api.schemas.workflow_configurations import PromptVariablesConfiguration
    from api.services.pipecat.prompt_cache import close_prompt_cache
    from api.services.workflow.prompt_replay import compare_recent_prompts

    workflow = await db_client.get_workflow(
        args.workflow_id, organization_id=args.organization_id
    )
    if workflow is None:
        parser.error("Workflow not found in the selected organization")
    definition = (
        await db_client.get_draft_version(args.workflow_id)
        or workflow.released_definition
    )
    config = PromptVariablesConfiguration.model_validate(
        (definition.workflow_configurations or {}).get("prompt_variables") or {}
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = await compare_recent_prompts(
            organization_id=args.organization_id,
            workflow_id=args.workflow_id,
            definition=definition,
            config=config,
            limit=args.limit,
            max_turns=args.max_turns,
            concurrency=args.concurrency,
        )
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Wrote {len(result['turns'])} paired turns to {args.output}")
    finally:
        await close_prompt_cache()
        await db_client.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
