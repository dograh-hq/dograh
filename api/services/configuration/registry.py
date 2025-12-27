from enum import Enum, auto
from typing import Annotated, Dict, Literal, Type, TypeVar, Union

from pydantic import BaseModel, Field, computed_field


class ServiceType(Enum):
    LLM = auto()
    TTS = auto()
    STT = auto()


class ServiceProviders(str, Enum):
    OPENAI = "openai"
    DEEPGRAM = "deepgram"
    GROQ = "groq"
    CARTESIA = "cartesia"
    # NEUPHONIC = "neuphonic"
    ELEVENLABS = "elevenlabs"
    GOOGLE = "google"
    AZURE = "azure"
    DOGRAH = "dograh"
    SARVAM = "sarvam"


class BaseServiceConfiguration(BaseModel):
    provider: Literal[
        ServiceProviders.OPENAI,
        ServiceProviders.DEEPGRAM,
        ServiceProviders.GROQ,
        ServiceProviders.ELEVENLABS,
        ServiceProviders.GOOGLE,
        ServiceProviders.AZURE,
        ServiceProviders.DOGRAH,
        # ServiceProviders.SARVAM,
    ]
    api_key: str


class BaseLLMConfiguration(BaseServiceConfiguration):
    model: str


class BaseTTSConfiguration(BaseServiceConfiguration):
    model: str


class BaseSTTConfiguration(BaseServiceConfiguration):
    model: str


# Unified registry for all service types
REGISTRY: Dict[ServiceType, Dict[str, Type[BaseServiceConfiguration]]] = {
    ServiceType.LLM: {},
    ServiceType.TTS: {},
    ServiceType.STT: {},
}

T = TypeVar("T", bound=BaseServiceConfiguration)


def register_service(service_type: ServiceType):
    """Generic decorator for registering service configurations"""

    def decorator(cls: Type[T]) -> Type[T]:
        # Get provider from class attributes or field defaults
        provider = getattr(cls, "provider", None)
        if provider is None:
            # Try to get from model fields
            provider = cls.model_fields.get("provider", None)
            if provider is not None:
                provider = provider.default
        if provider is None:
            raise ValueError(f"Provider not specified for {cls.__name__}")

        REGISTRY[service_type][provider] = cls
        return cls

    return decorator


# Convenience decorators
def register_llm(cls: Type[BaseLLMConfiguration]):
    return register_service(ServiceType.LLM)(cls)


def register_tts(cls: Type[BaseTTSConfiguration]):
    return register_service(ServiceType.TTS)(cls)


def register_stt(cls: Type[BaseSTTConfiguration]):
    return register_service(ServiceType.STT)(cls)


###################################################### LLM ########################################################################

# Suggested models for each provider (used for UI dropdown)
OPENAI_MODELS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-3.5-turbo",
]
GOOGLE_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "deepseek-r1-distill-llama-70b",
    "qwen-qwq-32b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "gemma2-9b-it",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
]
AZURE_MODELS = ["gpt-4.1-mini"]
DOGRAH_LLM_MODELS = ["default", "accurate", "fast", "lite", "zen", "zen_lite"]


@register_llm
class OpenAILLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(default="gpt-4.1", json_schema_extra={"examples": OPENAI_MODELS})
    api_key: str


@register_llm
class GoogleLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.GOOGLE] = ServiceProviders.GOOGLE
    model: str = Field(
        default="gemini-2.0-flash", json_schema_extra={"examples": GOOGLE_MODELS}
    )
    api_key: str


@register_llm
class GroqLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.GROQ] = ServiceProviders.GROQ
    model: str = Field(
        default="llama-3.3-70b-versatile", json_schema_extra={"examples": GROQ_MODELS}
    )
    api_key: str


@register_llm
class AzureLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.AZURE] = ServiceProviders.AZURE
    model: str = Field(
        default="gpt-4.1-mini", json_schema_extra={"examples": AZURE_MODELS}
    )
    api_key: str
    endpoint: str


@register_llm
class DograhLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: str = Field(
        default="default", json_schema_extra={"examples": DOGRAH_LLM_MODELS}
    )
    api_key: str


LLMConfig = Annotated[
    Union[
        OpenAILLMService,
        GroqLLMService,
        GoogleLLMService,
        AzureLLMService,
        DograhLLMService,
    ],
    Field(discriminator="provider"),
]

###################################################### TTS ########################################################################


@register_tts
class DeepgramTTSConfiguration(BaseServiceConfiguration):
    provider: Literal[ServiceProviders.DEEPGRAM] = ServiceProviders.DEEPGRAM
    voice: str = "aura-2-helena-en"
    api_key: str

    @computed_field
    @property
    def model(self) -> str:
        # Deepgram model's name is inferred using the voice name.
        # It can either contain aura-2 or aura-1
        if "aura-2" in self.voice:
            return "aura-2"
        elif "aura-1" in self.voice:
            return "aura-1"
        else:
            # Default fallback
            return "aura-2"


