from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlparse, urlunparse

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.constants import MPS_API_URL
from api.services.configuration.options import (
    DEEPGRAM_FLUX_MODELS,
    DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGE_OPTIONS,
)
from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.gemini_json_schema_adapter import (
    DograhGeminiJSONSchemaAdapter,
)
from api.services.pipecat.minimax_tts import MiniMaxOwnedSessionTTSService
from api.utils.url_security import validate_user_configured_service_url
from pipecat.services.assemblyai.stt import AssemblyAISTTService, AssemblyAISTTSettings
from pipecat.services.aws.llm import AWSBedrockLLMService, AWSBedrockLLMSettings
from pipecat.services.azure.llm import AzureLLMService, AzureLLMSettings
from pipecat.services.azure.stt import AzureSTTService, AzureSTTSettings
from pipecat.services.azure.tts import AzureTTSService, AzureTTSSettings
from pipecat.services.cartesia.stt import CartesiaSTTService, CartesiaSTTSettings
from pipecat.services.cartesia.tts import (
    CartesiaTTSService,
    CartesiaTTSSettings,
    GenerationConfig,
)
from pipecat.services.cartesia.turns.stt import CartesiaTurnsSTTService
from pipecat.services.deepgram.flux.stt import (
    DeepgramFluxSTTService,
    DeepgramFluxSTTSettings,
)
from pipecat.services.deepgram.stt import DeepgramSTTService, DeepgramSTTSettings
from pipecat.services.deepgram.tts import DeepgramTTSService, DeepgramTTSSettings
from pipecat.services.dograh.flux.stt import DograhFluxSTTService
from pipecat.services.dograh.llm import DograhLLMService
from pipecat.services.dograh.stt import DograhSTTService, DograhSTTSettings
from pipecat.services.dograh.tts import DograhTTSService, DograhTTSSettings
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService, ElevenLabsTTSSettings
from pipecat.services.gladia.stt import GladiaSTTService, GladiaSTTSettings
from pipecat.services.google.llm import GoogleLLMService, GoogleLLMSettings
from pipecat.services.google.stt import GoogleSTTService, GoogleSTTSettings
from pipecat.services.google.tts import GoogleTTSService, GoogleTTSSettings
from pipecat.services.google.vertex.llm import (
    GoogleVertexLLMService,
    GoogleVertexLLMSettings,
)
from pipecat.services.groq.llm import GroqLLMService, GroqLLMSettings
from pipecat.services.huggingface.llm import (
    HuggingFaceLLMService,
    HuggingFaceLLMSettings,
)
from pipecat.services.huggingface.stt import (
    HuggingFaceSTTService,
    HuggingFaceSTTSettings,
)
from pipecat.services.inworld.tts import InworldTTSService, InworldTTSSettings
from pipecat.services.minimax.llm import MiniMaxLLMService
from pipecat.services.minimax.tts import MiniMaxTTSSettings
from pipecat.services.openai._constants import OPENAI_SAMPLE_RATE
from pipecat.services.openai.base_llm import OpenAILLMSettings
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import (
    OpenAISTTService,
    OpenAISTTSettings,
)
from pipecat.services.openai.tts import OpenAITTSService, OpenAITTSSettings
from pipecat.services.openrouter.llm import OpenRouterLLMService, OpenRouterLLMSettings
from pipecat.services.rime.tts import RimeTTSService, RimeTTSSettings
from pipecat.services.sarvam.llm import SarvamLLMService, SarvamLLMSettings
from pipecat.services.sarvam.stt import SarvamSTTService, SarvamSTTSettings
from pipecat.services.sarvam.tts import SarvamTTSService, SarvamTTSSettings
from pipecat.services.smallest.stt import SmallestSTTService, SmallestSTTSettings
from pipecat.services.smallest.tts import SmallestTTSService, SmallestTTSSettings
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


DEEPGRAM_FLUX_LANGUAGE_HINTS = {
    "de": Language.DE,
    "en": Language.EN,
    "es": Language.ES,
    "fr": Language.FR,
    "hi": Language.HI,
    "it": Language.IT,
    "ja": Language.JA,
    "nl": Language.NL,
    "pt": Language.PT,
    "ru": Language.RU,
}


def dograh_stt_uses_flux_language(language: str | None) -> bool:
    language = language or "multi"
    return language in DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGE_OPTIONS


