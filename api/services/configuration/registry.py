import random
from enum import Enum, auto
from typing import Annotated, Dict, Literal, Type, TypeVar, Union

from pydantic import BaseModel, Field, computed_field, field_validator


class ServiceType(Enum):
    LLM = auto()
    TTS = auto()
    STT = auto()
    EMBEDDINGS = auto()
    REALTIME = auto()


class ServiceProviders(str, Enum):
    OPENAI = "openai"
    DEEPGRAM = "deepgram"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    CARTESIA = "cartesia"
    # NEUPHONIC = "neuphonic"
    ELEVENLABS = "elevenlabs"
    GOOGLE = "google"
    AZURE = "azure"
    DOGRAH = "dograh"
    SARVAM = "sarvam"
    SPEECHMATICS = "speechmatics"
    CAMB = "camb"
    AWS_BEDROCK = "aws_bedrock"
    SPEACHES = "speaches"
    ASSEMBLYAI = "assemblyai"
    GLADIA = "gladia"
    RIME = "rime"
    OPENAI_REALTIME = "openai_realtime"
    GOOGLE_REALTIME = "google_realtime"
    GOOGLE_VERTEX_REALTIME = "google_vertex_realtime"


class BaseServiceConfiguration(BaseModel):
    provider: Literal[
        ServiceProviders.OPENAI,
        ServiceProviders.DEEPGRAM,
        ServiceProviders.GROQ,
        ServiceProviders.OPENROUTER,
        ServiceProviders.ELEVENLABS,
        ServiceProviders.GOOGLE,
        ServiceProviders.AZURE,
        ServiceProviders.DOGRAH,
        ServiceProviders.AWS_BEDROCK,
        ServiceProviders.SPEACHES,
        ServiceProviders.ASSEMBLYAI,
        ServiceProviders.GLADIA,
        ServiceProviders.RIME,
        ServiceProviders.OPENAI_REALTIME,
        ServiceProviders.GOOGLE_REALTIME,
        ServiceProviders.GOOGLE_VERTEX_REALTIME,
        # ServiceProviders.SARVAM,
    ]
    api_key: str | list[str]

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v):
        if v is None:
            return v
        if isinstance(v, list) and len(v) == 0:
            raise ValueError("api_key list must not be empty")
        return v

    def __getattribute__(self, name: str):
        if name == "api_key":
            value = super().__getattribute__(name)
            if value is None:
                return value
            if isinstance(value, list):
                return random.choice(value)
            return value
        return super().__getattribute__(name)

    def get_all_api_keys(self) -> list[str]:
        """Get all API keys as a list (bypasses random selection)."""
        value = super().__getattribute__("api_key")
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        return [value]


class BaseLLMConfiguration(BaseServiceConfiguration):
    model: str


class BaseTTSConfiguration(BaseServiceConfiguration):
    model: str


class BaseSTTConfiguration(BaseServiceConfiguration):
    model: str


class BaseEmbeddingsConfiguration(BaseServiceConfiguration):
    model: str


# Unified registry for all service types
REGISTRY: Dict[ServiceType, Dict[str, Type[BaseServiceConfiguration]]] = {
    ServiceType.LLM: {},
    ServiceType.TTS: {},
    ServiceType.STT: {},
    ServiceType.EMBEDDINGS: {},
    ServiceType.REALTIME: {},
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


def register_embeddings(cls: Type[BaseEmbeddingsConfiguration]):
    return register_service(ServiceType.EMBEDDINGS)(cls)


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
OPENROUTER_MODELS = [
    "openai/gpt-4.1",
    "openai/gpt-4.1-mini",
    "anthropic/claude-sonnet-4",
    "google/gemini-2.5-flash",
    "google/gemini-2.0-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "deepseek/deepseek-chat-v3-0324",
]
AZURE_MODELS = ["gpt-4.1-mini"]
DOGRAH_LLM_MODELS = ["default", "accurate", "fast", "lite", "zen"]
AWS_BEDROCK_MODELS = [
    "us.amazon.nova-pro-v1:0",
    "us.amazon.nova-lite-v1:0",
    "us.amazon.nova-micro-v1:0",
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
]


@register_llm
class OpenAILLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="gpt-4.1",
        description="OpenAI chat model to use.",
        json_schema_extra={"examples": OPENAI_MODELS, "allow_custom_input": True},
    )


