import os

from fastapi import WebSocket
from loguru import logger

from api.constants import APP_ROOT_DIR
from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_file_cache import get_cached_ambient_noise_path
from api.services.telephony.providers.ari_call_strategies import (
    ARIBridgeSwapStrategy,
    ARIHangupStrategy,
)
from api.services.telephony.providers.cloudonix_call_strategies import (
    CloudonixHangupStrategy,
)
from api.services.telephony.providers.twilio_call_strategies import (
    TwilioConferenceStrategy,
    TwilioHangupStrategy,
)
from pipecat.audio.mixers.silence_mixer import SilenceAudioMixer
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer
from pipecat.serializers.asterisk import AsteriskFrameSerializer
from pipecat.serializers.telnyx import TelnyxFrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.serializers.vobiz import VobizFrameSerializer
from pipecat.serializers.vonage import VonageFrameSerializer
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

librnnoise_path = os.path.normpath(
    str(APP_ROOT_DIR / "native" / "rnnoise" / "librnnoise.so")
)


async def _build_audio_out_mixer(
    audio_out_sample_rate: int,
    ambient_noise_config: dict | None,
):
    """Build the audio output mixer based on the ambient noise configuration.

    Returns a ``SoundfileMixer`` when ambient noise is enabled, or a
    ``SilenceAudioMixer`` otherwise.  Supports custom user-uploaded audio
    files via the ``storage_key`` / ``storage_backend`` fields in the config.
    """
    if not ambient_noise_config or not ambient_noise_config.get("enabled", False):
        return SilenceAudioMixer()

    volume = ambient_noise_config.get("volume", 0.3)

    # Check for a custom uploaded ambient noise file
    storage_key = ambient_noise_config.get("storage_key")
    storage_backend = ambient_noise_config.get("storage_backend")

    if storage_key and storage_backend:
        cached_path = await get_cached_ambient_noise_path(
            storage_key, storage_backend, audio_out_sample_rate
        )
        if cached_path:
            return SoundfileMixer(
                sound_files={"custom": cached_path},
                default_sound="custom",
                volume=volume,
            )
        logger.warning("Custom ambient noise file unavailable, falling back to default")

    # Default built-in office ambience
    return SoundfileMixer(
        sound_files={
            "office": APP_ROOT_DIR
            / "assets"
            / f"office-ambience-{audio_out_sample_rate}-mono.wav"
        },
        default_sound="office",
        volume=volume,
    )


async def create_twilio_transport(
    websocket_client: WebSocket,
    stream_sid: str,
    call_sid: str,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create a transport for Twilio connections"""

    # Fetch Twilio credentials from organization config
    config = await db_client.get_configuration(
        organization_id, OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value
    )

    if not config or not config.value:
        raise ValueError(
            f"Twilio credentials not configured for organization {organization_id}"
        )

    account_sid = config.value.get("account_sid")
    auth_token = config.value.get("auth_token")

    if not account_sid or not auth_token:
        raise ValueError(
            f"Incomplete Twilio configuration for organization {organization_id}"
        )
    # Create strategy instances
    transfer_strategy = TwilioConferenceStrategy()
    hangup_strategy = TwilioHangupStrategy()

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=account_sid,
        auth_token=auth_token,
        transfer_strategy=transfer_strategy,
        hangup_strategy=hangup_strategy,
    )

    mixer = await _build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    return FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            serializer=serializer,
        ),
    )


async def create_cloudonix_transport(
    websocket_client: WebSocket,
    call_id: str,
    stream_sid: str,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create a transport for Cloudonix connections"""

    # Load Cloudonix configuration from database
    from api.services.telephony.factory import load_telephony_config

    config = await load_telephony_config(organization_id)

    if config.get("provider") != "cloudonix":
        raise ValueError(f"Expected Cloudonix provider, got {config.get('provider')}")

    bearer_token = config.get("bearer_token")
    domain_id = config.get("domain_id")

    if not bearer_token or not domain_id:
        raise ValueError(
            f"Incomplete Cloudonix configuration for organization {organization_id}. "
            f"Required: bearer_token, domain_id"
        )

    from pipecat.serializers.cloudonix import CloudonixFrameSerializer

    hangup_strategy = CloudonixHangupStrategy()
    serializer = CloudonixFrameSerializer(
        call_id=call_id,
        stream_sid=stream_sid,
        domain_id=domain_id,
        bearer_token=bearer_token,
        hangup_strategy=hangup_strategy,
    )

    mixer = await _build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    return FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            serializer=serializer,
            audio_out_10ms_chunks=2,
        ),
    )


