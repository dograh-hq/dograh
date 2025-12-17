import os

from fastapi import WebSocket

from api.constants import APP_ROOT_DIR, ENABLE_RNNOISE, ENABLE_SMART_TURN
from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.looptalk.internal_transport import InternalTransport
from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.stasis_rtp_connection import StasisRTPConnection
from api.services.telephony.stasis_rtp_serializer import StasisRTPFrameSerializer
from api.services.telephony.stasis_rtp_transport import (
    StasisRTPTransport,
    StasisRTPTransportParams,
)
from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter
from pipecat.audio.mixers.silence_mixer import SilenceAudioMixer
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer, VADParams
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


def create_turn_analyzer(workflow_run_id: int, audio_config: AudioConfig):
    """Create a turn analyzer backed by the local Smart Turn HTTP service.

    Args:
        workflow_run_id: ID of the workflow run for turn analyzer context
        audio_config: Audio configuration containing pipeline sample rate
    """
    if ENABLE_SMART_TURN:
        return LocalSmartTurnAnalyzerV3(params=SmartTurnParams())

    return None


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

    turn_analyzer = create_turn_analyzer(workflow_run_id, audio_config)

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=account_sid,
        auth_token=auth_token,
    )

    return FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            vad_analyzer=(
                SileroVADAnalyzer(
                    params=VADParams(
                        confidence=vad_config.get("confidence", 0.7),
                        start_secs=vad_config.get("start_seconds", 0.4),
                        stop_secs=vad_config.get("stop_seconds", 0.8),
                        min_volume=vad_config.get("minimum_volume", 0.6),
                    )
                )
                if vad_config
                else SileroVADAnalyzer()
            ),  # Sample rate will be set by transport
            audio_out_mixer=(
                SoundfileMixer(
                    sound_files={
                        "office": APP_ROOT_DIR
                        / "assets"
                        / f"office-ambience-{audio_config.transport_out_sample_rate}-mono.wav"
                    },
                    default_sound="office",
                    volume=ambient_noise_config.get("volume", 0.3),
                )
                if ambient_noise_config and ambient_noise_config.get("enabled", False)
                else SilenceAudioMixer()
            ),
            turn_analyzer=turn_analyzer,
            serializer=serializer,
            audio_in_filter=RNNoiseFilter(library_path=librnnoise_path)
            if ENABLE_RNNOISE
            else None,
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

    turn_analyzer = create_turn_analyzer(workflow_run_id, audio_config)

    serializer = VonageFrameSerializer(
        call_uuid=call_uuid,
        application_id=application_id,
        private_key=private_key,
        params=VonageFrameSerializer.InputParams(
            vonage_sample_rate=audio_config.transport_in_sample_rate,
            sample_rate=audio_config.pipeline_sample_rate,
        ),
    )

    # Important: Vonage uses binary WebSocket mode, not text
    return FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            vad_analyzer=(
                SileroVADAnalyzer(
                    params=VADParams(
                        confidence=vad_config.get("confidence", 0.7),
                        start_secs=vad_config.get("start_seconds", 0.4),
                        stop_secs=vad_config.get("stop_seconds", 0.8),
                        min_volume=vad_config.get("minimum_volume", 0.6),
                    )
                )
                if vad_config
                else SileroVADAnalyzer()
            ),
            audio_out_mixer=(
                SoundfileMixer(
                    sound_files={
                        "office": APP_ROOT_DIR
                        / "assets"
                        / f"office-ambience-{audio_config.transport_out_sample_rate}-mono.wav"
                    },
                    default_sound="office",
                    volume=ambient_noise_config.get("volume", 0.3),
                )
                if ambient_noise_config and ambient_noise_config.get("enabled", False)
                else SilenceAudioMixer()
            ),
            turn_analyzer=turn_analyzer,
            serializer=serializer,
            audio_in_filter=RNNoiseFilter(library_path=librnnoise_path)
            if ENABLE_RNNOISE
            else None,
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

    turn_analyzer = create_turn_analyzer(workflow_run_id, audio_config)

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

    # Create WebSocket transport (same structure as Twilio/Vonage)
    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            vad_analyzer=(
                SileroVADAnalyzer(
                    params=VADParams(
                        confidence=vad_config.get("confidence", 0.7),
                        start_secs=vad_config.get("start_seconds", 0.4),
                        stop_secs=vad_config.get("stop_seconds", 0.8),
                        min_volume=vad_config.get("minimum_volume", 0.6),
                    )
                )
                if vad_config
                else SileroVADAnalyzer()
            ),
            audio_out_mixer=(
                SoundfileMixer(
                    sound_files={
                        "office": APP_ROOT_DIR
                        / "assets"
                        / f"office-ambience-{audio_config.transport_out_sample_rate}-mono.wav"
                    },
                    default_sound="office",
                    volume=ambient_noise_config.get("volume", 0.3),
                )
                if ambient_noise_config and ambient_noise_config.get("enabled", False)
                else SilenceAudioMixer()
            ),
            turn_analyzer=turn_analyzer,
            serializer=serializer,
            audio_in_filter=RNNoiseFilter(library_path=librnnoise_path)
            if ENABLE_RNNOISE
            else None,
        ),
    )

    logger.info(
        f"[run {workflow_run_id}] Vobiz transport created successfully (VAD enabled)"
    )
    return transport