@register_llm
class GoogleLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.GOOGLE] = ServiceProviders.GOOGLE
    model: str = Field(
        default="gemini-2.0-flash",
        description="Gemini model on Google AI Studio (not Vertex).",
        json_schema_extra={"examples": GOOGLE_MODELS, "allow_custom_input": True},
    )


@register_llm
class GroqLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.GROQ] = ServiceProviders.GROQ
    model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq-hosted model identifier.",
        json_schema_extra={"examples": GROQ_MODELS, "allow_custom_input": True},
    )


@register_llm
class OpenRouterLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.OPENROUTER] = ServiceProviders.OPENROUTER
    model: str = Field(
        default="openai/gpt-4.1",
        description="OpenRouter model slug in 'vendor/model' form.",
        json_schema_extra={"examples": OPENROUTER_MODELS, "allow_custom_input": True},
    )

    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Override only if proxying OpenRouter through your own gateway.",
    )


@register_llm
class AzureLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.AZURE] = ServiceProviders.AZURE
    model: str = Field(
        default="gpt-4.1-mini",
        description="Azure deployment name (not the upstream OpenAI model id).",
        json_schema_extra={"examples": AZURE_MODELS, "allow_custom_input": True},
    )

    endpoint: str = Field(
        description="Azure OpenAI resource endpoint (e.g. https://<resource>.openai.azure.com).",
    )


@register_llm
class DograhLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: str = Field(
        default="default",
        description="Dograh-hosted model tier.",
        json_schema_extra={"examples": DOGRAH_LLM_MODELS, "allow_custom_input": True},
    )


@register_llm
class AWSBedrockLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.AWS_BEDROCK] = ServiceProviders.AWS_BEDROCK
    model: str = Field(
        default="us.amazon.nova-pro-v1:0",
        description="Bedrock model ID — include the region inference-profile prefix (e.g. 'us.').",
        json_schema_extra={"examples": AWS_BEDROCK_MODELS, "allow_custom_input": True},
    )
    aws_access_key: str = Field(
        default="",
        description="AWS access key ID with bedrock:InvokeModel permission.",
    )
    aws_secret_key: str = Field(
        default="",
        description="AWS secret access key paired with the access key ID.",
    )
    aws_region: str = Field(
        default="us-east-1",
        description="AWS region where the Bedrock model is available.",
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Not used for Bedrock — authentication is via the AWS credentials above. Leave blank.",
    )


SPEACHES_LLM_MODELS = ["llama3", "mistral", "phi3", "qwen2", "gemma2", "deepseek-r1"]