async def create_telnyx_transport(
    websocket_client: WebSocket,
    stream_id: str,
    call_control_id: str,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create a transport for Telnyx connections."""
    config = await db_client.get_configuration(
        organization_id, OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value
    )

    if not config or not config.value:
        raise ValueError(
            f"Telnyx credentials not configured for organization {organization_id}"
        )

    if config.value.get("provider") != "telnyx":
        raise ValueError(
            f"Expected Telnyx provider, got {config.value.get('provider')}"
        )

    api_key = config.value.get("api_key")
    if not api_key:
        raise ValueError(
            f"Incomplete Telnyx configuration for organization {organization_id}"
        )

    serializer = TelnyxFrameSerializer(
        stream_id=stream_id,
        call_control_id=call_control_id,
        api_key=api_key,
        outbound_encoding="PCMU",
        inbound_encoding="PCMU",
    )

    mixer = await _build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    return FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            serializer=serializer,
        ),
    )


async def create_ari_transport(
    websocket_client: WebSocket,
    channel_id: str,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create a transport for Asterisk ARI connections"""

    from api.services.telephony.factory import load_telephony_config

    config = await load_telephony_config(organization_id)

    if config.get("provider") != "ari":
        raise ValueError(f"Expected ARI provider, got {config.get('provider')}")

    ari_endpoint = config.get("ari_endpoint")
    app_name = config.get("app_name")
    app_password = config.get("app_password")

    if not ari_endpoint or not app_name or not app_password:
        raise ValueError(
            f"Incomplete ARI configuration for organization {organization_id}. "
            f"Required: ari_endpoint, app_name, app_password"
        )
    # Create strategy instances
    transfer_strategy = ARIBridgeSwapStrategy()
    hangup_strategy = ARIHangupStrategy()

    serializer = AsteriskFrameSerializer(
        channel_id=channel_id,
        ari_endpoint=ari_endpoint,
        app_name=app_name,
        app_password=app_password,
        transfer_strategy=transfer_strategy,
        hangup_strategy=hangup_strategy,
        params=AsteriskFrameSerializer.InputParams(
            asterisk_sample_rate=audio_config.transport_in_sample_rate,
            sample_rate=audio_config.pipeline_sample_rate,
        ),
    )

    mixer = await _build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    return FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            serializer=serializer,
        ),
    )