ELEVENLABS_TTS_MODELS = ["eleven_flash_v2_5"]


@register_tts
class ElevenlabsTTSConfiguration(BaseServiceConfiguration):
    provider: Literal[ServiceProviders.ELEVENLABS] = ServiceProviders.ELEVENLABS
    voice: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel voice ID
    speed: float = Field(default=1.0, ge=0.1, le=2.0, description="Speed of the voice")
    model: str = Field(
        default="eleven_flash_v2_5",
        json_schema_extra={"examples": ELEVENLABS_TTS_MODELS},
    )
    api_key: str


OPENAI_TTS_MODELS = ["gpt-4o-mini-tts"]


@register_tts
class OpenAITTSService(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="gpt-4o-mini-tts", json_schema_extra={"examples": OPENAI_TTS_MODELS}
    )
    voice: str = "alloy"
    api_key: str


DOGRAH_TTS_MODELS = ["default"]


@register_tts
class DograhTTSService(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: str = Field(
        default="default", json_schema_extra={"examples": DOGRAH_TTS_MODELS}
    )
    voice: str = "default"
    api_key: str


SARVAM_TTS_MODELS = ["bulbul:v2", "bulbul:v3"]
SARVAM_VOICES = ["anushka", "manisha", "vidya", "arya", "abhilash", "karun", "hitesh"]
SARVAM_LANGUAGES = [
    "bn-IN",
    "en-IN",
    "gu-IN",
    "hi-IN",
    "kn-IN",
    "ml-IN",
    "mr-IN",
    "od-IN",
    "pa-IN",
    "ta-IN",
    "te-IN",
    "as-IN",
]


# @register_tts
# class SarvamTTSConfiguration(BaseTTSConfiguration):
#     provider: Literal[ServiceProviders.SARVAM] = ServiceProviders.SARVAM
#     model: str = Field(default="bulbul:v2", json_schema_extra={"examples": SARVAM_TTS_MODELS})
#     voice: str = Field(default="anushka", json_schema_extra={"examples": SARVAM_VOICES})
#     language: str = Field(default="hi-IN", json_schema_extra={"examples": SARVAM_LANGUAGES})
#     api_key: str


TTSConfig = Annotated[
    Union[
        DeepgramTTSConfiguration,
        OpenAITTSService,
        ElevenlabsTTSConfiguration,
        DograhTTSService,
        # SarvamTTSConfiguration,
    ],
    Field(discriminator="provider"),
]

###################################################### STT ########################################################################


DEEPGRAM_STT_MODELS = ["nova-3-general"]
DEEPGRAM_LANGUAGES = [
    "multi",
    "en",
    "en-US",
    "en-GB",
    "en-AU",
    "en-IN",
    "es",
    "es-419",
    "fr",
    "fr-CA",
    "de",
    "it",
    "pt",
    "pt-BR",
    "nl",
    "hi",
    "ja",
    "ko",
    "zh-CN",
    "zh-TW",
    "ru",
    "pl",
    "tr",
    "uk",
    "vi",
    "sv",
    "da",
    "no",
    "fi",
    "id",
    "th",
]


@register_stt
class DeepgramSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.DEEPGRAM] = ServiceProviders.DEEPGRAM
    model: str = Field(
        default="nova-3-general", json_schema_extra={"examples": DEEPGRAM_STT_MODELS}
    )
    language: str = Field(
        default="multi", json_schema_extra={"examples": DEEPGRAM_LANGUAGES}
    )
    api_key: str


@register_stt
class CartesiaSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.CARTESIA] = ServiceProviders.CARTESIA
    api_key: str


OPENAI_STT_MODELS = ["gpt-4o-transcribe"]


@register_stt
class OpenAISTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="gpt-4o-transcribe", json_schema_extra={"examples": OPENAI_STT_MODELS}
    )
    api_key: str


# Dograh STT Service
DOGRAH_STT_MODELS = ["default"]


@register_stt
class DograhSTTService(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: str = Field(
        default="default", json_schema_extra={"examples": DOGRAH_STT_MODELS}
    )
    api_key: str


# Sarvam STT Service
SARVAM_STT_MODELS = ["saarika:v2.5", "saaras:v2"]


# @register_stt
# class SarvamSTTConfiguration(BaseSTTConfiguration):
#     provider: Literal[ServiceProviders.SARVAM] = ServiceProviders.SARVAM
#     model: str = Field(default="saarika:v2.5", json_schema_extra={"examples": SARVAM_STT_MODELS})
#     language: str = Field(default="hi-IN", json_schema_extra={"examples": SARVAM_LANGUAGES})
#     api_key: str


STTConfig = Annotated[
    Union[
        DeepgramSTTConfiguration,
        OpenAISTTConfiguration,
        DograhSTTService,
        # SarvamSTTConfiguration,
    ],
    Field(discriminator="provider"),
]

ServiceConfig = Annotated[
    Union[LLMConfig, TTSConfig, STTConfig], Field(discriminator="provider")
]
