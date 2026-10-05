"""Omitted campaign rate limits must not become JSON null in the Python SDK."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_PY_SRC = REPO_ROOT / "sdk" / "python" / "src"
if str(SDK_PY_SRC) not in sys.path:
    sys.path.insert(0, str(SDK_PY_SRC))

from dograh_sdk._generated_models import CreateCampaignRequest  # noqa: E402


def test_omitted_rate_limit_dumps_as_one_not_null():
    body = CreateCampaignRequest(
        name="SDK campaign",
        workflow_id=1,
        source_type="csv",
        source_id="contacts.csv",
    )
    dumped = body.model_dump(mode="json")
    assert dumped["rate_limit_per_second"] == 1


def test_typescript_create_campaign_rate_limit_stays_optional():
    source = (
        REPO_ROOT / "sdk" / "typescript" / "src" / "_generated_models.ts"
    ).read_text()
    assert "rate_limit_per_second?: number;" in source