def stt_uses_external_turns(user_config) -> bool:
    if user_config.stt.provider == ServiceProviders.DEEPGRAM.value:
        return user_config.stt.model in DEEPGRAM_FLUX_MODELS
    if user_config.stt.provider == ServiceProviders.DOGRAH.value:
        return dograh_stt_uses_flux_language(getattr(user_config.stt, "language", None))
    if user_config.stt.provider == ServiceProviders.CARTESIA.value:
        return user_config.stt.model == "ink-2"
    return False


class DograhGoogleLLMService(GoogleLLMService):
    adapter_class = DograhGeminiJSONSchemaAdapter


class DograhGoogleVertexLLMService(GoogleVertexLLMService):
    adapter_class = DograhGeminiJSONSchemaAdapter


def _validate_runtime_service_url(url: str, field_name: str) -> None:
    try:
        validate_user_configured_service_url(
            url,
            field_name=field_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def create_stt_service(
    user_config,
    audio_config: "AudioConfig",
    keyterms: list[str] | None = None,
    correlation_id: str | None = None,
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
        if user_config.stt.model in DEEPGRAM_FLUX_MODELS:
            settings_kwargs = {
                "model": user_config.stt.model,
                "eot_timeout_ms": 3000,
                "eot_threshold": 0.7,
                "eager_eot_threshold": 0.5,
                "keyterm": keyterms or [],
            }
            if user_config.stt.model == "flux-general-multi":
                language = getattr(user_config.stt, "language", None)
                language_hint = DEEPGRAM_FLUX_LANGUAGE_HINTS.get(language)
                if language_hint:
                    settings_kwargs["language_hints"] = [language_hint]

            return DeepgramFluxSTTService(
                api_key=user_config.stt.api_key,
                settings=DeepgramFluxSTTSettings(**settings_kwargs),
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
        kwargs = {}
        base_url = getattr(user_config.stt, "base_url", None)
        if base_url:
            _validate_runtime_service_url(base_url, "base_url")
            kwargs["base_url"] = base_url
        return OpenAISTTService(
            api_key=user_config.stt.api_key,
            settings=OpenAISTTSettings(model=user_config.stt.model),
            **kwargs,
        )
    elif user_config.stt.provider == ServiceProviders.GOOGLE.value:
        language = getattr(user_config.stt, "language", None) or "en-US"
        location = getattr(user_config.stt, "location", None) or "global"
        credentials = getattr(user_config.stt, "credentials", None)

        settings_kwargs = {"model": user_config.stt.model}
        try:
            settings_kwargs["languages"] = [Language(language)]
        except ValueError:
            settings_kwargs["language_codes"] = [language]

        return GoogleSTTService(
            credentials=credentials,
            location=location,
            settings=GoogleSTTSettings(**settings_kwargs),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.CARTESIA.value:
        if user_config.stt.model == "ink-2":
            return CartesiaTurnsSTTService(
                api_key=user_config.stt.api_key,
                should_interrupt=False,  # Let UserAggregator emit interruption frames.
                sample_rate=audio_config.transport_in_sample_rate,
            )

        language = getattr(user_config.stt, "language", None) or "en"
        return CartesiaSTTService(
            api_key=user_config.stt.api_key,
            settings=CartesiaSTTSettings(
                model=user_config.stt.model,
                language=language,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.DOGRAH.value:
        base_url = MPS_API_URL.replace("http://", "ws://").replace("https://", "wss://")
        language = getattr(user_config.stt, "language", None) or "multi"

        if dograh_stt_uses_flux_language(language):
            # Dograh's Flux proxy only supports multilingual auto-detect and the
            # same language hint subset as Deepgram Flux multilingual.
            settings_kwargs = {
                "model": "flux-general-multi",
                "eot_timeout_ms": 3000,
                "eot_threshold": 0.7,
                "eager_eot_threshold": 0.5,
                "keyterm": keyterms or [],
            }
            language_hint = DEEPGRAM_FLUX_LANGUAGE_HINTS.get(language)
            if language_hint:
                settings_kwargs["language_hints"] = [language_hint]
            return DograhFluxSTTService(
                base_url=base_url,
                api_key=user_config.stt.api_key,
                correlation_id=correlation_id,
                settings=DeepgramFluxSTTSettings(**settings_kwargs),
                should_interrupt=False,  # external turn strategies own interruption
                sample_rate=audio_config.transport_in_sample_rate,
            )

        return DograhSTTService(
            base_url=base_url,
            api_key=user_config.stt.api_key,
            correlation_id=correlation_id,
            settings=DograhSTTSettings(
                model=user_config.stt.model,
                language=language,
            ),
            keyterms=keyterms,
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SARVAM.value:
        language = getattr(user_config.stt, "language", None)
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
            "ur-IN": Language.UR_IN,
            "kok-IN": Language.KOK_IN,
            "mai-IN": Language.MAI_IN,
            "sd-IN": Language.SD_IN,
        }
        if not language or language == "unknown":
            pipecat_language = None
        elif language in language_mapping:
            pipecat_language = language_mapping[language]
        else:
            # Unmapped BCP-47 codes pass through; Sarvam accepts them per https://docs.sarvam.ai/api-reference-docs/speech-to-text/transcribe
            pipecat_language = language
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
        _validate_runtime_service_url(user_config.stt.base_url, "base_url")
        return SpeachesSTTService(
            base_url=user_config.stt.base_url,
            api_key=user_config.stt.api_key or "none",
            settings=SpeachesSTTSettings(
                model=user_config.stt.model,
                language=language,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.HUGGINGFACE.value:
        base_url = (
            getattr(user_config.stt, "base_url", None)
            or "https://router.huggingface.co/hf-inference"
        )
        _validate_runtime_service_url(base_url, "base_url")
        return HuggingFaceSTTService(
            api_key=user_config.stt.api_key,
            base_url=base_url,
            bill_to=getattr(user_config.stt, "bill_to", None),
            settings=HuggingFaceSTTSettings(
                model=user_config.stt.model,
                return_timestamps=getattr(user_config.stt, "return_timestamps", False),
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
    elif user_config.stt.provider == ServiceProviders.AZURE_SPEECH.value:
        from pipecat.transcriptions.language import Language as PipecatLanguage

        language_code = getattr(user_config.stt, "language", None) or "en-US"
        region = getattr(user_config.stt, "region", None) or "eastus"
        try:
            pipecat_language = PipecatLanguage(language_code)
        except ValueError:
            pipecat_language = language_code
        return AzureSTTService(
            api_key=user_config.stt.api_key,
            region=region,
            settings=AzureSTTSettings(language=pipecat_language),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SMALLEST.value:
        language_code = getattr(user_config.stt, "language", None) or "en"
        try:
            pipecat_language = Language(language_code)
        except ValueError:
            pipecat_language = Language.EN
        return SmallestSTTService(
            api_key=user_config.stt.api_key,
            settings=SmallestSTTSettings(
                model=user_config.stt.model,
                language=pipecat_language,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid STT provider {user_config.stt.provider}"
        )


def create_tts_service(
    user_config, audio_config: "AudioConfig", correlation_id: str | None = None
):
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
        kwargs = {}
        base_url = getattr(user_config.tts, "base_url", None)
        if base_url:
            _validate_runtime_service_url(base_url, "base_url")
            kwargs["base_url"] = base_url
        return OpenAITTSService(
            api_key=user_config.tts.api_key,
            sample_rate=OPENAI_SAMPLE_RATE,
            settings=OpenAITTSSettings(model=user_config.tts.model),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
            **kwargs,
        )
    elif user_config.tts.provider == ServiceProviders.GOOGLE.value:
        model = getattr(user_config.tts, "model", None) or "chirp_3_hd"
        language = getattr(user_config.tts, "language", None) or "en-US"
        voice = getattr(user_config.tts, "voice", None) or "en-US-Chirp3-HD-Charon"
        speed = getattr(user_config.tts, "speed", None)
        location = getattr(user_config.tts, "location", None) or None
        credentials = getattr(user_config.tts, "credentials", None)

        settings_kwargs = {
            "model": model,
            "voice": voice,
            "language": language,
        }
        if speed is not None and speed != 1.0:
            settings_kwargs["speaking_rate"] = speed

        return GoogleTTSService(
            credentials=credentials,
            location=location,
            settings=GoogleTTSSettings(**settings_kwargs),
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
        _validate_runtime_service_url(user_config.tts.base_url, "base_url")
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
        language = getattr(user_config.tts, "language", None) or "en"
        return CartesiaTTSService(
            api_key=user_config.tts.api_key,
            settings=CartesiaTTSSettings(
                voice=user_config.tts.voice,
                model=user_config.tts.model,
                language=language,
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
    elif user_config.tts.provider == ServiceProviders.INWORLD.value:
        voice = getattr(user_config.tts, "voice", None) or "Ashley"
        model = getattr(user_config.tts, "model", None) or "inworld-tts-2"
        speed = getattr(user_config.tts, "speed", None)
        language = getattr(user_config.tts, "language", None) or "en-US"
        delivery_mode = getattr(user_config.tts, "delivery_mode", None) or "BALANCED"
        return InworldTTSService(
            api_key=user_config.tts.api_key,
            settings=InworldTTSSettings(
                voice=voice,
                model=model,
                language=language,
                speaking_rate=speed,
                delivery_mode=delivery_mode,
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
            correlation_id=correlation_id,
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
        _validate_runtime_service_url(user_config.tts.base_url, "base_url")
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

        voice = (
            getattr(user_config.tts, "voice", None) or ""
        ).strip().lower() or "anushka"
        speed = getattr(user_config.tts, "speed", None)
        settings_kwargs = {
            "model": user_config.tts.model,
            "voice": voice,
            "language": pipecat_language,
        }
        if speed and speed != 1.0:
            settings_kwargs["pace"] = speed
        return SarvamTTSService(
            api_key=user_config.tts.api_key,
            settings=SarvamTTSSettings(**settings_kwargs),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.MINIMAX.value:
        group_id = getattr(user_config.tts, "group_id", None)
        if not group_id:
            raise HTTPException(
                status_code=400,
                detail="MiniMax TTS requires a group_id. Configure it in your TTS settings.",
            )
        voice = getattr(user_config.tts, "voice", None) or "English_Graceful_Lady"
        speed = getattr(user_config.tts, "speed", None) or 1.0

        # Pipecat appends "?GroupId=..." to base_url as-is, so /t2a_v2 must
        # already be in the path.
        base_url = (
            getattr(user_config.tts, "base_url", None)
            or "https://api.minimax.io/v1/t2a_v2"
        ).rstrip("/")
        if not base_url.endswith("/t2a_v2"):
            base_url = f"{base_url}/t2a_v2"
        _validate_runtime_service_url(base_url, "base_url")

        session = aiohttp.ClientSession()
        return MiniMaxOwnedSessionTTSService(
            api_key=user_config.tts.api_key,
            group_id=group_id,
            base_url=base_url,
            aiohttp_session=session,
            settings=MiniMaxTTSSettings(
                model=user_config.tts.model,
                voice=voice,
                speed=speed,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.AZURE_SPEECH.value:
        region = getattr(user_config.tts, "region", None) or "eastus"
        voice = getattr(user_config.tts, "voice", None) or "en-US-AriaNeural"
        language = getattr(user_config.tts, "language", None) or "en-US"
        speed = getattr(user_config.tts, "speed", None) or 1.0
        # Map speed multiplier (0.5–2.0) to Azure SSML rate string (e.g. "1.25")
        rate = str(speed) if speed != 1.0 else None
        settings_kwargs: dict = {
            "voice": voice,
            "language": language,
        }
        if rate:
            settings_kwargs["rate"] = rate
        return AzureTTSService(
            api_key=user_config.tts.api_key,
            region=region,
            settings=AzureTTSSettings(**settings_kwargs),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.SMALLEST.value:
        language_code = getattr(user_config.tts, "language", None) or "en"
        try:
            pipecat_language = Language(language_code)
        except ValueError:
            pipecat_language = Language.EN
        speed = getattr(user_config.tts, "speed", None)
        model = user_config.tts.model.replace("lightning-v", "lightning_v")
        settings_kwargs = SmallestTTSSettings(
            model=model,
            voice=user_config.tts.voice,
            language=pipecat_language,
        )
        if speed and speed != 1.0:
            settings_kwargs.speed = speed
        return SmallestTTSService(
            api_key=user_config.tts.api_key,
            settings=settings_kwargs,
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid TTS provider {user_config.tts.provider}"
        )


def _migrate_deprecated_google_model(model: str) -> str:
    """Google removed the ``gemini-2.0-flash*`` models. Transparently upgrade
    any stored config that still references them to the 2.5 equivalent so old
    user configurations keep working instead of failing at runtime."""
    if model and model.startswith("gemini-2.0-flash"):
        migrated = model.replace("gemini-2.0-", "gemini-2.5-", 1)
        logger.warning(
            f"Google model '{model}' is no longer supported; using '{migrated}' instead"
        )
        return migrated
    return model


def create_llm_service_from_provider(
    provider: str,
    model: str,
    api_key: str | None,
    *,
    correlation_id: str | None = None,
    base_url: str | None = None,
    endpoint: str | None = None,
    aws_access_key: str | None = None,
    aws_secret_key: str | None = None,
    aws_region: str | None = None,
    project_id: str | None = None,
    location: str | None = None,
    credentials: str | None = None,
    temperature: float | None = None,
    bill_to: str | None = None,
):
    """Create an LLM service from explicit provider/model/api_key.

    Also used by create_llm_service which extracts these from user_config.
    """
    logger.info(f"Creating LLM service: provider={provider}, model={model}")
    if provider == ServiceProviders.OPENAI.value:
        kwargs = {}
        if base_url:
            _validate_runtime_service_url(base_url, "base_url")
            kwargs["base_url"] = base_url
        if "gpt-5" in model:
            return OpenAILLMService(
                api_key=api_key,
                settings=OpenAILLMSettings(
                    model=model,
                    extra={"reasoning_effort": "minimal", "verbosity": "low"},
                ),
                **kwargs,
            )
        return OpenAILLMService(
            api_key=api_key,
            settings=OpenAILLMSettings(model=model, temperature=0.1),
            **kwargs,
        )
    elif provider == ServiceProviders.GROQ.value:
        return GroqLLMService(
            api_key=api_key,
            settings=GroqLLMSettings(model=model, temperature=0.1),
        )
    elif provider == ServiceProviders.OPENROUTER.value:
        kwargs = {}
        if base_url:
            _validate_runtime_service_url(base_url, "base_url")
            kwargs["base_url"] = base_url
        return OpenRouterLLMService(
            api_key=api_key,
            settings=OpenRouterLLMSettings(model=model, temperature=0.1),
            **kwargs,
        )
    elif provider == ServiceProviders.GOOGLE.value:
        model = _migrate_deprecated_google_model(model)
        return DograhGoogleLLMService(
            api_key=api_key,
            settings=GoogleLLMSettings(model=model, temperature=0.1),
        )
    elif provider == ServiceProviders.GOOGLE_VERTEX.value:
        return DograhGoogleVertexLLMService(
            credentials=credentials,
            project_id=project_id,
            location=location or "us-east4",
            settings=GoogleVertexLLMSettings(model=model, temperature=0.1),
        )
    elif provider == ServiceProviders.AZURE.value:
        if endpoint:
            _validate_runtime_service_url(endpoint, "endpoint")
        return AzureLLMService(
            api_key=api_key,
            endpoint=endpoint,
            settings=AzureLLMSettings(model=model, temperature=0.1),
        )
    elif provider == ServiceProviders.DOGRAH.value:
        return DograhLLMService(
            base_url=f"{MPS_API_URL}/api/v1/llm",
            api_key=api_key,
            correlation_id=correlation_id,
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
        base_url = base_url or "http://localhost:11434/v1"
        _validate_runtime_service_url(base_url, "base_url")
        return SpeachesLLMService(
            base_url=base_url,
            api_key=api_key or "none",
            settings=SpeachesLLMSettings(model=model),
        )
    elif provider == ServiceProviders.HUGGINGFACE.value:
        base_url = base_url or "https://router.huggingface.co/v1"
        _validate_runtime_service_url(base_url, "base_url")
        return HuggingFaceLLMService(
            api_key=api_key,
            base_url=base_url,
            bill_to=bill_to,
            settings=HuggingFaceLLMSettings(model=model, temperature=0.1),
        )
    elif provider == ServiceProviders.MINIMAX.value:
        base_url = base_url or "https://api.minimax.io/v1"
        _validate_runtime_service_url(base_url, "base_url")
        return MiniMaxLLMService(
            api_key=api_key,
            base_url=base_url,
            settings=MiniMaxLLMService.Settings(
                model=model,
                temperature=temperature if temperature is not None else 1.0,
            ),
        )
    elif provider == ServiceProviders.SARVAM.value:
        return SarvamLLMService(
            api_key=api_key,
            settings=SarvamLLMSettings(
                model=model,
                temperature=temperature if temperature is not None else 0.5,
            ),
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
    elif provider == ServiceProviders.GROK_REALTIME.value:
        from api.services.pipecat.realtime.grok_realtime import (
            DograhGrokRealtimeLLMService,
        )
        from pipecat.services.xai.realtime.events import SessionProperties

        return DograhGrokRealtimeLLMService(
            api_key=api_key,
            settings=DograhGrokRealtimeLLMService.Settings(
                model=model,
                session_properties=SessionProperties(
                    voice=voice or "Ara",
                ),
            ),
        )
    elif provider == ServiceProviders.ULTRAVOX_REALTIME.value:
        from api.services.pipecat.realtime.ultravox_realtime import (
            DograhUltravoxOneShotInputParams,
            DograhUltravoxRealtimeLLMService,
        )

        return DograhUltravoxRealtimeLLMService(
            params=DograhUltravoxOneShotInputParams(
                api_key=api_key,
                model=model,
                voice=voice,
                output_medium="voice",
            ),
            settings=DograhUltravoxRealtimeLLMService.Settings(
                model=model,
                output_medium="voice",
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
    elif provider == ServiceProviders.AZURE_REALTIME.value:
        from api.services.pipecat.realtime.azure_realtime import (
            DograhAzureRealtimeLLMService,
        )
        from pipecat.services.openai.realtime.events import (
            AudioConfiguration,
            AudioInput,
            AudioOutput,
            InputAudioTranscription,
            SessionProperties,
        )

        endpoint = getattr(realtime_config, "endpoint", None) or ""
        if not endpoint:
            raise HTTPException(
                status_code=400,
                detail="Azure Realtime requires an endpoint.",
            )
        _validate_runtime_service_url(endpoint, "endpoint")
        api_version = (
            getattr(realtime_config, "api_version", None) or "2025-04-01-preview"
        )
        # Construct the Azure Realtime WebSocket URL
        # https://<resource>.openai.azure.com/openai/realtime?api-version=<ver>&deployment=<model>
        parsed_endpoint = urlparse(endpoint)
        wss_url = urlunparse(
            (
                "wss",
                parsed_endpoint.netloc,
                "/openai/realtime",
                "",
                urlencode({"api-version": api_version, "deployment": model}),
                "",
            )
        )
        return DograhAzureRealtimeLLMService(
            api_key=api_key,
            base_url=wss_url,
            settings=DograhAzureRealtimeLLMService.Settings(
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
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid realtime LLM provider {provider}"
        )


def create_llm_service(user_config, correlation_id: str | None = None):
    """Create and return appropriate LLM service based on user configuration."""
    provider = user_config.llm.provider
    model = user_config.llm.model
    api_key = user_config.llm.api_key

    kwargs = {}
    if provider == ServiceProviders.OPENAI.value:
        kwargs["base_url"] = user_config.llm.base_url
    elif provider == ServiceProviders.OPENROUTER.value:
        kwargs["base_url"] = user_config.llm.base_url
    elif provider == ServiceProviders.AZURE.value:
        kwargs["endpoint"] = user_config.llm.endpoint
    elif provider == ServiceProviders.SPEACHES.value:
        kwargs["base_url"] = user_config.llm.base_url
    elif provider == ServiceProviders.HUGGINGFACE.value:
        kwargs["base_url"] = user_config.llm.base_url
        kwargs["bill_to"] = user_config.llm.bill_to
    elif provider == ServiceProviders.AWS_BEDROCK.value:
        kwargs["aws_access_key"] = user_config.llm.aws_access_key
        kwargs["aws_secret_key"] = user_config.llm.aws_secret_key
        kwargs["aws_region"] = user_config.llm.aws_region
    elif provider == ServiceProviders.GOOGLE_VERTEX.value:
        kwargs["project_id"] = user_config.llm.project_id
        kwargs["location"] = user_config.llm.location
        kwargs["credentials"] = user_config.llm.credentials
    elif provider == ServiceProviders.MINIMAX.value:
        kwargs["base_url"] = user_config.llm.base_url
        kwargs["temperature"] = user_config.llm.temperature
    elif provider == ServiceProviders.SARVAM.value:
        kwargs["temperature"] = user_config.llm.temperature

    return create_llm_service_from_provider(
        provider,
        model,
        api_key,
        correlation_id=correlation_id,
        **kwargs,
    )