@register_llm
class SpeachesLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.SPEACHES] = ServiceProviders.SPEACHES
    model: str = Field(
        default="llama3",
        description="Model name as exposed by your OpenAI-compatible server.",
        json_schema_extra={
            "examples": SPEACHES_LLM_MODELS,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="http://localhost:11434/v1",
        description="OpenAI-compatible endpoint (Ollama, vLLM, etc.).",
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Usually not required for self-hosted endpoints. Leave blank unless your server enforces one.",
    )


OPENAI_REALTIME_MODELS = ["gpt-realtime-2"]
OPENAI_REALTIME_VOICES = [
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
]


@register_service(ServiceType.REALTIME)
class OpenAIRealtimeLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.OPENAI_REALTIME] = (
        ServiceProviders.OPENAI_REALTIME
    )
    model: str = Field(
        default="gpt-realtime-2",
        description="OpenAI realtime (speech-to-speech) model.",
        json_schema_extra={
            "examples": OPENAI_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="alloy",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": OPENAI_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )


GOOGLE_REALTIME_MODELS = ["gemini-3.1-flash-live-preview"]
GOOGLE_REALTIME_VOICES = ["Puck", "Charon", "Kore", "Fenrir", "Aoede"]
GOOGLE_REALTIME_LANGUAGES = [
    "ar",
    "bn",
    "de",
    "en",
    "es",
    "fr",
    "gu",
    "hi",
    "id",
    "it",
    "ja",
    "kn",
    "ko",
    "ml",
    "mr",
    "nl",
    "pl",
    "pt",
    "ru",
    "ta",
    "te",
    "th",
    "tr",
    "vi",
    "zh",
]


@register_service(ServiceType.REALTIME)
class GoogleRealtimeLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.GOOGLE_REALTIME] = (
        ServiceProviders.GOOGLE_REALTIME
    )
    model: str = Field(
        default="gemini-3.1-flash-live-preview",
        description="Gemini Live model on Google AI Studio (not Vertex).",
        json_schema_extra={
            "examples": GOOGLE_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="Puck",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": GOOGLE_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={
            "examples": GOOGLE_REALTIME_LANGUAGES,
            "allow_custom_input": True,
        },
    )


GOOGLE_VERTEX_REALTIME_MODELS = [
    "google/gemini-live-2.5-flash-native-audio",
]
GOOGLE_VERTEX_REALTIME_VOICES = GOOGLE_REALTIME_VOICES
GOOGLE_VERTEX_REALTIME_LANGUAGES = GOOGLE_REALTIME_LANGUAGES


@register_service(ServiceType.REALTIME)
class GoogleVertexRealtimeLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.GOOGLE_VERTEX_REALTIME] = (
        ServiceProviders.GOOGLE_VERTEX_REALTIME
    )
    model: str = Field(
        default="google/gemini-live-2.5-flash-native-audio",
        description="Vertex AI publisher/model identifier.",
        json_schema_extra={
            "examples": GOOGLE_VERTEX_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="Charon",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": GOOGLE_VERTEX_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en",
        description="BCP-47 language code (e.g. 'en-US').",
        json_schema_extra={
            "examples": GOOGLE_VERTEX_REALTIME_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    project_id: str = Field(description="Google Cloud project ID for Vertex AI.")
    location: str = Field(
        default="us-east4",
        description="GCP region for the Vertex AI endpoint (e.g. 'us-east4').",
    )
    credentials: str | None = Field(
        default=None,
        description=(
            "Paste the entire service-account JSON file contents. If omitted, "
            "falls back to Application Default Credentials (ADC)."
        ),
        json_schema_extra={"multiline": True},
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description=(
            "Not used for Vertex AI — authentication is via the service account "
            "in `credentials` (or ADC). Leave blank."
        ),
    )


REALTIME_PROVIDERS = {
    ServiceProviders.OPENAI_REALTIME.value,
    ServiceProviders.GOOGLE_REALTIME.value,
    ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
}


LLMConfig = Annotated[
    Union[
        OpenAILLMService,
        GroqLLMService,
        OpenRouterLLMConfiguration,
        GoogleLLMService,
        AzureLLMService,
        DograhLLMService,
        AWSBedrockLLMConfiguration,
        SpeachesLLMConfiguration,
    ],
    Field(discriminator="provider"),
]

RealtimeConfig = Annotated[
    Union[
        OpenAIRealtimeLLMConfiguration,
        GoogleRealtimeLLMConfiguration,
        GoogleVertexRealtimeLLMConfiguration,
    ],
    Field(discriminator="provider"),
]

###################################################### TTS ########################################################################


@register_tts
class DeepgramTTSConfiguration(BaseServiceConfiguration):
    provider: Literal[ServiceProviders.DEEPGRAM] = ServiceProviders.DEEPGRAM
    voice: str = Field(
        default="aura-2-helena-en",
        description="Deepgram voice ID (model is inferred from the 'aura-N' prefix).",
    )

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
    voice: str = Field(
        default="21m00Tcm4TlvDq8ikWAM",
        description="ElevenLabs voice ID from your Voice Library.",
    )
    speed: float = Field(default=1.0, ge=0.1, le=2.0, description="Speed of the voice.")
    model: str = Field(
        default="eleven_flash_v2_5",
        description="ElevenLabs TTS model.",
        json_schema_extra={"examples": ELEVENLABS_TTS_MODELS},
    )
    base_url: str = Field(
        default="https://api.elevenlabs.io",
        description=(
            "ElevenLabs API base URL. Override to use a Data Residency endpoint "
            "(e.g. https://api.eu.residency.elevenlabs.io) for GDPR / HIPAA / "
            "regional compliance."
        ),
    )


OPENAI_TTS_MODELS = ["gpt-4o-mini-tts"]


@register_tts
class OpenAITTSService(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="gpt-4o-mini-tts",
        description="OpenAI TTS model.",
        json_schema_extra={"examples": OPENAI_TTS_MODELS},
    )
    voice: str = Field(
        default="alloy",
        description="OpenAI TTS voice name.",
    )


DOGRAH_TTS_MODELS = ["default"]


@register_tts
class DograhTTSService(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: str = Field(
        default="default",
        description="Dograh TTS tier.",
        json_schema_extra={"examples": DOGRAH_TTS_MODELS},
    )
    voice: str = Field(
        default="default",
        description="Voice preset.",
    )
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Speed of the voice.")


CARTESIA_TTS_MODELS = ["sonic-3"]


@register_tts
class CartesiaTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.CARTESIA] = ServiceProviders.CARTESIA
    model: str = Field(
        default="sonic-3",
        description="Cartesia TTS model.",
        json_schema_extra={"examples": CARTESIA_TTS_MODELS},
    )
    voice: str = Field(
        default="3faa81ae-d3d8-4ab1-9e44-e50e46d33c30",
        description="Cartesia voice UUID from your Cartesia dashboard.",
    )
    speed: float = Field(default=1.0, ge=0.6, le=1.5, description="Speed of the voice.")
    volume: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="Volume multiplier for generated speech.",
    )


SARVAM_TTS_MODELS = ["bulbul:v2", "bulbul:v3"]
SARVAM_V2_VOICES = [
    "anushka",
    "manisha",
    "vidya",
    "arya",
    "abhilash",
    "karun",
    "hitesh",
]
SARVAM_V3_VOICES = [
    "shubh",
    "aditya",
    "ritu",
    "priya",
    "neha",
    "rahul",
    "pooja",
    "rohan",
    "simran",
    "kavya",
    "amit",
    "dev",
    "ishita",
    "shreya",
    "ratan",
    "varun",
    "manan",
    "sumit",
    "roopa",
    "kabir",
    "aayan",
    "ashutosh",
    "advait",
    "amelia",
    "sophia",
    "anand",
    "tanya",
    "tarun",
    "sunny",
    "mani",
    "gokul",
    "vijay",
    "shruti",
    "suhani",
    "mohit",
    "kavitha",
    "rehan",
    "soham",
    "rupali",
]
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


@register_tts
class SarvamTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.SARVAM] = ServiceProviders.SARVAM
    model: str = Field(
        default="bulbul:v2",
        description="Sarvam TTS model (voice list depends on this).",
        json_schema_extra={"examples": SARVAM_TTS_MODELS},
    )
    voice: str = Field(
        default="anushka",
        description="Sarvam voice name; must match the selected model's voice list.",
        json_schema_extra={
            "examples": SARVAM_V2_VOICES,
            "model_options": {
                "bulbul:v2": SARVAM_V2_VOICES,
                "bulbul:v3": SARVAM_V3_VOICES,
            },
        },
    )
    language: str = Field(
        default="hi-IN",
        description="BCP-47 Indian-language code (e.g. hi-IN, en-IN).",
        json_schema_extra={"examples": SARVAM_LANGUAGES},
    )


CAMB_TTS_MODELS = ["mars-flash", "mars-pro", "mars-instruct"]


@register_tts
class CambTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.CAMB] = ServiceProviders.CAMB
    model: str = Field(
        default="mars-flash",
        description="Camb.ai TTS model.",
        json_schema_extra={"examples": CAMB_TTS_MODELS},
    )
    voice: str = Field(default="147320", description="Camb.ai voice ID.")
    language: str = Field(default="en-us", description="BCP-47 language code.")