def create_webrtc_transport(
    webrtc_connection: SmallWebRTCConnection,
    workflow_run_id: int,
    audio_config: AudioConfig,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create a transport for WebRTC connections"""
    turn_analyzer = create_turn_analyzer(workflow_run_id, audio_config)

    return SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            vad_analyzer=(
                SileroVADAnalyzer(
                    params=VADParams(
                        confidence=vad_config.get("confidence", 0.7),
                        start_secs=vad_config.get("start_seconds", 0.4),
                        stop_secs=vad_config.get("stop_seconds", 0.8),
                        min_volume=vad_config.get("minimum_volume", 0.6),
                    )
                )
                if vad_config
                else SileroVADAnalyzer()
            ),  # Sample rate will be set by transport
            audio_out_mixer=(
                SoundfileMixer(
                    sound_files={
                        "office": APP_ROOT_DIR
                        / "assets"
                        / f"office-ambience-{audio_config.transport_out_sample_rate}-mono.wav"
                    },
                    default_sound="office",
                    volume=ambient_noise_config.get("volume", 0.3),
                )
                if ambient_noise_config and ambient_noise_config.get("enabled", False)
                else SilenceAudioMixer()
            ),
            turn_analyzer=turn_analyzer,
            audio_in_filter=RNNoiseFilter(library_path=librnnoise_path)
            if ENABLE_RNNOISE
            else None,
        ),
    )


def create_stasis_transport(
    stasis_connection: StasisRTPConnection,
    workflow_run_id: int,
    audio_config: AudioConfig,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create a transport for ARI connections"""
    turn_analyzer = create_turn_analyzer(workflow_run_id, audio_config)

    serializer = StasisRTPFrameSerializer(
        StasisRTPFrameSerializer.InputParams(
            sample_rate=audio_config.transport_in_sample_rate
        )
    )

    return StasisRTPTransport(
        stasis_connection,
        params=StasisRTPTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_10ms_chunks=2,  # Send 20ms packets
            vad_analyzer=(
                SileroVADAnalyzer(
                    params=VADParams(
                        confidence=vad_config.get("confidence", 0.7),
                        start_secs=vad_config.get("start_seconds", 0.4),
                        stop_secs=vad_config.get("stop_seconds", 0.8),
                        min_volume=vad_config.get("minimum_volume", 0.6),
                    )
                )
                if vad_config
                else SileroVADAnalyzer()
            ),  # Sample rate will be set by transport
            audio_out_mixer=(
                SoundfileMixer(
                    sound_files={
                        "office": APP_ROOT_DIR
                        / "assets"
                        / f"office-ambience-{audio_config.transport_out_sample_rate}-mono.wav"
                    },
                    default_sound="office",
                    volume=ambient_noise_config.get("volume", 0.3),
                )
                if ambient_noise_config and ambient_noise_config.get("enabled", False)
                else SilenceAudioMixer()
            ),
            turn_analyzer=turn_analyzer,
            serializer=serializer,
            audio_in_filter=RNNoiseFilter(library_path=librnnoise_path)
            if ENABLE_RNNOISE
            else None,
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
    turn_analyzer = create_turn_analyzer(workflow_run_id, audio_config)

    # Create and return the internal transport with latency
    return InternalTransport(
        params=TransportParams(
            audio_out_enabled=True,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_channels=1,
            audio_in_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_in_channels=1,
            vad_analyzer=(
                SileroVADAnalyzer(
                    params=VADParams(
                        confidence=vad_config.get("confidence", 0.7),
                        start_secs=vad_config.get("start_seconds", 0.4),
                        stop_secs=vad_config.get("stop_seconds", 0.8),
                        min_volume=vad_config.get("minimum_volume", 0.6),
                    )
                )
                if vad_config
                else SileroVADAnalyzer()
            ),
            audio_out_mixer=(
                SoundfileMixer(
                    sound_files={
                        "office": APP_ROOT_DIR
                        / "assets"
                        / f"office-ambience-{audio_config.transport_out_sample_rate}-mono.wav"
                    },
                    default_sound="office",
                    volume=ambient_noise_config.get("volume", 0.3),
                )
                if ambient_noise_config and ambient_noise_config.get("enabled", False)
                else SilenceAudioMixer()
            ),
            turn_analyzer=turn_analyzer,
            audio_in_filter=RNNoiseFilter(library_path=librnnoise_path)
            if ENABLE_RNNOISE
            else None,
        ),
        latency_seconds=latency_seconds,
    )
