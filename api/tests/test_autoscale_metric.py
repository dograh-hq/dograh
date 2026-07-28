"""Unit tests for the /health/autoscale-metric handler.

Verifies the pure signal arithmetic — value = fleet_active_calls + max(0, buffer)
— and the Redis-failure 503, with the fleet count and devops-secret gate mocked,
so no Redis is needed.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.main import autoscale_metric


@pytest.mark.parametrize(
    "fleet, buffer, expected",
    [
        (5, 0, 5),  # no buffer
        (5, 20, 25),  # buffer folds into the numerator
        (0, 10, 10),  # idle fleet still reports the buffer
        (5, -3, 5),  # negative buffer clamped to 0
    ],
)
@pytest.mark.asyncio
async def test_value_is_fleet_plus_clamped_buffer(fleet, buffer, expected):
    with (
        patch("api.routes.main._verify_devops_secret"),  # skip auth
        patch("api.services.call_concurrency.call_concurrency") as mock_cc,
    ):
        mock_cc.get_fleet_active_calls = AsyncMock(return_value=fleet)
        resp = await autoscale_metric(buffer=buffer, x_dograh_devops_secret="ok")
    assert resp.value == expected


@pytest.mark.asyncio
async def test_redis_failure_responds_503_not_zero():
    """A failed fleet read must fail the KEDA scrape (HPA holds replicas);
    responding 0 would instead instruct it to scale toward minReplicas."""
    with (
        patch("api.routes.main._verify_devops_secret"),  # skip auth
        patch("api.services.call_concurrency.call_concurrency") as mock_cc,
    ):
        mock_cc.get_fleet_active_calls = AsyncMock(
            side_effect=ConnectionError("redis down")
        )
        with pytest.raises(HTTPException) as exc_info:
            await autoscale_metric(buffer=0, x_dograh_devops_secret="ok")
    assert exc_info.value.status_code == 503
