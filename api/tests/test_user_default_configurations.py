"""Tests for backend-owned defaults exposed to generated clients."""

import pytest

from api.routes.user import get_default_configurations
from api.schemas.workflow_configurations import DEFAULT_CALL_DISPOSITION_OPTIONS


@pytest.mark.asyncio
async def test_default_configurations_expose_disposition_suggestions_without_enabling_them():
    response = await get_default_configurations()

    assert response.default_call_dispositions == list(DEFAULT_CALL_DISPOSITION_OPTIONS)
    assert response.workflow_configurations.call_dispositions == []