RIME_TTS_MODELS = ["arcana", "mistv3", "mistv2", "mist"]
RIME_TTS_LANGUAGES = ["en", "de", "fr", "es", "hi"]


@register_tts
class RimeTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.RIME] = ServiceProviders.RIME
    model: str = Field(
        default="arcana",
        description="Rime TTS model.",
        json_schema_extra={"examples": RIME_TTS_MODELS, "allow_custom_input": True},
    )
    voice: str = Field(
        default="celeste",
        description="Rime voice ID.",
    )
    speed: float = Field(
        default=1.0, ge=0.5, le=2.0, description="Speech speed multiplier."
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={"examples": RIME_TTS_LANGUAGES, "allow_custom_input": True},
    )


SPEACHES_TTS_MODELS = ["hexgrad/Kokoro-82M"]


@register_tts
class SpeachesTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.SPEACHES] = ServiceProviders.SPEACHES
    model: str = Field(
        default="kokoro",
        description="Model name as served by your TTS endpoint (e.g. Kokoro-FastAPI).",
        json_schema_extra={
            "examples": SPEACHES_TTS_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="af_heart",
        json_schema_extra={"allow_custom_input": True},
        description="Voice ID for the TTS engine.",
    )
    base_url: str = Field(
        default="http://localhost:8000/v1",
        description="OpenAI-compatible TTS endpoint (Kokoro-FastAPI, etc.).",
    )
    speed: float = Field(
        default=1.0, ge=0.25, le=4.0, description="Speech speed (0.25 to 4.0)."
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Usually not required for self-hosted TTS. Leave blank unless enforced.",
    )


TTSConfig = Annotated[
    Union[
        DeepgramTTSConfiguration,
        OpenAITTSService,
        ElevenlabsTTSConfiguration,
        CartesiaTTSConfiguration,
        DograhTTSService,
        SarvamTTSConfiguration,
        CambTTSConfiguration,
        RimeTTSConfiguration,
        SpeachesTTSConfiguration,
    ],
    Field(discriminator="provider"),
]

###################################################### STT ########################################################################


DEEPGRAM_STT_MODELS = ["nova-3-general", "flux-general-en", "flux-general-multi"]
DEEPGRAM_LANGUAGES = [
    "multi",
    "ar",
    "ar-AE",
    "ar-SA",
    "ar-QA",
    "ar-KW",
    "ar-SY",
    "ar-LB",
    "ar-PS",
    "ar-JO",
    "ar-EG",
    "ar-SD",
    "ar-TD",
    "ar-MA",
    "ar-DZ",
    "ar-TN",
    "ar-IQ",
    "ar-IR",
    "be",
    "bn",
    "bs",
    "bg",
    "ca",
    "cs",
    "da",
    "da-DK",
    "de",
    "de-CH",
    "el",
    "en",
    "en-US",
    "en-AU",
    "en-GB",
    "en-IN",
    "en-NZ",
    "es",
    "es-419",
    "et",
    "fa",
    "fi",
    "fr",
    "fr-CA",
    "he",
    "hi",
    "hr",
    "hu",
    "id",
    "it",
    "ja",
    "kn",
    "ko",
    "ko-KR",
    "lt",
    "lv",
    "mk",
    "mr",
    "ms",
    "nl",
    "nl-BE",
    "no",
    "pl",
    "pt",
    "pt-BR",
    "pt-PT",
    "ro",
    "ru",
    "sk",
    "sl",
    "sr",
    "sv",
    "sv-SE",
    "ta",
    "te",
    "th",
    "tl",
    "tr",
    "uk",
    "ur",
    "vi",
    "zh-CN",
    "zh-TW",
]


@register_stt
class DeepgramSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.DEEPGRAM] = ServiceProviders.DEEPGRAM
    model: str = Field(
        default="nova-3-general",
        description="Deepgram STT model.",
        json_schema_extra={"examples": DEEPGRAM_STT_MODELS},
    )
    language: str = Field(
        default="multi",
        description="Language code; 'multi' enables auto-detect (Nova-3 only).",
        json_schema_extra={
            "examples": DEEPGRAM_LANGUAGES,
            "model_options": {
                "nova-3-general": DEEPGRAM_LANGUAGES,
                "flux-general-en": ["en"],
            },
        },
    )


