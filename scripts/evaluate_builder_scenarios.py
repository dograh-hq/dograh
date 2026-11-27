"""Run bounded, mocked text scenarios with real inference against a source file.

Source api/.env and activate the repo venv first. This command consumes inference
credits; its tool results are supplied fixtures, never live integration calls.

python -m scripts.evaluate_builder_scenarios --code agent.ts --scenarios cases.json \
    --organization-id 7 --user-id 3 --output /tmp/scenario-report.json

cases.json is [{"name": "...", "scenario": {...WorkflowScenario fields...}}].
"""

import argparse
import asyncio
import json
from pathlib import Path

from api.db import db_client
from api.services.workflow.builder_runtime.scenarios import (
    WorkflowScenario,
    run_workflow_scenario,
)


async def evaluate(args):
    if not await db_client.is_user_member_of_organization(
        args.user_id, args.organization_id
    ):
        raise ValueError("The user must belong to the selected organization")
    code = args.code.read_text()
    cases = json.loads(args.scenarios.read_text())
    if not isinstance(cases, list) or not 1 <= len(cases) <= 20:
        raise ValueError("Provide between one and twenty named scenarios")
    # Validate the entire set before spending credits.
    scenarios = [
        (case["name"], WorkflowScenario.model_validate(case["scenario"]))
        for case in cases
    ]
    reports = []
    for name, scenario in scenarios:
        report = await run_workflow_scenario(
            code, scenario, args.organization_id, str(args.user_id)
        )
        reports.append({"name": name, **report})
        # Save after each case, so interrupted evaluations retain completed results.
        args.output.write_text(
            json.dumps(
                {
                    "cases": reports,
                    "passed": sum(case.get("passed") is True for case in reports),
                    "total": len(reports),
                },
                indent=2,
            )
        )
    print(
        f"{sum(case.get('passed') is True for case in reports)}/{len(reports)} scenarios passed; report: {args.output}"
    )
    return all(case.get("passed") is True for case in reports)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", type=Path, required=True)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(0 if asyncio.run(evaluate(parser.parse_args())) else 1)


if __name__ == "__main__":
    main()
