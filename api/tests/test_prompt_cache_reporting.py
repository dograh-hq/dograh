import csv
from types import SimpleNamespace

import pytest

from api.services.integrations.tuner.cost import _sum_llm_tokens
from api.services.reports.run_report import build_run_report_csv


@pytest.mark.parametrize(
    "llm,percentage,totals",
    [
        (
            {
                "model": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 1050,
                    "cache_read_input_tokens": 900,
                }
            },
            "90.0",
            (1000, 50, 900),
        ),
        (
            {"model": {"prompt_tokens": 200, "cache_read_input_tokens": 100}},
            "50.0",
            (200, 0, 100),
        ),
        ({"model": {"total_tokens": 0}}, "", (0, 0, 0)),
        (
            {
                "model": {
                    "prompt_tokens": 200,
                    "completion_tokens": 20,
                    "total_tokens": 0,
                    "cache_read_input_tokens": 100,
                }
            },
            "50.0",
            (200, 20, 100),
        ),
        (
            {
                "model": {
                    "prompt_tokens": 200,
                    "completion_tokens": 20,
                    "total_tokens": 10,
                    "cache_read_input_tokens": 100,
                }
            },
            "50.0",
            (200, 20, 100),
        ),
        ({"prompt_tokens": 200, "cache_read_input_tokens": 50}, "25.0", (200, 0, 50)),
        ({"invalid": 42, "model": None}, "", (0, 0, 0)),
    ],
)
def test_cache_report_and_tuner_use_consistent_gross_input(llm, percentage, totals):
    run = SimpleNamespace(
        id=1,
        campaign_id=None,
        workflow_id=2,
        definition_id=None,
        created_at=None,
        initial_context={},
        gathered_context={},
        public_access_token=None,
        usage_info={"llm": llm},
    )
    rows = list(csv.DictReader(build_run_report_csv([run])))
    assert rows[0]["LLM Cached Input (%)"] == percentage
    assert _sum_llm_tokens(run.usage_info) == totals