CARTESIA_STT_MODELS = ["ink-whisper"]


@register_stt
class CartesiaSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.CARTESIA] = ServiceProviders.CARTESIA
    model: str = Field(
        default="ink-whisper",
        description="Cartesia STT model.",
        json_schema_extra={"examples": CARTESIA_STT_MODELS},
    )


OPENAI_STT_MODELS = ["gpt-4o-transcribe"]


@register_stt
class OpenAISTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="gpt-4o-transcribe",
        description="OpenAI transcription model.",
        json_schema_extra={"examples": OPENAI_STT_MODELS},
    )


# Dograh STT Service
DOGRAH_STT_MODELS = ["default"]
DOGRAH_STT_LANGUAGES = DEEPGRAM_LANGUAGES


@register_stt
class DograhSTTService(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: str = Field(
        default="default",
        description="Dograh STT tier.",
        json_schema_extra={"examples": DOGRAH_STT_MODELS},
    )
    language: str = Field(
        default="multi",
        description="Language code; use 'multi' for auto-detect.",
        json_schema_extra={"examples": DOGRAH_STT_LANGUAGES},
    )


# Sarvam STT Service
SARVAM_STT_MODELS = ["saarika:v2.5", "saaras:v2"]


@register_stt
class SarvamSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.SARVAM] = ServiceProviders.SARVAM
    model: str = Field(
        default="saarika:v2.5",
        description="Sarvam STT model.",
        json_schema_extra={"examples": SARVAM_STT_MODELS},
    )
    language: str = Field(
        default="hi-IN",
        description="BCP-47 Indian-language code.",
        json_schema_extra={"examples": SARVAM_LANGUAGES},
    )


