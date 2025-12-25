from typing import TYPE_CHECKING

from fastapi import HTTPException

from api.constants import MPS_API_URL
from api.services.configuration.registry import ServiceProviders
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.cartesia.stt import CartesiaSTTService
from pipecat.services.deepgram.stt import DeepgramSTTService, LiveOptions
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.dograh.llm import DograhLLMService
from pipecat.services.dograh.stt import DograhSTTService
from pipecat.services.dograh.tts import DograhTTSService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.text.xml_function_tag_filter import XMLFunctionTagFilter

if TYPE_CHECKING:
    from api.services.pipecat.audio_config import AudioConfig


def create_stt_service(user_config):
    """Create and return appropriate STT service based on user configuration"""
    if user_config.stt.provider == ServiceProviders.DEEPGRAM.value:
        # Use language from user config, defaulting to "multi" for multilingual support
        language = getattr(user_config.stt, "language", None)
        language_value = (
            language.value if hasattr(language, "value") else (language or "multi")
        )
        live_options = LiveOptions(
            language=language_value, profanity_filter=False, endpointing=100
        )
        return DeepgramSTTService(
            live_options=live_options,
            api_key=user_config.stt.api_key,
            audio_passthrough=False,  # Disable passthrough since audio is buffered separately
        )
    elif user_config.stt.provider == ServiceProviders.OPENAI.value:
        return OpenAISTTService(
            api_key=user_config.stt.api_key,
            model=user_config.stt.model.value,
            audio_passthrough=False,  # Disable passthrough since audio is buffered separately
        )
    elif user_config.stt.provider == ServiceProviders.CARTESIA.value:
        return CartesiaSTTService(
            api_key=user_config.stt.api_key,
            audio_passthrough=False,  # Disable passthrough since audio is buffered separately
        )
    elif user_config.stt.provider == ServiceProviders.DOGRAH.value:
        base_url = MPS_API_URL.replace("http://", "ws://").replace("https://", "wss://")
        return DograhSTTService(
            base_url=base_url,
            api_key=user_config.stt.api_key,
            model=user_config.stt.model.value,
            audio_passthrough=False,  # Disable passthrough since audio is buffered separately
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
        language_value = language.value if hasattr(language, "value") else language
        pipecat_language = language_mapping.get(language_value, Language.HI_IN)

        return SarvamSTTService(
            api_key=user_config.stt.api_key,
            model=user_config.stt.model.value,
            params=SarvamSTTService.InputParams(language=pipecat_language),
            audio_passthrough=False,
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid STT provider {user_config.stt.provider}"
        )


def create_tts_service(user_config, audio_config: "AudioConfig"):
    """Create and return appropriate TTS service based on user configuration

    Args:
        user_config: User configuration containing TTS settings
        transport_type: Type of transport (e.g., 'stasis', 'twilio', 'webrtc')
    """
    # Create function call filter to prevent TTS from speaking function call tags
    xml_function_tag_filter = XMLFunctionTagFilter()
    if user_config.tts.provider == ServiceProviders.DEEPGRAM.value:
        return DeepgramTTSService(
            api_key=user_config.tts.api_key,
            voice=user_config.tts.voice.value,
            text_filters=[xml_function_tag_filter],
        )
    elif user_config.tts.provider == ServiceProviders.OPENAI.value:
        return OpenAITTSService(
            api_key=user_config.tts.api_key,
            model=user_config.tts.model.value,
            text_filters=[xml_function_tag_filter],
        )
    elif user_config.tts.provider == ServiceProviders.ELEVENLABS.value:
        # Backward compatible with older configuration "Name - voice_id"
        try:
            voice_id = user_config.tts.voice.split(" - ")[1]
        except IndexError:
            voice_id = user_config.tts.voice

        return ElevenLabsTTSService(
            reconnect_on_error=False,
            api_key=user_config.tts.api_key,
            voice_id=voice_id,
            model=user_config.tts.model.value,
            params=ElevenLabsTTSService.InputParams(
                stability=0.8, speed=user_config.tts.speed, similarity_boost=0.75
            ),
            text_filters=[xml_function_tag_filter],
        )
    elif user_config.tts.provider == ServiceProviders.DOGRAH.value:
        # Convert HTTP URL to WebSocket URL for TTS
        base_url = MPS_API_URL.replace("http://", "ws://").replace("https://", "wss://")
        # Handle both enum and string values for model and voice
        return DograhTTSService(
            base_url=base_url,
            api_key=user_config.tts.api_key,
            model=user_config.tts.model.value,
            voice=user_config.tts.voice.value,
            text_filters=[xml_function_tag_filter],
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
        language_value = language.value if hasattr(language, "value") else language
        pipecat_language = language_mapping.get(language_value, Language.HI)

        voice = getattr(user_config.tts, "voice", None)
        voice_value = voice.value if hasattr(voice, "value") else (voice or "anushka")

        return SarvamTTSService(
            api_key=user_config.tts.api_key,
            model=user_config.tts.model.value,
            voice_id=voice_value,
            params=SarvamTTSService.InputParams(language=pipecat_language),
            text_filters=[xml_function_tag_filter],
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid TTS provider {user_config.tts.provider}"
        )


def create_llm_service(user_config):
    """Create and return appropriate LLM service based on user configuration"""
    if user_config.llm.provider == ServiceProviders.OPENAI.value:
        if "gpt-5" in user_config.llm.model.value:
            return OpenAILLMService(
                api_key=user_config.llm.api_key,
                model=user_config.llm.model.value,
                params=OpenAILLMService.InputParams(
                    reasoning_effort="minimal", verbosity="low"
                ),
            )
        else:
            return OpenAILLMService(
                api_key=user_config.llm.api_key,
                model=user_config.llm.model.value,
                params=OpenAILLMService.InputParams(temperature=0.1),
            )
    elif user_config.llm.provider == ServiceProviders.GROQ.value:
        print(
            f"Creating Groq LLM service with API key: {user_config.llm.api_key} and model: {user_config.llm.model.value}"
        )
        return GroqLLMService(
            api_key=user_config.llm.api_key,
            model=user_config.llm.model.value,
            params=OpenAILLMService.InputParams(temperature=0.1),
        )
    elif user_config.llm.provider == ServiceProviders.GOOGLE.value:
        # Use the correct InputParams class for Google to avoid propagating OpenAI-specific
        # NOT_GIVEN sentinels that break Pydantic validation in GoogleLLMService.
        return GoogleLLMService(
            api_key=user_config.llm.api_key,
            model=user_config.llm.model.value,
            params=GoogleLLMService.InputParams(temperature=0.1),
        )
    elif user_config.llm.provider == ServiceProviders.AZURE.value:
        return AzureLLMService(
            api_key=user_config.llm.api_key,
            endpoint=user_config.llm.endpoint,
            model=user_config.llm.model.value,  # Azure uses deployment name as model
            params=AzureLLMService.InputParams(temperature=0.1),
        )
    elif user_config.llm.provider == ServiceProviders.DOGRAH.value:
        return DograhLLMService(
            base_url=f"{MPS_API_URL}/api/v1/llm",
            api_key=user_config.llm.api_key,
            model=user_config.llm.model.value,
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid LLM provider")
