from unittest.mock import AsyncMock

import pytest
from pipecat.utils.enums import EndTaskReason

from api.services.workflow.pipecat_engine_callbacks import create_max_duration_callback


@pytest.mark.asyncio
async def test_max_duration_callback_aborts_immediately():
    engine = AsyncMock()

    callback = create_max_duration_callback(engine)
    await callback()

    engine.end_call_with_reason.assert_awaited_once_with(
        EndTaskReason.CALL_DURATION_EXCEEDED.value,
        abort_immediately=True,
    )