# Speechmatics STT Service
SPEECHMATICS_STT_LANGUAGES = [
    "en",
    "es",
    "fr",
    "de",
    "it",
    "pt",
    "nl",
    "ja",
    "ko",
    "zh",
    "ru",
    "ar",
    "hi",
    "pl",
    "tr",
    "vi",
    "th",
    "id",
    "ms",
    "sv",
    "da",
    "no",
    "fi",
]


@register_stt
class SpeechmaticsSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.SPEECHMATICS] = ServiceProviders.SPEECHMATICS
    model: str = Field(
        default="enhanced",
        description="Speechmatics operating point: 'standard' or 'enhanced'.",
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={"examples": SPEECHMATICS_STT_LANGUAGES},
    )


SPEACHES_STT_MODELS = [
    "Systran/faster-distil-whisper-small.en",
    "Systran/faster-whisper-large-v3",
]
SPEACHES_STT_LANGUAGES = ["en", "ar", "nl", "fr", "de", "hi", "it", "pt", "es"]


@register_stt
class SpeachesSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.SPEACHES] = ServiceProviders.SPEACHES
    model: str = Field(
        default="Systran/faster-distil-whisper-small.en",
        description="Whisper model identifier as served by your STT endpoint.",
        json_schema_extra={
            "examples": SPEACHES_STT_MODELS,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={
            "examples": SPEACHES_STT_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="http://localhost:8000/v1",
        description="OpenAI-compatible STT endpoint (Speaches, etc.).",
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Usually not required for self-hosted STT. Leave blank unless enforced.",
    )


ASSEMBLYAI_STT_MODELS = ["u3-rt-pro"]
ASSEMBLYAI_STT_LANGUAGES = ["en", "es", "de", "fr", "pt", "it"]


@register_stt
class AssemblyAISTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.ASSEMBLYAI] = ServiceProviders.ASSEMBLYAI
    model: str = Field(
        default="u3-rt-pro",
        description="AssemblyAI realtime STT model.",
        json_schema_extra={"examples": ASSEMBLYAI_STT_MODELS},
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={"examples": ASSEMBLYAI_STT_LANGUAGES},
    )


GLADIA_STT_MODELS = ["solaria-1"]
GLADIA_STT_LANGUAGES = [
    "af",
    "am",
    "ar",
    "as",
    "az",
    "ba",
    "be",
    "bg",
    "bn",
    "bo",
    "br",
    "bs",
    "ca",
    "cs",
    "cy",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "eu",
    "fa",
    "fi",
    "fo",
    "fr",
    "gl",
    "gu",
    "ha",
    "haw",
    "he",
    "hi",
    "hr",
    "ht",
    "hu",
    "hy",
    "id",
    "is",
    "it",
    "ja",
    "jw",
    "ka",
    "kk",
    "km",
    "kn",
    "ko",
    "la",
    "lb",
    "ln",
    "lo",
    "lt",
    "lv",
    "mg",
    "mi",
    "mk",
    "ml",
    "mn",
    "mr",
    "ms",
    "mt",
    "my",
    "ne",
    "nl",
    "nn",
    "no",
    "oc",
    "pa",
    "pl",
    "ps",
    "pt",
    "ro",
    "ru",
    "sa",
    "sd",
    "si",
    "sk",
    "sl",
    "sn",
    "so",
    "sq",
    "sr",
    "su",
    "sv",
    "sw",
    "ta",
    "te",
    "tg",
    "th",
    "tk",
    "tl",
    "tr",
    "tt",
    "uk",
    "ur",
    "uz",
    "vi",
    "wo",
    "yi",
    "yo",
    "zh",
]


@register_stt
class GladiaSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.GLADIA] = ServiceProviders.GLADIA
    model: str = Field(
        default="solaria-1",
        description="Gladia STT model.",
        json_schema_extra={"examples": GLADIA_STT_MODELS},
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={"examples": GLADIA_STT_LANGUAGES},
    )


