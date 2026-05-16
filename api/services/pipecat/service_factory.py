from typing import TYPE_CHECKING

from fastapi import HTTPException
from loguru import logger

from api.constants import MPS_API_URL
from api.services.configuration.registry import ServiceProviders
from pipecat.services.assemblyai.stt import AssemblyAISTTService, AssemblyAISTTSettings
from pipecat.services.aws.llm import AWSBedrockLLMService, AWSBedrockLLMSettings
from pipecat.services.azure.llm import AzureLLMService, AzureLLMSettings
from pipecat.services.cartesia.stt import CartesiaSTTService
from pipecat.services.cartesia.tts import (
    CartesiaTTSService,
    CartesiaTTSSettings,
    GenerationConfig,
)
from pipecat.services.deepgram.flux.stt import (
    DeepgramFluxSTTService,
    DeepgramFluxSTTSettings,
)
from pipecat.services.deepgram.stt import DeepgramSTTService, DeepgramSTTSettings
from pipecat.services.deepgram.tts import DeepgramTTSService, DeepgramTTSSettings
from pipecat.services.dograh.llm import DograhLLMService
from pipecat.services.dograh.stt import DograhSTTService, DograhSTTSettings
from pipecat.services.dograh.tts import DograhTTSService, DograhTTSSettings
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService, ElevenLabsTTSSettings
from pipecat.services.gladia.stt import GladiaSTTService, GladiaSTTSettings
from pipecat.services.google.llm import GoogleLLMService, GoogleLLMSettings
from pipecat.services.groq.llm import GroqLLMService, GroqLLMSettings
from pipecat.services.openai.base_llm import OpenAILLMSettings
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import (
    OpenAISTTService,
    OpenAISTTSettings,
)
from pipecat.services.openai.tts import OpenAITTSService, OpenAITTSSettings
from pipecat.services.openrouter.llm import OpenRouterLLMService, OpenRouterLLMSettings
from pipecat.services.rime.tts import RimeTTSService, RimeTTSSettings
from pipecat.services.sarvam.stt import SarvamSTTService, SarvamSTTSettings
from pipecat.services.sarvam.tts import SarvamTTSService, SarvamTTSSettings
from pipecat.services.speaches.llm import SpeachesLLMService, SpeachesLLMSettings
from pipecat.services.speaches.stt import SpeachesSTTService, SpeachesSTTSettings
from pipecat.services.speaches.tts import SpeachesTTSService, SpeachesTTSSettings
from pipecat.services.speechmatics.stt import (
    SpeechmaticsSTTService,
    SpeechmaticsSTTSettings,
)
from pipecat.transcriptions.language import Language
from pipecat.utils.text.xml_function_tag_filter import XMLFunctionTagFilter

if TYPE_CHECKING:
    from api.services.pipecat.audio_config import AudioConfig


