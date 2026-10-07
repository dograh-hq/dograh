"""WhatsApp transport factory — intentionally not a live code path.

``ProviderSpec.transport_factory`` is required by the registry because every
other provider works the same way: the carrier dials a Dograh WebSocket and
``run_pipeline_telephony`` builds a ``FastAPIWebsocketTransport`` around it.

WhatsApp does not work that way. Meta negotiates media as WebRTC, so both call
directions run ``run_pipeline_smallwebrtc`` over a ``SmallWebRTCConnection``
handed back by ``pipecat.transports.whatsapp.client`` — see
``providers/whatsapp/routes.py`` (inbound) and ``providers/whatsapp/service.py``
``register_outbound_active_connection`` (outbound). Nothing reaches this
factory.

It previously returned a real WebSocket transport wired to a WhatsApp frame
serializer with its own hangup and transfer strategies. That was a second,
never-executed implementation of call teardown sitting beside the real one, so
a fix aimed at teardown could plausibly land here and change nothing. The
serializer and strategies are gone; this raises instead, so that if the registry
contract ever does route a WhatsApp call through here it fails loudly rather
than silently building a transport Meta will never speak to.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - signature documentation only
    from fastapi import WebSocket

    from api.services.pipecat.audio_config import AudioConfig


async def create_transport(
    websocket: "WebSocket",
    workflow_run_id: int,
    audio_config: "AudioConfig",
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    is_realtime: bool = False,
    call_id: str,
):
    """Always raises: WhatsApp media is WebRTC, not a carrier WebSocket."""
    raise NotImplementedError(
        "WhatsApp calls do not use a WebSocket transport. Media is negotiated as "
        "WebRTC and the pipeline runs via run_pipeline_smallwebrtc over the "
        "SmallWebRTCConnection from the WhatsApp client. Reaching this factory "
        f"means a WhatsApp call (run {workflow_run_id}, call {call_id}) was "
        "routed through run_pipeline_telephony, which cannot work."
    )