STTConfig = Annotated[
    Union[
        DeepgramSTTConfiguration,
        CartesiaSTTConfiguration,
        OpenAISTTConfiguration,
        DograhSTTService,
        SpeechmaticsSTTConfiguration,
        SarvamSTTConfiguration,
        SpeachesSTTConfiguration,
        AssemblyAISTTConfiguration,
        GladiaSTTConfiguration,
    ],
    Field(discriminator="provider"),
]

###################################################### EMBEDDINGS ########################################################################

OPENAI_EMBEDDING_MODELS = ["text-embedding-3-small"]


@register_embeddings
class OpenAIEmbeddingsConfiguration(BaseEmbeddingsConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="text-embedding-3-small",
        description="OpenAI embedding model.",
        json_schema_extra={"examples": OPENAI_EMBEDDING_MODELS},
    )


OPENROUTER_EMBEDDING_MODELS = ["openai/text-embedding-3-small"]


@register_embeddings
class OpenRouterEmbeddingsConfiguration(BaseEmbeddingsConfiguration):
    provider: Literal[ServiceProviders.OPENROUTER] = ServiceProviders.OPENROUTER
    model: str = Field(
        default="openai/text-embedding-3-small",
        description="OpenRouter-hosted embedding model slug.",
        json_schema_extra={"examples": OPENROUTER_EMBEDDING_MODELS},
    )

    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Override only if proxying OpenRouter through your own gateway.",
    )


EmbeddingsConfig = Annotated[
    Union[OpenAIEmbeddingsConfiguration, OpenRouterEmbeddingsConfiguration],
    Field(discriminator="provider"),
]

ServiceConfig = Annotated[
    Union[LLMConfig, RealtimeConfig, TTSConfig, STTConfig, EmbeddingsConfig],
    Field(discriminator="provider"),
]
