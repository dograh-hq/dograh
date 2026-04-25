"""Golden-test parity for the display_options evaluator.

Both the Python `evaluate_display_options` and the TypeScript
`evaluateDisplayOptions` (in `ui/src/components/flow/renderer/displayOptions.ts`)
must agree on every fixture in `display_options_fixtures.json`. The TS
side is verified by `ui/scripts/test-display-options.mjs`.
"""

import json
from pathlib import Path

import pytest

from api.services.workflow.node_specs import evaluate_display_options

FIXTURES_PATH = (
    Path(__file__).parent.parent
    / "services"
    / "workflow"
    / "node_specs"
    / "display_options_fixtures.json"
)


def load_cases():
    with open(FIXTURES_PATH) as f:
        return json.load(f)["cases"]


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["name"])
def test_python_evaluator_matches_fixture(case):
    rules = case["rules"]
    values = case["values"]
    expected = case["expected"]
    actual = evaluate_display_options(rules, values)
    assert actual is expected, (
        f"{case['name']}: expected {expected}, got {actual} "
        f"for rules={rules!r} values={values!r}"
    )