async def create_vonage_transport(
    websocket_client,
    call_uuid: str,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create a transport for Vonage connections"""

    # Use the factory to load config from database
    from api.services.telephony.factory import load_telephony_config

    config = await load_telephony_config(organization_id)

    if config.get("provider") != "vonage":
        raise ValueError(f"Expected Vonage provider, got {config.get('provider')}")

    application_id = config.get("application_id")
    private_key = config.get("private_key")

    if not application_id or not private_key:
        raise ValueError(
            f"Incomplete Vonage configuration for organization {organization_id}"
        )

    serializer = VonageFrameSerializer(
        call_uuid=call_uuid,
        application_id=application_id,
        private_key=private_key,
        params=VonageFrameSerializer.InputParams(
            vonage_sample_rate=audio_config.transport_in_sample_rate,
            sample_rate=audio_config.pipeline_sample_rate,
        ),
    )

    mixer = await _build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    # Important: Vonage uses binary WebSocket mode, not text
    return FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            serializer=serializer,
        ),
    )


async def create_vobiz_transport(
    websocket_client: WebSocket,
    stream_id: str,
    call_id: str,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create a transport for Vobiz connections.

    Vobiz uses Plivo-compatible WebSocket protocol:
    - MULAW audio at 8kHz (same as Twilio)
    - Base64-encoded audio in JSON messages
    - PlivoFrameSerializer handles the protocol
    """
    from loguru import logger

    logger.info(
        f"[run {workflow_run_id}] Creating Vobiz transport - "
        f"stream_id={stream_id}, call_id={call_id}"
    )

    # Load Vobiz configuration from database
    from api.services.telephony.factory import load_telephony_config

    config = await load_telephony_config(organization_id)

    if config.get("provider") != "vobiz":
        raise ValueError(f"Expected Vobiz provider, got {config.get('provider')}")

    auth_id = config.get("auth_id")
    auth_token = config.get("auth_token")

    if not auth_id or not auth_token:
        raise ValueError(
            f"Incomplete Vobiz configuration for organization {organization_id}"
        )

    logger.debug(
        f"[run {workflow_run_id}] Vobiz config loaded - auth_id={auth_id}, "
        f"from_numbers={len(config.get('from_numbers', []))} numbers"
    )

    # Use VobizFrameSerializer for Vobiz WebSocket protocol
    serializer = VobizFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        auth_id=auth_id,
        auth_token=auth_token,
        params=VobizFrameSerializer.InputParams(
            vobiz_sample_rate=8000,  # Vobiz uses MULAW at 8kHz
            sample_rate=audio_config.pipeline_sample_rate,
        ),
    )

    logger.debug(
        f"[run {workflow_run_id}] VobizFrameSerializer created for Vobiz - "
        f"transport_rate=8000Hz, pipeline_rate={audio_config.pipeline_sample_rate}Hz"
    )

    mixer = await _build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    # Create WebSocket transport (same structure as Twilio/Vonage)
    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            serializer=serializer,
        ),
    )

    logger.info(
        f"[run {workflow_run_id}] Vobiz transport created successfully (VAD enabled)"
    )
    return transport


async def create_webrtc_transport(
    webrtc_connection: SmallWebRTCConnection,
    workflow_run_id: int,
    audio_config: AudioConfig,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create a transport for WebRTC connections"""

    mixer = await _build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    return SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
        ),
    )


def create_internal_transport(
    workflow_run_id: int,
    audio_config: AudioConfig,
    latency_seconds: float = 0.0,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create an internal transport for agent-to-agent connections (LoopTalk).

    Args:
        workflow_run_id: ID of the workflow run for turn analyzer context
        audio_config: Audio configuration for the transport
        latency_seconds: Network latency to simulate

    Returns:
        InternalTransport instance configured with turn analyzer
    """
    pass
    # Commented out because looptalk coming in the regular import flow
    # was causing issue. May be move this to looptalk/orchestrator.py

    # Create and return the internal transport with latency
    # return InternalTransport(
    #     params=TransportParams(
    #         audio_out_enabled=True,
    #         audio_out_sample_rate=audio_config.transport_out_sample_rate,
    #         audio_out_channels=1,
    #         audio_in_enabled=True,
    #         audio_in_sample_rate=audio_config.transport_in_sample_rate,
    #         audio_in_channels=1,
    #         audio_out_mixer=(
    #             SoundfileMixer(
    #                 sound_files={
    #                     "office": APP_ROOT_DIR
    #                     / "assets"
    #                     / f"office-ambience-{audio_config.transport_out_sample_rate}-mono.wav"
    #                 },
    #                 default_sound="office",
    #                 volume=ambient_noise_config.get("volume", 0.3),
    #             )
    #             if ambient_noise_config and ambient_noise_config.get("enabled", False)
    #             else SilenceAudioMixer()
    #         ),
    #     ),
    #     latency_seconds=latency_seconds,
    # )
