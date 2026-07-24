"""Unit tests for the /health/autoscale-metric handler (AUTOSCALING_PLAN.md Phase 2).

Verifies the pure signal arithmetic — value = fleet_active_calls + max(0, buffer)
— with the fleet count and devops-secret gate mocked, so no Redis is needed.
"""

from unittest.mock import AsyncMock, patch

import pytest

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
        patch("api.services.campaign.rate_limiter.rate_limiter") as mock_rl,
    ):
        mock_rl.get_fleet_concurrent_count = AsyncMock(return_value=fleet)
        resp = await autoscale_metric(buffer=buffer, x_dograh_devops_secret="ok")
    assert resp.value == expected
