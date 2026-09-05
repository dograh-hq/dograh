"""An offer naming a finished run is refused on the signalling channel.

The client fixes the run id from the page it was opened with, so re-offering
after a call ends asks to start a run that is already over. The pipeline's own
guard runs inside a detached task whose rejection reaches nobody, so the refusal
has to happen here — before a peer connection or a concurrency slot is taken.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from api.routes.webrtc_signaling import SignalingManager

_OFFER = {"pc_id": "PC-abc123", "sdp": "v=0\r\n", "type": "offer"}
_USER = SimpleNamespace(id=8267, provider_id="user_x")


def _quota_ok():
    return SimpleNamespace(has_quota=True, error_code=None, error_message=None)


def _run(*, is_completed: bool):
    return SimpleNamespace(is_completed=is_completed, initial_context={})


async def _offer(
    manager: SignalingManager,
    ws,
    run,
    *,
    enforce_call_concurrency: bool = False,
    concurrency=None,
) -> MagicMock:
    """Drive _handle_offer with the surrounding I/O stubbed, return the pc class."""
    with (
        patch(
            "api.routes.webrtc_signaling.call_concurrency",
            concurrency if concurrency is not None else MagicMock(),
        ),
        patch(
            "api.routes.webrtc_signaling.authorize_workflow_run_start",
            AsyncMock(return_value=_quota_ok()),
        ),
        patch(
            "api.routes.webrtc_signaling.db_client.get_workflow_run",
            AsyncMock(return_value=run),
        ),
        patch(
            "api.routes.webrtc_signaling.get_ice_servers", MagicMock(return_value=[])
        ),
        patch(
            "api.routes.webrtc_signaling.filter_outbound_sdp",
            MagicMock(side_effect=lambda sdp: sdp),
        ),
        patch("api.routes.webrtc_signaling.register_ws_sender", MagicMock()),
        patch(
            "api.routes.webrtc_signaling.run_pipeline_smallwebrtc",
            AsyncMock(return_value=None),
        ),
        patch("api.routes.webrtc_signaling.SmallWebRTCConnection") as pc_cls,
    ):
        pc_cls.return_value.initialize = AsyncMock()
        pc_cls.return_value.get_answer = MagicMock(
            return_value={"sdp": "v=0\r\n", "type": "answer", "pc_id": _OFFER["pc_id"]}
        )
        await manager._handle_offer(
            ws,
            dict(_OFFER),
            10124,
            630140,
            _USER,
            organization_id=1,
            connection_key="10124:630140:8267:1",
            enforce_call_concurrency=enforce_call_concurrency,
        )
    return pc_cls


def _sent(ws) -> list[dict]:
    return [call.args[0] for call in ws.send_json.await_args_list]


async def test_completed_run_is_refused_without_building_a_peer_connection():
    manager = SignalingManager()
    ws = AsyncMock()

    pc_cls = await _offer(manager, ws, _run(is_completed=True))

    pc_cls.assert_not_called()
    assert manager._pipeline_tasks == set()
    assert manager._peer_connections == {}
    messages = _sent(ws)
    assert [m["type"] for m in messages] == ["error"]
    assert messages[0]["payload"]["error_type"] == "workflow_run_already_completed"


async def test_live_run_still_starts_its_pipeline():
    """The guard must not stand between a fresh run and its call."""
    manager = SignalingManager()
    ws = AsyncMock()

    pc_cls = await _offer(manager, ws, _run(is_completed=False))

    try:
        pc_cls.assert_called_once()
        assert len(manager._pipeline_tasks) == 1
        assert [m["type"] for m in _sent(ws)] == ["answer"]
    finally:
        for task in list(manager._pipeline_tasks):
            task.cancel()


async def test_completed_run_is_refused_before_taking_a_concurrency_slot():
    """Ordering matters: a call that cannot happen must not consume org capacity."""
    manager = SignalingManager()
    ws = AsyncMock()
    concurrency = MagicMock()
    concurrency.acquire_org_slot = AsyncMock()
    concurrency.bind_workflow_run = AsyncMock()

    await _offer(
        manager,
        ws,
        _run(is_completed=True),
        enforce_call_concurrency=True,
        concurrency=concurrency,
    )

    concurrency.acquire_org_slot.assert_not_called()
    concurrency.bind_workflow_run.assert_not_called()
    assert _sent(ws)[0]["payload"]["error_type"] == "workflow_run_already_completed"


async def test_missing_run_is_left_to_the_pipeline():
    """A vanished run is not the case this guard owns; behaviour is unchanged."""
    manager = SignalingManager()
    ws = AsyncMock()

    pc_cls = await _offer(manager, ws, None)

    try:
        pc_cls.assert_called_once()
        assert [m["type"] for m in _sent(ws)] == ["answer"]
    finally:
        for task in list(manager._pipeline_tasks):
            task.cancel()
