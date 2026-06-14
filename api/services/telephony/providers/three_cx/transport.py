"""3CX transport factory.

3CX rides on top of an Asterisk bridge, so transport is wire-identical to
the ARI provider. The only difference is ``expected_provider="three_cx"``
so ``load_credentials_for_transport`` validates the right config type.
"""

from fastapi import WebSocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.pipecat.transport_params import realtime_param_overrides
from api.services.telephony.factory import load_credentials_for_transport

from .serializers import AsteriskFrameSerializer
from .strategies import ThreeCxBridgeSwapStrategy, ThreeCxHangupStrategy


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    is_realtime: bool = False,
    channel_id: str,
):
    """Create a transport for 3CX-via-Asterisk connections."""
    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="three_cx"
    )

    ari_endpoint = config.get("ari_endpoint")
    app_name = config.get("app_name")
    app_password = config.get("app_password")

    if not ari_endpoint or not app_name or not app_password:
        raise ValueError(
            f"Incomplete 3CX configuration for organization {organization_id}. "
            f"Required: ari_endpoint, app_name, app_password"
        )

    serializer = AsteriskFrameSerializer(
        channel_id=channel_id,
        ari_endpoint=ari_endpoint,
        app_name=app_name,
        app_password=app_password,
        transfer_strategy=ThreeCxBridgeSwapStrategy(),
        hangup_strategy=ThreeCxHangupStrategy(),
        params=AsteriskFrameSerializer.InputParams(
            asterisk_sample_rate=audio_config.transport_in_sample_rate,
            sample_rate=audio_config.pipeline_sample_rate,
        ),
    )

    mixer = await build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    return FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            serializer=serializer,
            **realtime_param_overrides(is_realtime),
        ),
    )