def create_stt_service(
    user_config, audio_config: "AudioConfig", keyterms: list[str] | None = None
):
    """Create and return appropriate STT service based on user configuration

    Args:
        user_config: User configuration containing STT settings
        keyterms: Optional list of keyterms for speech recognition boosting (Deepgram only)
    """
    logger.info(
        f"Creating STT service: provider={user_config.stt.provider}, model={user_config.stt.model}"
    )
    if user_config.stt.provider == ServiceProviders.DEEPGRAM.value:
        # Check if using Flux model (English-only, no language selection)
        if user_config.stt.model == "flux-general-en":
            return DeepgramFluxSTTService(
                api_key=user_config.stt.api_key,
                settings=DeepgramFluxSTTSettings(
                    model=user_config.stt.model,
                    eot_timeout_ms=3000,
                    eot_threshold=0.7,
                    eager_eot_threshold=0.5,
                    keyterm=keyterms or [],
                ),
                should_interrupt=False,  # Let UserAggregator take care of sending InterruptionFrame
                sample_rate=audio_config.transport_in_sample_rate,
            )

        # Other models than flux
        # Use language from user config, defaulting to "multi" for multilingual support
        language = getattr(user_config.stt, "language", None) or "multi"
        logger.debug(f"Using DeepGram Model - {user_config.stt.model}")
        return DeepgramSTTService(
            api_key=user_config.stt.api_key,
            settings=DeepgramSTTSettings(
                language=language,
                profanity_filter=False,
                endpointing=100,
                model=user_config.stt.model,
                keyterm=keyterms or [],
            ),
            should_interrupt=False,  # Let UserAggregator take care of sending InterruptionFrame
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.OPENAI.value:
        return OpenAISTTService(
            api_key=user_config.stt.api_key,
            settings=OpenAISTTSettings(model=user_config.stt.model),
        )
    elif user_config.stt.provider == ServiceProviders.CARTESIA.value:
        return CartesiaSTTService(
            api_key=user_config.stt.api_key,
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.DOGRAH.value:
        base_url = MPS_API_URL.replace("http://", "ws://").replace("https://", "wss://")
        language = getattr(user_config.stt, "language", None) or "multi"
        return DograhSTTService(
            base_url=base_url,
            api_key=user_config.stt.api_key,
            settings=DograhSTTSettings(
                model=user_config.stt.model,
                language=language,
            ),
            keyterms=keyterms,
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SARVAM.value:
        # Map Sarvam language code to pipecat Language enum
        language_mapping = {
            "bn-IN": Language.BN_IN,
            "gu-IN": Language.GU_IN,
            "hi-IN": Language.HI_IN,
            "kn-IN": Language.KN_IN,
            "ml-IN": Language.ML_IN,
            "mr-IN": Language.MR_IN,
            "ta-IN": Language.TA_IN,
            "te-IN": Language.TE_IN,
            "pa-IN": Language.PA_IN,
            "od-IN": Language.OR_IN,
            "en-IN": Language.EN_IN,
            "as-IN": Language.AS_IN,
        }
        language = getattr(user_config.stt, "language", None)
        pipecat_language = language_mapping.get(language, Language.HI_IN)
        return SarvamSTTService(
            api_key=user_config.stt.api_key,
            settings=SarvamSTTSettings(
                model=user_config.stt.model,
                language=pipecat_language,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SPEACHES.value:
        language = getattr(user_config.stt, "language", None)
        return SpeachesSTTService(
            base_url=user_config.stt.base_url,
            api_key=user_config.stt.api_key or "none",
            settings=SpeachesSTTSettings(
                model=user_config.stt.model,
                language=language,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.ASSEMBLYAI.value:
        language = getattr(user_config.stt, "language", None)
        settings_kwargs = {"model": user_config.stt.model, "language": language}
        if keyterms:
            settings_kwargs["keyterms_prompt"] = keyterms
        return AssemblyAISTTService(
            api_key=user_config.stt.api_key,
            settings=AssemblyAISTTSettings(**settings_kwargs),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.GLADIA.value:
        from pipecat.services.gladia.config import LanguageConfig

        language = getattr(user_config.stt, "language", None) or "en"
        settings_kwargs = {
            "model": user_config.stt.model,
            "language_config": LanguageConfig(
                languages=[language], code_switching=False
            ),
        }
        return GladiaSTTService(
            api_key=user_config.stt.api_key,
            settings=GladiaSTTSettings(**settings_kwargs),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SPEECHMATICS.value:
        from pipecat.services.speechmatics.stt import (
            AdditionalVocabEntry,
            OperatingPoint,
        )

        language = getattr(user_config.stt, "language", None) or "en"
        # Map model field to operating point (standard or enhanced)
        operating_point = (
            OperatingPoint.ENHANCED
            if user_config.stt.model == "enhanced"
            else OperatingPoint.STANDARD
        )
        # Convert keyterms to AdditionalVocabEntry objects for Speechmatics
        additional_vocab = []
        if keyterms:
            additional_vocab = [AdditionalVocabEntry(content=term) for term in keyterms]
        return SpeechmaticsSTTService(
            api_key=user_config.stt.api_key,
            settings=SpeechmaticsSTTSettings(
                language=language,
                operating_point=operating_point,
                additional_vocab=additional_vocab,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid STT provider {user_config.stt.provider}"
        )


def create_tts_service(user_config, audio_config: "AudioConfig"):
    """Create and return appropriate TTS service based on user configuration

    Args:
        user_config: User configuration containing TTS settings
        transport_type: Type of transport (e.g., 'twilio', 'webrtc')
    """
    logger.info(
        f"Creating TTS service: provider={user_config.tts.provider}, model={user_config.tts.model}"
    )
    # Create function call filter to prevent TTS from speaking function call tags
    xml_function_tag_filter = XMLFunctionTagFilter()
    if user_config.tts.provider == ServiceProviders.DEEPGRAM.value:
        return DeepgramTTSService(
            api_key=user_config.tts.api_key,
            settings=DeepgramTTSSettings(voice=user_config.tts.voice),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.OPENAI.value:
        return OpenAITTSService(
            api_key=user_config.tts.api_key,
            settings=OpenAITTSSettings(model=user_config.tts.model),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.ELEVENLABS.value:
        # Backward compatible with older configuration "Name - voice_id"
        try:
            voice_id = user_config.tts.voice.split(" - ")[1]
        except IndexError:
            voice_id = user_config.tts.voice
        # ElevenLabs TTS uses WebSocket. Users configure base_url with an HTTP
        # scheme (matching ElevenLabs documentation, e.g.
        # https://api.eu.residency.elevenlabs.io); rewrite it to the WS scheme.
        elevenlabs_url = user_config.tts.base_url.replace("https://", "wss://").replace(
            "http://", "ws://"
        )
        return ElevenLabsTTSService(
            reconnect_on_error=False,
            api_key=user_config.tts.api_key,
            url=elevenlabs_url,
            settings=ElevenLabsTTSSettings(
                voice=voice_id,
                model=user_config.tts.model,
                stability=0.8,
                speed=user_config.tts.speed,
                similarity_boost=0.75,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.CARTESIA.value:
        speed = getattr(user_config.tts, "speed", None)
        volume = getattr(user_config.tts, "volume", None)
        gen_config_kwargs = {}
        if speed and speed != 1.0:
            gen_config_kwargs["speed"] = speed
        if volume and volume != 1.0:
            gen_config_kwargs["volume"] = volume
        generation_config = (
            GenerationConfig(**gen_config_kwargs) if gen_config_kwargs else None
        )
        return CartesiaTTSService(
            api_key=user_config.tts.api_key,
            settings=CartesiaTTSSettings(
                voice=user_config.tts.voice,
                model=user_config.tts.model,
                **(
                    {"generation_config": generation_config}
                    if generation_config
                    else {}
                ),
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.DOGRAH.value:
        # Convert HTTP URL to WebSocket URL for TTS
        base_url = MPS_API_URL.replace("http://", "ws://").replace("https://", "wss://")
        return DograhTTSService(
            base_url=base_url,
            api_key=user_config.tts.api_key,
            settings=DograhTTSSettings(
                model=user_config.tts.model,
                voice=user_config.tts.voice,
                speed=user_config.tts.speed,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.CAMB.value:
        from pipecat.services.camb.tts import CambTTSService

        voice_id = int(getattr(user_config.tts, "voice", None) or "147320")
        language = getattr(user_config.tts, "language", None) or "en-us"
        tts = CambTTSService(
            api_key=user_config.tts.api_key,
            voice_id=voice_id,
            model=user_config.tts.model,
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
        )
        # Set language directly as BCP-47 code (bypasses Language enum conversion)
        tts._settings.language = language
        return tts
    elif user_config.tts.provider == ServiceProviders.SPEACHES.value:
        return SpeachesTTSService(
            base_url=user_config.tts.base_url,
            api_key=user_config.tts.api_key or "none",
            settings=SpeachesTTSSettings(
                model=user_config.tts.model,
                voice=user_config.tts.voice,
                speed=user_config.tts.speed,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.RIME.value:
        speed = getattr(user_config.tts, "speed", None)
        language_code = getattr(user_config.tts, "language", None) or "en"
        rime_language_mapping = {
            "en": Language.EN,
            "de": Language.DE,
            "fr": Language.FR,
            "es": Language.ES,
            "hi": Language.HI,
        }
        pipecat_language = rime_language_mapping.get(language_code, Language.EN)
        settings_kwargs = {
            "voice": user_config.tts.voice,
            "model": user_config.tts.model,
            "language": pipecat_language,
        }
        if speed and speed != 1.0:
            settings_kwargs["speedAlpha"] = speed
        return RimeTTSService(
            api_key=user_config.tts.api_key,
            settings=RimeTTSSettings(**settings_kwargs),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.SARVAM.value:
        # Map Sarvam language code to pipecat Language enum for TTS
        language_mapping = {
            "bn-IN": Language.BN,
            "en-IN": Language.EN,
            "gu-IN": Language.GU,
            "hi-IN": Language.HI,
            "kn-IN": Language.KN,
            "ml-IN": Language.ML,
            "mr-IN": Language.MR,
            "od-IN": Language.OR,
            "pa-IN": Language.PA,
            "ta-IN": Language.TA,
            "te-IN": Language.TE,
        }
        language = getattr(user_config.tts, "language", None)
        pipecat_language = language_mapping.get(language, Language.HI)

        voice = getattr(user_config.tts, "voice", None) or "anushka"
        return SarvamTTSService(
            api_key=user_config.tts.api_key,
            settings=SarvamTTSSettings(
                model=user_config.tts.model,
                voice=voice,
                language=pipecat_language,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid TTS provider {user_config.tts.provider}"
        )


def create_llm_service_from_provider(
    provider: str,
    model: str,
    api_key: str,
    *,
    base_url: str | None = None,
    endpoint: str | None = None,
    aws_access_key: str | None = None,
    aws_secret_key: str | None = None,
    aws_region: str | None = None,
):
    """Create an LLM service from explicit provider/model/api_key.

    Also used by create_llm_service which extracts these from user_config.
    """
    logger.info(f"Creating LLM service: provider={provider}, model={model}")
    if provider == ServiceProviders.OPENAI.value:
        if "gpt-5" in model:
            return OpenAILLMService(
                api_key=api_key,
                settings=OpenAILLMSettings(
                    model=model,
                    extra={"reasoning_effort": "minimal", "verbosity": "low"},
                ),
            )
        return OpenAILLMService(
            api_key=api_key,
            settings=OpenAILLMSettings(model=model, temperature=0.1),
        )
    elif provider == ServiceProviders.GROQ.value:
        return GroqLLMService(
            api_key=api_key,
            settings=GroqLLMSettings(model=model, temperature=0.1),
        )
    elif provider == ServiceProviders.OPENROUTER.value:
        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenRouterLLMService(
            api_key=api_key,
            settings=OpenRouterLLMSettings(model=model, temperature=0.1),
            **kwargs,
        )
    elif provider == ServiceProviders.GOOGLE.value:
        return GoogleLLMService(
            api_key=api_key,
            settings=GoogleLLMSettings(model=model, temperature=0.1),
        )
    elif provider == ServiceProviders.AZURE.value:
        return AzureLLMService(
            api_key=api_key,
            endpoint=endpoint,
            settings=AzureLLMSettings(model=model, temperature=0.1),
        )
    elif provider == ServiceProviders.DOGRAH.value:
        return DograhLLMService(
            base_url=f"{MPS_API_URL}/api/v1/llm",
            api_key=api_key,
            settings=OpenAILLMSettings(model=model),
        )
    elif provider == ServiceProviders.AWS_BEDROCK.value:
        return AWSBedrockLLMService(
            aws_access_key=aws_access_key,
            aws_secret_key=aws_secret_key,
            aws_region=aws_region,
            settings=AWSBedrockLLMSettings(model=model),
        )
    elif provider == ServiceProviders.SPEACHES.value:
        return SpeachesLLMService(
            base_url=base_url or "http://localhost:11434/v1",
            api_key=api_key or "none",
            settings=SpeachesLLMSettings(model=model),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Invalid LLM provider {provider}")


def create_realtime_llm_service(user_config, audio_config: "AudioConfig"):
    """Create a realtime (speech-to-speech) LLM service that handles STT+LLM+TTS.

    These services bypass separate STT/TTS and handle audio directly via
    a bidirectional WebSocket connection. Reads from user_config.realtime.
    """
    realtime_config = user_config.realtime
    provider = realtime_config.provider
    model = realtime_config.model
    api_key = realtime_config.api_key
    voice = getattr(realtime_config, "voice", None)
    language = getattr(realtime_config, "language", None)

    logger.info(
        f"Creating realtime LLM service: provider={provider}, model={model}, voice={voice}, language={language}"
    )

    if provider == ServiceProviders.OPENAI_REALTIME.value:
        from api.services.pipecat.realtime.openai_realtime import (
            DograhOpenAIRealtimeLLMService,
        )
        from pipecat.services.openai.realtime.events import (
            AudioConfiguration,
            AudioInput,
            AudioOutput,
            InputAudioTranscription,
            SessionProperties,
        )

        return DograhOpenAIRealtimeLLMService(
            api_key=api_key,
            settings=DograhOpenAIRealtimeLLMService.Settings(
                model=model,
                session_properties=SessionProperties(
                    audio=AudioConfiguration(
                        input=AudioInput(
                            transcription=InputAudioTranscription(),
                        ),
                        output=AudioOutput(
                            voice=voice or "alloy",
                        ),
                    ),
                ),
            ),
        )
    elif provider == ServiceProviders.GOOGLE_REALTIME.value:
        from api.services.pipecat.realtime.gemini_live import (
            DograhGeminiLiveLLMService,
        )

        # Gemini Live enables input/output audio transcription by default
        # in its _connect() method — no need to configure it explicitly.
        settings_kwargs = {
            "model": model,
            "voice": voice or "Puck",
        }
        if language:
            settings_kwargs["language"] = language
        return DograhGeminiLiveLLMService(
            api_key=api_key,
            settings=DograhGeminiLiveLLMService.Settings(**settings_kwargs),
        )
    elif provider == ServiceProviders.GOOGLE_VERTEX_REALTIME.value:
        from api.services.pipecat.realtime.gemini_live_vertex import (
            DograhGeminiLiveVertexLLMService,
        )

        project_id = getattr(realtime_config, "project_id", None)
        location = getattr(realtime_config, "location", None) or "us-east4"
        credentials = getattr(realtime_config, "credentials", None)

        settings_kwargs = {
            "model": model,
            "voice": voice or "Charon",
        }
        if language:
            settings_kwargs["language"] = language
        return DograhGeminiLiveVertexLLMService(
            credentials=credentials,
            project_id=project_id,
            location=location,
            settings=DograhGeminiLiveVertexLLMService.Settings(**settings_kwargs),
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid realtime LLM provider {provider}"
        )


def create_llm_service(user_config):
    """Create and return appropriate LLM service based on user configuration."""
    provider = user_config.llm.provider
    model = user_config.llm.model
    api_key = user_config.llm.api_key

    kwargs = {}
    if provider == ServiceProviders.OPENROUTER.value:
        kwargs["base_url"] = user_config.llm.base_url
    elif provider == ServiceProviders.AZURE.value:
        kwargs["endpoint"] = user_config.llm.endpoint
    elif provider == ServiceProviders.SPEACHES.value:
        kwargs["base_url"] = user_config.llm.base_url
    elif provider == ServiceProviders.AWS_BEDROCK.value:
        kwargs["aws_access_key"] = user_config.llm.aws_access_key
        kwargs["aws_secret_key"] = user_config.llm.aws_secret_key
        kwargs["aws_region"] = user_config.llm.aws_region

    return create_llm_service_from_provider(provider, model, api_key, **kwargs)
