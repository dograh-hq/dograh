import random
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Annotated, ClassVar, Dict, Literal, Type, TypeVar, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_serializer,
    model_validator,
)

from api.services.configuration.options import (
    AZURE_EMBEDDING_MODELS,
    AZURE_MODELS,
    AZURE_REALTIME_API_VERSIONS,
    AZURE_REALTIME_MODELS,
    AZURE_REALTIME_VOICES,
    AZURE_SPEECH_REGIONS,
    AZURE_SPEECH_STT_LANGUAGES,
    AZURE_SPEECH_TTS_LANGUAGES,
    AZURE_SPEECH_TTS_VOICES,
    CARTESIA_INK_2_STT_LANGUAGES,
    CARTESIA_INK_WHISPER_STT_LANGUAGES,
    CARTESIA_STT_LANGUAGES,
    CARTESIA_STT_MODELS,
    DEEPGRAM_BASE_URLS,
    DEEPGRAM_DEFAULT_BASE_URL,
    DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGE_OPTIONS,
    DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGES,
    DEEPGRAM_LANGUAGES,
    DEEPGRAM_STT_MODELS,
    ELEVENLABS_STT_LANGUAGES,
    ELEVENLABS_STT_MODELS,
    GLADIA_STT_LANGUAGES,
    GLADIA_STT_MODELS,
    GOOGLE_MODELS,
    GOOGLE_REALTIME_LANGUAGES,
    GOOGLE_REALTIME_MODELS,
    GOOGLE_REALTIME_VOICES,
    GOOGLE_STT_LANGUAGES,
    GOOGLE_STT_MODELS,
    GOOGLE_TTS_LANGUAGES,
    GOOGLE_TTS_MODELS,
    GOOGLE_TTS_VOICES,
    GOOGLE_VERTEX_REALTIME_LANGUAGES,
    GOOGLE_VERTEX_REALTIME_MODELS,
    GOOGLE_VERTEX_REALTIME_VOICES,
    SARVAM_LANGUAGES,
    SARVAM_LLM_MODELS,
    SARVAM_STT_LANGUAGES_V3,
    SARVAM_STT_LANGUAGES_V25,
    SARVAM_STT_MODELS,
    SARVAM_TTS_MODELS,
    SARVAM_V2_VOICES,
    SARVAM_V3_VOICES,
    SMALLEST_TTS_LANGUAGES,
    SMALLEST_TTS_MODELS,
    SMALLEST_TTS_PRO_VOICES,
    SMALLEST_TTS_VOICES,
    SONIOX_STT_LANGUAGES,
    SONIOX_STT_MODELS,
    SPEECHMATICS_STT_LANGUAGES,
)
from api.services.configuration.options.google import (
    GOOGLE_VERTEX_DEFAULT_LOCATION,
    GOOGLE_VERTEX_LOCATIONS,
    GOOGLE_VERTEX_MODELS,
)
from api.services.configuration.temperature import (
    resolve_temperature,
    temperature_field,
)


class ServiceType(Enum):
    LLM = auto()
    TTS = auto()
    STT = auto()
    EMBEDDINGS = auto()
    REALTIME = auto()


class ServiceProviders(str, Enum):
    OPENAI = "openai"
    ATLASCLOUD = "atlascloud"
    HOPPER = "hopper"
    DEEPGRAM = "deepgram"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    INWORLD = "inworld"
    CARTESIA = "cartesia"
    # NEUPHONIC = "neuphonic"
    ELEVENLABS = "elevenlabs"
    GOOGLE = "google"
    AZURE = "azure"
    AZURE_SPEECH = "azure_speech"
    DOGRAH = "dograh"
    SARVAM = "sarvam"
    SPEECHMATICS = "speechmatics"
    CAMB = "camb"
    AWS_BEDROCK = "aws_bedrock"
    SPEACHES = "speaches"
    HUGGINGFACE = "huggingface"
    ASSEMBLYAI = "assemblyai"
    GLADIA = "gladia"
    RIME = "rime"
    MINIMAX = "minimax"
    GOOGLE_VERTEX = "google_vertex"
    OPENAI_REALTIME = "openai_realtime"
    GROK_REALTIME = "grok_realtime"
    ULTRAVOX_REALTIME = "ultravox_realtime"
    GOOGLE_REALTIME = "google_realtime"
    GOOGLE_VERTEX_REALTIME = "google_vertex_realtime"
    AZURE_REALTIME = "azure_realtime"
    AWS_NOVA_SONIC = "aws_nova_sonic"
    SMALLEST = "smallest"
    XAI = "xai"
    LMNT = "lmnt"
    SPEECHIFY = "speechify"
    SONIOX = "soniox"


@dataclass(frozen=True)
class Provider:
    """Account identity and display metadata shared by registered services."""

    id: str
    title: str
    description: str | None = None
    provider_docs_url: str | None = None

    def __post_init__(self):
        if not self.id.strip() or not self.title.strip():
            raise ValueError("Provider ID and title are required")

    @property
    def schema_config(self) -> ConfigDict:
        extra = {}
        if self.description is not None:
            extra["description"] = self.description
        if self.provider_docs_url is not None:
            extra["provider_docs_url"] = self.provider_docs_url
        return ConfigDict(title=self.title, json_schema_extra=extra)


class BaseServiceConfiguration(BaseModel):
    provider_definition: ClassVar[Provider]
    provider: Literal[
        ServiceProviders.OPENAI,
        ServiceProviders.ATLASCLOUD,
        ServiceProviders.HOPPER,
        ServiceProviders.DEEPGRAM,
        ServiceProviders.GROQ,
        ServiceProviders.OPENROUTER,
        ServiceProviders.INWORLD,
        ServiceProviders.ELEVENLABS,
        ServiceProviders.GOOGLE,
        ServiceProviders.AZURE,
        ServiceProviders.AZURE_SPEECH,
        ServiceProviders.DOGRAH,
        ServiceProviders.AWS_BEDROCK,
        ServiceProviders.SPEACHES,
        ServiceProviders.HUGGINGFACE,
        ServiceProviders.ASSEMBLYAI,
        ServiceProviders.GLADIA,
        ServiceProviders.RIME,
        ServiceProviders.MINIMAX,
        ServiceProviders.GOOGLE_VERTEX,
        ServiceProviders.OPENAI_REALTIME,
        ServiceProviders.GROK_REALTIME,
        ServiceProviders.ULTRAVOX_REALTIME,
        ServiceProviders.GOOGLE_REALTIME,
        ServiceProviders.GOOGLE_VERTEX_REALTIME,
        ServiceProviders.AZURE_REALTIME,
        ServiceProviders.AWS_NOVA_SONIC,
        ServiceProviders.SARVAM,
        ServiceProviders.SMALLEST,
        ServiceProviders.XAI,
        ServiceProviders.LMNT,
        ServiceProviders.SPEECHIFY,
        ServiceProviders.SONIOX,
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


class BaseChatLLMConfiguration(BaseLLMConfiguration):
    # Realtime services intentionally do not inherit chat sampling settings.
    temperature: float | None

    @model_serializer(mode="wrap")
    def serialize_temperature(self, handler, info):
        data = handler(self)
        # Configuration persistence and secret merging exclude None values.
        # Temperature's explicit None must survive: omission restores the
        # application default (often 0.1) rather than the provider default.
        if (
            info.exclude_none
            and self.temperature is None
            and (not info.exclude_unset or "temperature" in self.model_fields_set)
            and (info.include is None or "temperature" in info.include)
            and (info.exclude is None or "temperature" not in info.exclude)
        ):
            data["temperature"] = None
        return data

    @model_validator(mode="after")
    def validate_model_temperature(self):
        # Normalization must not mark an omitted field as explicitly supplied.
        object.__setattr__(
            self,
            "temperature",
            resolve_temperature(
                self.provider,
                self.model,
                self.temperature,
                base_url=getattr(self, "base_url", None),
            ),
        )
        return self


class BaseTTSConfiguration(BaseServiceConfiguration):
    model: str


class BaseSTTConfiguration(BaseServiceConfiguration):
    model: str


class BaseEmbeddingsConfiguration(BaseServiceConfiguration):
    model: str


# Runtime IDs stay stable for saved configurations and service factories.
# Each registered class also carries its shared account provider definition.
REGISTRY: Dict[ServiceType, Dict[str, Type[BaseServiceConfiguration]]] = {
    ServiceType.LLM: {},
    ServiceType.TTS: {},
    ServiceType.STT: {},
    ServiceType.EMBEDDINGS: {},
    ServiceType.REALTIME: {},
}

T = TypeVar("T", bound=BaseServiceConfiguration)


def registered_provider_names(
    service_types: Iterable[ServiceType] | None = None,
) -> tuple[str, ...]:
    """Return canonical provider IDs from the live configuration registry."""

    selected_types = (
        tuple(service_types) if service_types is not None else tuple(REGISTRY)
    )
    providers = {
        str(getattr(provider, "value", provider))
        for service_type in selected_types
        for provider in REGISTRY.get(service_type, {})
    }
    return tuple(sorted(provider for provider in providers if provider))


def match_registered_provider(
    candidate: str,
    *,
    service_types: Iterable[ServiceType] | None = None,
) -> str | None:
    """Find a registered provider ID embedded in a runtime class/name hint.

    Separators and case are ignored, and the most specific (longest) provider
    ID wins. This keeps best-effort runtime discovery tied to registrations
    instead of maintaining another provider catalog at each consumer.
    """

    compact_candidate = "".join(
        character for character in candidate.casefold() if character.isalnum()
    )
    matches: list[tuple[int, str]] = []
    for provider in registered_provider_names(service_types):
        compact_provider = "".join(
            character for character in provider.casefold() if character.isalnum()
        )
        if compact_provider and compact_provider in compact_candidate:
            matches.append((len(compact_provider), provider))

    if not matches:
        return None
    return max(matches, key=lambda match: (match[0], match[1]))[1]


def get_provider_definition(provider_id: str) -> Provider | None:
    """Accept account IDs and historical runtime IDs using registration metadata."""
    for configurations in REGISTRY.values():
        for runtime_id, cls in configurations.items():
            definition = cls.provider_definition
            if provider_id in (runtime_id, definition.id):
                return definition
    return None


def get_service_configuration(
    service_type: ServiceType, provider_id: str
) -> Type[BaseServiceConfiguration] | None:
    definition = get_provider_definition(provider_id)
    if definition is None:
        return None
    return next(
        (
            cls
            for cls in REGISTRY[service_type].values()
            if cls.provider_definition.id == definition.id
        ),
        None,
    )


def register_service(service_type: ServiceType, *, provider: Provider):
    """Register one service per account provider and role, retaining runtime IDs."""

    def decorator(cls: Type[T]) -> Type[T]:
        field = cls.model_fields.get("provider")
        if field is None or field.is_required() or not isinstance(field.default, str):
            raise ValueError(f"Runtime provider default required for {cls.__name__}")
        runtime_id = field.default
        for role, configurations in REGISTRY.items():
            for existing_id, existing in configurations.items():
                definition = existing.provider_definition
                if definition.id == provider.id:
                    if definition != provider:
                        raise ValueError(
                            f"Conflicting metadata for provider {provider.id}"
                        )
                    if role == service_type:
                        raise ValueError(
                            f"Duplicate {service_type.name} provider {provider.id}"
                        )
                elif (
                    runtime_id in (existing_id, definition.id)
                    or provider.id == existing_id
                ):
                    raise ValueError(f"Ambiguous provider identity for {cls.__name__}")

        cls.provider_definition = provider
        cls.model_config = {**cls.model_config, **provider.schema_config}
        cls.model_rebuild(force=True)
        REGISTRY[service_type][runtime_id] = cls
        return cls

    return decorator


# Convenience decorators
def register_llm(*, provider: Provider):
    return register_service(ServiceType.LLM, provider=provider)


def register_tts(*, provider: Provider):
    return register_service(ServiceType.TTS, provider=provider)


def register_stt(*, provider: Provider):
    return register_service(ServiceType.STT, provider=provider)


def register_embeddings(*, provider: Provider):
    return register_service(ServiceType.EMBEDDINGS, provider=provider)


###################################################### LLM ########################################################################

# Account identities and labels are shared by each provider's services.
OPENAI_PROVIDER = Provider("openai", "OpenAI")
ATLASCLOUD_PROVIDER = Provider(
    "atlascloud",
    "Atlas Cloud",
    description="Atlas Cloud OpenAI-compatible LLM API.",
)
HOPPER_PROVIDER = Provider(
    "hopper",
    "Hopper",
    provider_docs_url="https://docs.withhopper.com",
)
GOOGLE_PROVIDER = Provider("google", "Google")
GROQ_PROVIDER = Provider("groq", "Groq")
OPENROUTER_PROVIDER = Provider("openrouter", "Open Router")
AZURE_OPENAI_PROVIDER = Provider("azure", "Azure OpenAI")
DOGRAH_PROVIDER = Provider("dograh", "Dograh")
AWS_BEDROCK_PROVIDER = Provider("aws_bedrock", "AWS Bedrock")
GOOGLE_VERTEX_PROVIDER = Provider("google_vertex", "Google Vertex")
GROK_REALTIME_PROVIDER = Provider("grok_realtime", "Grok")
ULTRAVOX_REALTIME_PROVIDER = Provider("ultravox_realtime", "Ultravox")
DEEPGRAM_PROVIDER = Provider("deepgram", "Deepgram")
ELEVENLABS_PROVIDER = Provider("elevenlabs", "ElevenLabs")
CARTESIA_PROVIDER = Provider("cartesia", "Cartesia")
XAI_PROVIDER = Provider("xai", "xAI")
LMNT_PROVIDER = Provider("lmnt", "LMNT")
MINIMAX_PROVIDER = Provider("minimax", "MiniMax")
SPEECHIFY_PROVIDER = Provider(
    "speechify",
    "Speechify",
    provider_docs_url="https://docs.speechify.ai",
)
INWORLD_PROVIDER = Provider(
    "inworld",
    "Inworld",
    description=(
        "Inworld AI streaming text-to-speech with built-in and cloned voices. "
        "Defaults to the Ashley system voice on inworld-tts-2."
    ),
    provider_docs_url="https://docs.inworld.ai/tts/tts",
)
SARVAM_PROVIDER = Provider("sarvam", "Sarvam")
CAMB_PROVIDER = Provider("camb", "Camb.ai")
RIME_PROVIDER = Provider("rime", "Rime")
SPEECHMATICS_PROVIDER = Provider("speechmatics", "Speechmatics")
ASSEMBLYAI_PROVIDER = Provider("assemblyai", "AssemblyAI")
GLADIA_PROVIDER = Provider("gladia", "Gladia")
SONIOX_PROVIDER = Provider("soniox", "Soniox")
SPEACHES_PROVIDER = Provider(
    "speaches",
    "Local Models (Speaches)",
    description=(
        "Self-hosted OpenAI-compatible local models. See the Speaches project "
        "for setup and supported backends."
    ),
    provider_docs_url="https://github.com/speaches-ai/speaches",
)
HUGGINGFACE_PROVIDER = Provider(
    "huggingface",
    "Hugging Face",
    description="Hosted Hugging Face Inference Providers API for usage-based inference.",
    provider_docs_url="https://huggingface.co/docs/inference-providers/en/index",
)
AZURE_SPEECH_PROVIDER = Provider(
    "azure_speech",
    "Azure Speech Services",
    description="Azure Cognitive Services Speech — TTS and STT via the Azure Speech SDK.",
    provider_docs_url="https://learn.microsoft.com/en-us/azure/ai-services/speech-service/",
)
AZURE_REALTIME_PROVIDER = Provider(
    "azure_realtime",
    "Azure OpenAI Realtime",
    description="Azure OpenAI Realtime API — low-latency speech-to-speech conversations.",
    provider_docs_url="https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/realtime-audio-quickstart",
)
AWS_NOVA_SONIC_PROVIDER = Provider(
    "aws_nova_sonic",
    "AWS Nova 2 Sonic",
    description=(
        "Amazon Bedrock's realtime speech-to-speech model. Uses AWS IAM "
        "credentials rather than a Bedrock API key."
    ),
    provider_docs_url=(
        "https://docs.aws.amazon.com/nova/latest/nova2-userguide/"
        "sonic-getting-started.html"
    ),
)

OPENAI_MODELS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-3.5-turbo",
]

ATLASCLOUD_API_BASE_URL = "https://api.atlascloud.ai/v1"
ATLASCLOUD_MODELS = [
    "qwen/qwen3.5-flash",
    "deepseek-ai/deepseek-v4-pro",
]

HOPPER_API_BASE_URL = "https://api.withhopper.com/v1"
HOPPER_MODELS = [
    "gemma-4-31b",
]

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
]
OPENROUTER_MODELS = [
    "openai/gpt-4.1",
    "openai/gpt-4.1-mini",
    "anthropic/claude-sonnet-4",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "deepseek/deepseek-chat-v3-0324",
]
DOGRAH_LLM_MODELS = ["default", "accurate", "fast", "lite", "zen"]
AWS_BEDROCK_MODELS = [
    "us.amazon.nova-pro-v1:0",
    "us.amazon.nova-lite-v1:0",
    "us.amazon.nova-micro-v1:0",
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
]


@register_llm(provider=OPENAI_PROVIDER)
class OpenAILLMService(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("openai", 0.1)
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="gpt-4.1",
        description="OpenAI chat model to use.",
        json_schema_extra={"examples": OPENAI_MODELS, "allow_custom_input": True},
    )
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Override only if using an OpenAI-compatible API (e.g. local LLM, proxy).",
    )


@register_llm(provider=ATLASCLOUD_PROVIDER)
class AtlasCloudLLMService(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("atlascloud", 0.1)
    provider: Literal[ServiceProviders.ATLASCLOUD] = ServiceProviders.ATLASCLOUD
    model: str = Field(
        default="qwen/qwen3.5-flash",
        description="Atlas Cloud OpenAI-compatible chat model identifier.",
        json_schema_extra={"examples": ATLASCLOUD_MODELS, "allow_custom_input": True},
    )
    base_url: str = Field(
        default=ATLASCLOUD_API_BASE_URL,
        description="Atlas Cloud OpenAI-compatible API endpoint.",
    )


@register_llm(provider=HOPPER_PROVIDER)
class HopperLLMConfiguration(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("hopper", 0.1)
    provider: Literal[ServiceProviders.HOPPER] = ServiceProviders.HOPPER
    api_key: str | list[str] = Field(
        description="API key from your Hopper console.",
        json_schema_extra={
            "docs_url": "https://withhopper.com/console/keys",
            "docs_label": "Create a key",
        },
    )
    model: str = Field(
        default="gemma-4-31b",
        description="Hopper chat model.",
        json_schema_extra={"examples": HOPPER_MODELS, "allow_custom_input": True},
    )


@register_llm(provider=GOOGLE_PROVIDER)
class GoogleLLMService(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("google", 0.1)
    provider: Literal[ServiceProviders.GOOGLE] = ServiceProviders.GOOGLE
    model: str = Field(
        default="gemini-3.5-flash",
        description="Gemini model on Google AI Studio (not Vertex).",
        json_schema_extra={"examples": GOOGLE_MODELS, "allow_custom_input": True},
    )


@register_llm(provider=GOOGLE_VERTEX_PROVIDER)
class GoogleVertexLLMConfiguration(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("google_vertex", 0.1)
    provider: Literal[ServiceProviders.GOOGLE_VERTEX] = ServiceProviders.GOOGLE_VERTEX
    model: str = Field(
        default="gemini-3.5-flash",
        description="Gemini model on Vertex AI.",
        json_schema_extra={
            "examples": GOOGLE_VERTEX_MODELS,
            "allow_custom_input": True,
        },
    )
    project_id: str = Field(description="Google Cloud project ID for Vertex AI.")
    location: str = Field(
        default=GOOGLE_VERTEX_DEFAULT_LOCATION,
        description=(
            "Vertex AI location, which decides where requests are processed. "
            "'eu' and 'us' are multi-regions that keep processing inside that "
            "geography; a single region such as 'europe-west4' pins it further; "
            "'global' routes anywhere in the world and carries no data "
            "residency guarantee. Model availability varies by location."
        ),
        json_schema_extra={
            "examples": list(GOOGLE_VERTEX_LOCATIONS),
            "allow_custom_input": True,
        },
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


@register_llm(provider=GROQ_PROVIDER)
class GroqLLMService(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("groq", 0.1)
    provider: Literal[ServiceProviders.GROQ] = ServiceProviders.GROQ
    model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq-hosted model identifier.",
        json_schema_extra={"examples": GROQ_MODELS, "allow_custom_input": True},
    )


@register_llm(provider=OPENROUTER_PROVIDER)
class OpenRouterLLMConfiguration(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("openrouter", 0.1)
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
    provider_order: list[str] = Field(
        default_factory=list,
        description=(
            "OpenRouter provider slugs to try first, in order, one per entry "
            "(e.g. groq), as listed on the model's OpenRouter page. Pinning a "
            "low-latency provider avoids OpenRouter's default price-weighted "
            "routing; other providers are still used if these are unavailable."
        ),
    )


@register_llm(provider=AZURE_OPENAI_PROVIDER)
class AzureLLMService(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("azure", 0.1)
    provider: Literal[ServiceProviders.AZURE] = ServiceProviders.AZURE
    model: str = Field(
        default="gpt-4.1-mini",
        description="Azure deployment name (not the upstream OpenAI model id).",
        json_schema_extra={"examples": AZURE_MODELS, "allow_custom_input": True},
    )

    endpoint: str = Field(
        description="Azure OpenAI resource endpoint (e.g. https://<resource>.openai.azure.com).",
    )


@register_llm(provider=DOGRAH_PROVIDER)
class DograhLLMService(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("dograh", None)
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: str = Field(
        default="default",
        description="Dograh-hosted model tier.",
        json_schema_extra={"examples": DOGRAH_LLM_MODELS, "allow_custom_input": True},
    )


@register_llm(provider=AWS_BEDROCK_PROVIDER)
class AWSBedrockLLMConfiguration(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("aws_bedrock", None)
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


@register_llm(provider=SPEACHES_PROVIDER)
class SpeachesLLMConfiguration(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("speaches", None)
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


HUGGINGFACE_LLM_MODELS = [
    "openai/gpt-oss-120b:cerebras",
    "deepseek-ai/DeepSeek-R1:fastest",
    "Qwen/Qwen3-Coder-480B-A35B-Instruct:fastest",
]


@register_llm(provider=HUGGINGFACE_PROVIDER)
class HuggingFaceLLMConfiguration(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("huggingface", 0.1)
    provider: Literal[ServiceProviders.HUGGINGFACE] = ServiceProviders.HUGGINGFACE
    model: str = Field(
        default="openai/gpt-oss-120b:cerebras",
        description="Hugging Face chat-completion model identifier, optionally with provider suffix.",
        json_schema_extra={
            "examples": HUGGINGFACE_LLM_MODELS,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="https://router.huggingface.co/v1",
        description="Hugging Face OpenAI-compatible chat-completions router base URL.",
    )
    bill_to: str | None = Field(
        default=None,
        description="Optional Hugging Face organization or user to bill using X-HF-Bill-To.",
    )


MINIMAX_MODELS = [
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed",
    "MiniMax-M3",
]


@register_llm(provider=MINIMAX_PROVIDER)
class MiniMaxLLMConfiguration(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("minimax", 1.0)
    provider: Literal[ServiceProviders.MINIMAX] = ServiceProviders.MINIMAX
    model: str = Field(
        default="MiniMax-M2.7",
        description="MiniMax chat model.",
        json_schema_extra={"examples": MINIMAX_MODELS, "allow_custom_input": True},
    )
    base_url: str = Field(
        default="https://api.minimax.io/v1",
        description="MiniMax OpenAI-compatible API endpoint.",
    )


@register_llm(provider=SARVAM_PROVIDER)
class SarvamLLMConfiguration(BaseChatLLMConfiguration):
    temperature: float | None = temperature_field("sarvam", 0.5)
    provider: Literal[ServiceProviders.SARVAM] = ServiceProviders.SARVAM
    model: str = Field(
        default="sarvam-105b-conversations",
        description="Sarvam chat model.",
        json_schema_extra={"examples": SARVAM_LLM_MODELS, "allow_custom_input": True},
    )
    base_url: str = Field(
        default="https://api.sarvam.ai/v1",
        description="Sarvam API base URL.",
    )


OPENAI_REALTIME_MODELS = [
    "gpt-live-1",
    "gpt-realtime-2.1",
    "gpt-realtime-2.1-mini",
    "gpt-realtime-2",
]
OPENAI_LIVE_VOICES = ["marin", "cedar"]
# ISO 639-1 codes accepted by the Realtime API's input_audio_transcription.
# Not exhaustive — the field allows custom input.
OPENAI_REALTIME_LANGUAGES = [
    "en",
    "es",
    "pt",
    "fr",
    "de",
    "it",
    "hi",
    "ja",
    "ko",
    "zh",
]
OPENAI_REALTIME_VOICES = [
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
]
AWS_NOVA_SONIC_MODELS = ["amazon.nova-2-sonic-v1:0"]
AWS_NOVA_SONIC_VOICES = [
    "tiffany",
    "matthew",
    "amy",
    "olivia",
    "kiara",
    "arjun",
    "ambre",
    "florian",
    "beatrice",
    "lorenzo",
    "tina",
    "lennart",
    "lupe",
    "carlos",
    "carolina",
    "leo",
]
AWS_NOVA_SONIC_REGIONS = [
    "us-east-1",
    "us-west-2",
    "eu-north-1",
    "ap-northeast-1",
]
AWS_NOVA_SONIC_ENDPOINTING_SENSITIVITIES = ["HIGH", "MEDIUM", "LOW"]


@register_service(ServiceType.REALTIME, provider=OPENAI_PROVIDER)
class OpenAIRealtimeLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.OPENAI_REALTIME] = (
        ServiceProviders.OPENAI_REALTIME
    )
    model: str = Field(
        default="gpt-realtime-2",
        description="Choose GPT-Live for full-duplex speech or a GPT-Realtime model.",
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
            "model_options": {"gpt-live-1": OPENAI_LIVE_VOICES},
            "allow_custom_input": True,
        },
    )
    language: str | None = Field(
        default=None,
        description=(
            "ISO 639-1 language code for input audio transcription (e.g. 'pt', 'es'). "
            "Improves transcription accuracy and latency. Leave unset to auto-detect."
        ),
        json_schema_extra={
            "examples": OPENAI_REALTIME_LANGUAGES,
            "allow_custom_input": True,
            "hidden_for_models": ["gpt-live-1"],
        },
    )
    backend_model: str = Field(
        default="gpt-5.4-mini",
        min_length=1,
        description=(
            "OpenAI Responses model that follows your workflow and calls tools. "
            "Uses the same API key; backend usage is billed separately from voice."
        ),
        json_schema_extra={
            "examples": ["gpt-5.4-mini"],
            "allow_custom_input": True,
            "visible_for_models": ["gpt-live-1"],
        },
    )

    @model_validator(mode="before")
    @classmethod
    def default_live_voice(cls, data):
        if (
            isinstance(data, dict)
            and data.get("model") == "gpt-live-1"
            and not data.get("voice")
        ):
            return {**data, "voice": "marin"}
        return data


@register_service(ServiceType.REALTIME, provider=AWS_NOVA_SONIC_PROVIDER)
class AWSNovaSonicRealtimeLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.AWS_NOVA_SONIC] = ServiceProviders.AWS_NOVA_SONIC
    model: str = Field(
        default="amazon.nova-2-sonic-v1:0",
        description="Amazon Nova 2 Sonic model ID.",
        json_schema_extra={
            "examples": AWS_NOVA_SONIC_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="matthew",
        description=(
            "Voice the model speaks in. Tiffany and Matthew are polyglot voices."
        ),
        json_schema_extra={
            "examples": AWS_NOVA_SONIC_VOICES,
            "allow_custom_input": True,
            "docs_url": (
                "https://docs.aws.amazon.com/nova/latest/nova2-userguide/"
                "sonic-language-support.html"
            ),
        },
    )
    aws_access_key: str = Field(
        default="",
        description=(
            "AWS access key ID with permission to invoke Nova 2 Sonic in Bedrock."
        ),
    )
    aws_secret_key: str = Field(
        default="",
        description="AWS secret access key paired with the access key ID.",
    )
    aws_session_token: str | None = Field(
        default=None,
        description="Optional AWS session token for temporary IAM credentials.",
    )
    aws_region: str = Field(
        default="us-east-1",
        description="AWS region where Nova 2 Sonic is enabled for the account.",
        json_schema_extra={
            "examples": AWS_NOVA_SONIC_REGIONS,
            "allow_custom_input": True,
        },
    )
    endpointing_sensitivity: Literal["HIGH", "MEDIUM", "LOW"] | None = Field(
        default=None,
        description=(
            "How quickly Nova decides the user has stopped speaking. Leave blank "
            "to use the model default."
        ),
        json_schema_extra={
            "examples": AWS_NOVA_SONIC_ENDPOINTING_SENSITIVITIES,
        },
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Sampling temperature for Nova 2 Sonic (0 to 1).",
        json_schema_extra={
            "docs_url": "https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-input-events.html",
            "docs_label": "Temperature documentation",
        },
    )
    max_tokens: int = Field(
        default=1024,
        ge=1,
        le=5000,
        description="Maximum response tokens.",
    )
    top_p: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="Nucleus-sampling threshold.",
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description=(
            "Not used for Nova 2 Sonic — authentication is via the AWS "
            "credentials above. Leave blank."
        ),
    )


GROK_REALTIME_MODELS = ["grok-voice-think-fast-1.0"]
GROK_REALTIME_VOICES = ["ara", "rex", "sal", "eve", "leo"]
ULTRAVOX_REALTIME_MODELS = ["ultravox-v0.7", "fixie-ai/ultravox"]


@register_service(ServiceType.REALTIME, provider=GROK_REALTIME_PROVIDER)
class GrokRealtimeLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.GROK_REALTIME] = ServiceProviders.GROK_REALTIME
    model: str = Field(
        default="grok-voice-think-fast-1.0",
        description="Grok realtime voice-agent model.",
        json_schema_extra={
            "examples": GROK_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="ara",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": GROK_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )


@register_service(ServiceType.REALTIME, provider=ULTRAVOX_REALTIME_PROVIDER)
class UltravoxRealtimeLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.ULTRAVOX_REALTIME] = (
        ServiceProviders.ULTRAVOX_REALTIME
    )
    model: str = Field(
        default="ultravox-v0.7",
        description="Ultravox realtime voice-agent model.",
        json_schema_extra={
            "examples": ULTRAVOX_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="Mark",
        description="Ultravox voice name or voice ID.",
    )
    temperature: float = Field(
        # Preserve OneShotInputParams' pre-existing on-wire default.
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Sampling temperature. Lower values give more predictable responses.",
        json_schema_extra={
            "docs_url": "https://docs.ultravox.ai/api-reference/calls/calls-post",
            "docs_label": "Temperature documentation",
        },
    )


@register_service(ServiceType.REALTIME, provider=GOOGLE_PROVIDER)
class GoogleRealtimeLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.GOOGLE_REALTIME] = (
        ServiceProviders.GOOGLE_REALTIME
    )
    model: str = Field(
        default="gemini-3.8-live",
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


@register_service(ServiceType.REALTIME, provider=GOOGLE_VERTEX_PROVIDER)
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
        default=GOOGLE_VERTEX_DEFAULT_LOCATION,
        description=(
            "Vertex AI location, which decides where requests are processed. "
            "'eu' and 'us' are multi-regions that keep processing inside that "
            "geography; a single region such as 'europe-west4' pins it further; "
            "'global' routes anywhere in the world and carries no data "
            "residency guarantee. Model availability varies by location."
        ),
        json_schema_extra={
            "examples": list(GOOGLE_VERTEX_LOCATIONS),
            "allow_custom_input": True,
        },
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


@register_service(ServiceType.REALTIME, provider=AZURE_REALTIME_PROVIDER)
class AzureRealtimeLLMConfiguration(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.AZURE_REALTIME] = ServiceProviders.AZURE_REALTIME
    model: str = Field(
        default="gpt-realtime",
        description="Azure OpenAI realtime deployment name.",
        json_schema_extra={
            "examples": AZURE_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    endpoint: str = Field(
        description="Azure OpenAI resource endpoint (e.g. https://<resource>.openai.azure.com).",
    )
    voice: str = Field(
        default="alloy",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": AZURE_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )
    api_version: str = Field(
        default="v1",
        description=(
            "Azure OpenAI Realtime protocol version. Use 'v1' for the GA API; "
            "date-based versions select the deprecated preview endpoint."
        ),
        json_schema_extra={
            "examples": AZURE_REALTIME_API_VERSIONS,
        },
    )


REALTIME_PROVIDERS = {
    ServiceProviders.OPENAI_REALTIME.value,
    ServiceProviders.GROK_REALTIME.value,
    ServiceProviders.ULTRAVOX_REALTIME.value,
    ServiceProviders.GOOGLE_REALTIME.value,
    ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
    ServiceProviders.AZURE_REALTIME.value,
    ServiceProviders.AWS_NOVA_SONIC.value,
}


LLMConfig = Annotated[
    Union[
        OpenAILLMService,
        AtlasCloudLLMService,
        HopperLLMConfiguration,
        GoogleVertexLLMConfiguration,
        GroqLLMService,
        OpenRouterLLMConfiguration,
        GoogleLLMService,
        AzureLLMService,
        DograhLLMService,
        AWSBedrockLLMConfiguration,
        SpeachesLLMConfiguration,
        HuggingFaceLLMConfiguration,
        MiniMaxLLMConfiguration,
        SarvamLLMConfiguration,
    ],
    Field(discriminator="provider"),
]

RealtimeConfig = Annotated[
    Union[
        OpenAIRealtimeLLMConfiguration,
        GrokRealtimeLLMConfiguration,
        UltravoxRealtimeLLMConfiguration,
        GoogleRealtimeLLMConfiguration,
        GoogleVertexRealtimeLLMConfiguration,
        AzureRealtimeLLMConfiguration,
        AWSNovaSonicRealtimeLLMConfiguration,
    ],
    Field(discriminator="provider"),
]

###################################################### TTS ########################################################################


@register_tts(provider=DEEPGRAM_PROVIDER)
class DeepgramTTSConfiguration(BaseServiceConfiguration):
    provider: Literal[ServiceProviders.DEEPGRAM] = ServiceProviders.DEEPGRAM
    voice: str = Field(
        default="aura-2-helena-en",
        description="Deepgram voice ID (model is inferred from the 'aura-N' prefix).",
    )
    speed: float | None = Field(
        default=None,
        ge=0.7,
        le=1.5,
        description=(
            "Speaking rate multiplier (1.0 is normal speed). Leave blank to use "
            "Deepgram's default. Supported by Aura-2 English and Spanish voices; "
            "0.9–1.5 is recommended for Spanish."
        ),
    )
    base_url: str = Field(
        default=DEEPGRAM_DEFAULT_BASE_URL,
        description=(
            "Deepgram API endpoint. This is what decides where your text is "
            "processed: use https://api.eu.deepgram.com to keep processing "
            "inside the EU, or https://api.au.deepgram.com for Australia. The "
            "same API key works on every regional endpoint."
        ),
        json_schema_extra={
            "examples": list(DEEPGRAM_BASE_URLS),
            "allow_custom_input": True,
        },
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


@register_tts(provider=ELEVENLABS_PROVIDER)
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
        json_schema_extra={
            "examples": ELEVENLABS_TTS_MODELS,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="https://api.elevenlabs.io",
        description=(
            "ElevenLabs API base URL. Override to use a Data Residency endpoint "
            "(e.g. https://api.eu.residency.elevenlabs.io) for GDPR / HIPAA / "
            "regional compliance."
        ),
    )


@register_tts(provider=GOOGLE_PROVIDER)
class GoogleTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.GOOGLE] = ServiceProviders.GOOGLE
    model: str = Field(
        default="chirp_3_hd",
        description=(
            "Google Cloud low-latency TTS engine. Dograh maps this to Pipecat's "
            "streaming Google TTS service for Chirp 3 HD and Journey voices."
        ),
        json_schema_extra={
            "examples": GOOGLE_TTS_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="en-US-Chirp3-HD-Charon",
        description="Google Cloud voice name. Use a Chirp 3 HD or Journey voice for streaming TTS.",
        json_schema_extra={
            "examples": GOOGLE_TTS_VOICES,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en-US",
        description="BCP-47 language code for synthesis.",
        json_schema_extra={
            "examples": GOOGLE_TTS_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=2.0,
        description="Speech speed multiplier for Google streaming TTS.",
    )
    location: str | None = Field(
        default=None,
        description=(
            "Optional Google Cloud regional Text-to-Speech endpoint (for example "
            "'us-central1'). Leave blank to use the default endpoint."
        ),
    )
    credentials: str | None = Field(
        default=None,
        description=(
            "Paste the entire Google Cloud service-account JSON. If omitted, "
            "the server falls back to Application Default Credentials (ADC)."
        ),
        json_schema_extra={"multiline": True},
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Not used for Google Cloud TTS. Leave blank.",
    )


OPENAI_TTS_MODELS = ["gpt-4o-mini-tts"]


@register_tts(provider=OPENAI_PROVIDER)
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
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Override only if using an OpenAI-compatible API (e.g. local TTS, proxy).",
    )


DOGRAH_TTS_MODELS = ["default"]


@register_tts(provider=DOGRAH_PROVIDER)
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
        json_schema_extra={"allow_custom_input": True},
    )
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Speed of the voice.")


CARTESIA_TTS_MODELS = ["sonic-3.6", "sonic-3.5", "sonic-3"]
INWORLD_TTS_MODELS = ["inworld-tts-2"]
INWORLD_TTS_VOICES = ["Ashley"]
INWORLD_TTS_LANGUAGES = ["en-US"]


@register_tts(provider=CARTESIA_PROVIDER)
class CartesiaTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.CARTESIA] = ServiceProviders.CARTESIA
    model: str = Field(
        default="sonic-3.6",
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
    language: str = Field(
        default="en",
        description="Cartesia language code for TTS synthesis (e.g. 'en', 'tr', 'fr', 'de').",
        json_schema_extra={"allow_custom_input": True},
    )


@register_tts(provider=INWORLD_PROVIDER)
class InworldTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.INWORLD] = ServiceProviders.INWORLD
    model: str = Field(
        default="inworld-tts-2",
        description="Inworld TTS model.",
        json_schema_extra={"examples": INWORLD_TTS_MODELS, "allow_custom_input": True},
    )
    voice: str = Field(
        default="Ashley",
        description=(
            "Inworld voice ID. Use Ashley for the default warm English voice, "
            "or a workspace voice ID for a cloned/custom voice."
        ),
        json_schema_extra={"examples": INWORLD_TTS_VOICES, "allow_custom_input": True},
    )
    language: str = Field(
        default="en-US",
        description="BCP-47 language code for synthesis.",
        json_schema_extra={
            "examples": INWORLD_TTS_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="Speech speed multiplier.",
    )
    delivery_mode: Literal["STABLE", "BALANCED", "CREATIVE"] = Field(
        default="BALANCED",
        description=(
            "Controls stability versus expressiveness for inworld-tts-2 "
            "(STABLE, BALANCED, or CREATIVE)."
        ),
    )


@register_tts(provider=SARVAM_PROVIDER)
class SarvamTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.SARVAM] = ServiceProviders.SARVAM
    model: str = Field(
        default="bulbul:v2",
        description="Sarvam TTS model (voice list depends on this).",
        json_schema_extra={"examples": SARVAM_TTS_MODELS},
    )
    voice: str = Field(
        default="anushka",
        description="Sarvam voice name or custom voice ID.",
        json_schema_extra={
            "examples": SARVAM_V2_VOICES,
            "allow_custom_input": True,
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
    speed: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="Speech speed multiplier.",
    )


CAMB_TTS_MODELS = ["mars-flash", "mars-pro", "mars-instruct"]


@register_tts(provider=CAMB_PROVIDER)
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


@register_tts(provider=RIME_PROVIDER)
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


@register_tts(provider=SPEACHES_PROVIDER)
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


MINIMAX_TTS_MODELS = ["speech-2.8-hd", "speech-2.8-turbo"]
MINIMAX_TTS_VOICES = [
    "English_Graceful_Lady",
    "English_Insightful_Speaker",
    "English_radiant_girl",
    "English_Persuasive_Man",
    "English_Lucky_Robot",
    "English_expressive_narrator",
]


@register_tts(provider=MINIMAX_PROVIDER)
class MiniMaxTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.MINIMAX] = ServiceProviders.MINIMAX
    model: str = Field(
        default="speech-2.8-hd",
        description="MiniMax TTS model.",
        json_schema_extra={"examples": MINIMAX_TTS_MODELS},
    )
    voice: str = Field(
        default="English_Graceful_Lady",
        description="MiniMax voice ID.",
        json_schema_extra={"examples": MINIMAX_TTS_VOICES, "allow_custom_input": True},
    )
    base_url: str = Field(
        default="https://api.minimax.io/v1/t2a_v2",
        description=(
            "MiniMax TTS API endpoint (must include the /v1/t2a_v2 path). "
            "Defaults to the global endpoint; override with "
            "https://api.minimaxi.chat/v1/t2a_v2 (mainland China) or "
            "https://api-uw.minimax.io/v1/t2a_v2 (US-West)."
        ),
    )
    speed: float = Field(
        default=1.0, ge=0.5, le=2.0, description="Speech speed (0.5 to 2.0)."
    )
    group_id: str = Field(
        description="MiniMax Group ID (found in your MiniMax dashboard under Account → Group).",
    )


@register_tts(provider=AZURE_SPEECH_PROVIDER)
class AzureSpeechTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.AZURE_SPEECH] = ServiceProviders.AZURE_SPEECH
    model: str = Field(
        default="neural",
        description="Azure Speech synthesis engine (neural voices only).",
        json_schema_extra={"examples": ["neural"]},
    )
    region: str = Field(
        default="eastus",
        description="Azure region for Speech Services (e.g. 'eastus', 'westeurope').",
        json_schema_extra={
            "examples": AZURE_SPEECH_REGIONS,
        },
    )
    voice: str = Field(
        default="en-US-AriaNeural",
        description="Azure Neural voice name (e.g. 'en-US-AriaNeural').",
        json_schema_extra={
            "examples": AZURE_SPEECH_TTS_VOICES,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en-US",
        description="BCP-47 language code for synthesis.",
        json_schema_extra={
            "examples": AZURE_SPEECH_TTS_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    speed: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="Speech speed multiplier (0.5 to 2.0).",
    )


SMALLEST_PROVIDER = Provider(
    "smallest",
    "Smallest AI",
    description="Smallest AI ultralow-latency TTS (Waves) and STT (Pulse) APIs.",
    provider_docs_url="https://smallest.ai/docs",
)


@register_tts(provider=SMALLEST_PROVIDER)
class SmallestAITTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.SMALLEST] = ServiceProviders.SMALLEST
    model: str = Field(
        default="lightning_v3.1",
        description="Smallest AI TTS model. lightning_v3.1_pro is the premium pool (American, British, Indian accents); lightning_v3.1 is the standard pool with 217 voices across 12 languages.",
        json_schema_extra={"examples": SMALLEST_TTS_MODELS},
    )
    voice: str = Field(
        default="sophia",
        description="Smallest AI voice ID. Available voices differ by model: lightning_v3.1 has a broad multilingual pool; lightning_v3.1_pro has premium American, British, and Indian accent voices (English + Hindi only).",
        json_schema_extra={
            "examples": list(SMALLEST_TTS_VOICES),
            "allow_custom_input": True,
            "model_options": {
                "lightning_v3.1": list(SMALLEST_TTS_VOICES),
                "lightning_v3.1_pro": list(SMALLEST_TTS_PRO_VOICES),
            },
        },
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code for synthesis.",
        json_schema_extra={
            "examples": SMALLEST_TTS_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    speed: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="Speech speed multiplier (0.5 to 2.0).",
    )


XAI_TTS_VOICES = ["eve", "ara", "leo", "rex", "sal"]


@register_tts(provider=XAI_PROVIDER)
class XAITTSConfiguration(BaseServiceConfiguration):
    provider: Literal[ServiceProviders.XAI] = ServiceProviders.XAI
    voice: str = Field(
        default="eve",
        description="xAI voice persona.",
        json_schema_extra={"examples": XAI_TTS_VOICES, "allow_custom_input": True},
    )
    language: str = Field(
        default="en",
        description="BCP-47 language code for synthesis (e.g. 'en', 'fr', 'de'), or 'auto' for automatic language detection.",
        json_schema_extra={"allow_custom_input": True},
    )

    @computed_field
    @property
    def model(self) -> str:
        # xAI TTS has no separate model selector; the voice fully specifies the
        # output. A constant keeps the shared `.model` contract satisfied.
        return "xai-tts"


LMNT_TTS_MODELS = ["aurora", "blizzard"]
LMNT_TTS_VOICES = ["lily", "daniel", "ava", "caleb", "leah", "zeke"]


class LmntTTSConfiguration(BaseTTSConfiguration):
    """Stored LMNT configurations remain readable after the provider's retirement."""

    model_config = LMNT_PROVIDER.schema_config
    provider: Literal[ServiceProviders.LMNT] = ServiceProviders.LMNT
    model: str = Field(
        default="aurora",
        description=(
            "LMNT TTS model. 'aurora' is the general-purpose model; 'blizzard' "
            "targets more expressive, conversational speech."
        ),
        json_schema_extra={"examples": LMNT_TTS_MODELS},
    )
    voice: str = Field(
        default="lily",
        description=(
            "LMNT voice ID. Use a stock voice name or a custom voice ID from "
            "your LMNT account."
        ),
        json_schema_extra={"examples": LMNT_TTS_VOICES, "allow_custom_input": True},
    )
    language: str = Field(
        default="en",
        description=(
            "Language code for synthesis (e.g. 'en', 'es', 'fr', 'de', 'pt', "
            "'zh', 'ko', 'hi')."
        ),
        json_schema_extra={"allow_custom_input": True},
    )


# Only the streaming-native Simba models: pipecat's SpeechifyHttpTTSService
# uses the /v1/audio/stream/with-timestamps endpoint, which rejects the legacy
# simba-english/simba-multilingual models.
SPEECHIFY_TTS_MODELS = [
    "simba-3.2",
    "simba-3.0",
]
SPEECHIFY_TTS_VOICES = ["beatrice_32", "geffen_32", "alicia", "alton"]
# The API rejects voices outside a model's allow-list (HTTP 400): simba-3.2
# only accepts voices that list it in GET /v1/voices, currently its dedicated
# "_32" voices. simba-3.0 accepts the general shared catalog.
SPEECHIFY_TTS_VOICES_BY_MODEL = {
    "simba-3.2": ["beatrice_32", "geffen_32"],
    "simba-3.0": SPEECHIFY_TTS_VOICES,
}
# Documented languages per model, used to filter the language dropdown. The
# API accepts other codes (synthesis succeeds), so this steers rather than
# hard-blocks: allow_custom_input still permits manual entry.
SPEECHIFY_TTS_LANGUAGES_BY_MODEL = {
    "simba-3.2": ["en"],
    "simba-3.0": ["en", "de", "es", "fr", "it", "pt-BR"],
}


@register_tts(provider=SPEECHIFY_PROVIDER)
class SpeechifyTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.SPEECHIFY] = ServiceProviders.SPEECHIFY
    model: str = Field(
        default="simba-3.2",
        description=(
            "Speechify TTS model. 'simba-3.2' is the streaming-native English "
            "model with the lowest latency; 'simba-3.0' adds German, Spanish, "
            "French, Italian, and Portuguese."
        ),
        json_schema_extra={"examples": SPEECHIFY_TTS_MODELS},
    )
    voice: str = Field(
        default="beatrice_32",
        description=(
            "Speechify voice ID. Options are filtered to voices available for "
            "the selected model; a custom or cloned voice ID must support the "
            "selected model (see GET /v1/voices), or synthesis fails."
        ),
        json_schema_extra={
            "examples": SPEECHIFY_TTS_VOICES,
            "allow_custom_input": True,
            "model_options": SPEECHIFY_TTS_VOICES_BY_MODEL,
        },
    )
    language: str = Field(
        default="en",
        description=(
            "Language code for synthesis (e.g. 'en', 'de', 'es', 'fr', 'it', "
            "'pt-BR'). Options are filtered to the selected model's documented "
            "languages; simba-3.2 is documented as English-only."
        ),
        json_schema_extra={
            "examples": SPEECHIFY_TTS_LANGUAGES_BY_MODEL["simba-3.0"],
            "allow_custom_input": True,
            "model_options": SPEECHIFY_TTS_LANGUAGES_BY_MODEL,
        },
    )


TTSConfig = Annotated[
    Union[
        DeepgramTTSConfiguration,
        GoogleTTSConfiguration,
        OpenAITTSService,
        ElevenlabsTTSConfiguration,
        CartesiaTTSConfiguration,
        InworldTTSConfiguration,
        DograhTTSService,
        SarvamTTSConfiguration,
        CambTTSConfiguration,
        RimeTTSConfiguration,
        SpeachesTTSConfiguration,
        MiniMaxTTSConfiguration,
        AzureSpeechTTSConfiguration,
        SmallestAITTSConfiguration,
        XAITTSConfiguration,
        LmntTTSConfiguration,
        SpeechifyTTSConfiguration,
    ],
    Field(discriminator="provider"),
]

###################################################### STT ########################################################################


@register_stt(provider=DEEPGRAM_PROVIDER)
class DeepgramSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.DEEPGRAM] = ServiceProviders.DEEPGRAM
    model: str = Field(
        default="nova-3-general",
        description="Deepgram STT model.",
        json_schema_extra={"examples": DEEPGRAM_STT_MODELS, "allow_custom_input": True},
    )
    language: str = Field(
        default="multi",
        description=(
            "Language code. 'multi' enables Nova-3 auto-detect and omits "
            "language hints for Flux multilingual auto-detect."
        ),
        json_schema_extra={
            "examples": DEEPGRAM_LANGUAGES,
            "allow_custom_input": True,
            "model_options": {
                "nova-3-general": DEEPGRAM_LANGUAGES,
                "nova-3-medical": DEEPGRAM_LANGUAGES,
                "flux-general-en": ("en",),
                "flux-general-multi": DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGE_OPTIONS,
            },
        },
    )
    language_hints: list[str] = Field(
        default_factory=list,
        description=(
            "More languages to bias Flux multilingual toward, on top of the "
            "language above. Pick several when callers switch between known "
            "languages; leave empty to rely on the language above."
        ),
        json_schema_extra={
            "examples": DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGES,
            "visible_for_models": ["flux-general-multi"],
        },
    )

    base_url: str = Field(
        default=DEEPGRAM_DEFAULT_BASE_URL,
        description=(
            "Deepgram API endpoint. This is what decides where call audio is "
            "processed: use https://api.eu.deepgram.com to keep processing "
            "inside the EU, or https://api.au.deepgram.com for Australia. The "
            "same API key works on every regional endpoint."
        ),
        json_schema_extra={
            "examples": list(DEEPGRAM_BASE_URLS),
            "allow_custom_input": True,
        },
    )

    @field_validator("language_hints")
    @classmethod
    def validate_language_hints(cls, hints: list[str]) -> list[str]:
        # Deepgram rejects the whole stream for an unsupported hint, so catch it
        # when the configuration is saved rather than when a call connects.
        unsupported = [
            hint
            for hint in hints
            if hint.split("-", 1)[0].lower() not in DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGES
        ]
        if unsupported:
            raise ValueError(
                "Unsupported Flux multilingual language hints: "
                + ", ".join(unsupported)
            )
        return hints


@register_stt(provider=CARTESIA_PROVIDER)
class CartesiaSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.CARTESIA] = ServiceProviders.CARTESIA
    model: str = Field(
        default="ink-whisper",
        description="Cartesia STT model.",
        json_schema_extra={"examples": CARTESIA_STT_MODELS},
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code. ink-2 currently supports English only.",
        json_schema_extra={
            "examples": CARTESIA_STT_LANGUAGES,
            "model_options": {
                "ink-2": CARTESIA_INK_2_STT_LANGUAGES,
                "ink-whisper": CARTESIA_INK_WHISPER_STT_LANGUAGES,
            },
        },
    )


OPENAI_STT_MODELS = ["gpt-4o-transcribe"]


@register_stt(provider=OPENAI_PROVIDER)
class OpenAISTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="gpt-4o-transcribe",
        description="OpenAI transcription model.",
        json_schema_extra={"examples": OPENAI_STT_MODELS},
    )
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Override only if using an OpenAI-compatible API (e.g. local STT, proxy).",
    )


@register_stt(provider=GOOGLE_PROVIDER)
class GoogleSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.GOOGLE] = ServiceProviders.GOOGLE
    model: str = Field(
        default="latest_long",
        description="Google Cloud Speech-to-Text V2 recognition model.",
        json_schema_extra={
            "examples": GOOGLE_STT_MODELS,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en-US",
        description="Primary BCP-47 language code for recognition.",
        json_schema_extra={
            "examples": GOOGLE_STT_LANGUAGES,
            "allow_custom_input": True,
            "docs_url": "https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages",
        },
    )
    location: str = Field(
        default="global",
        description="Google Cloud Speech-to-Text region (for example 'global' or 'us-central1').",
    )
    credentials: str | None = Field(
        default=None,
        description=(
            "Paste the entire Google Cloud service-account JSON. If omitted, "
            "the server falls back to Application Default Credentials (ADC)."
        ),
        json_schema_extra={"multiline": True},
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Not used for Google Cloud STT. Leave blank.",
    )


# Dograh STT Service
DOGRAH_STT_MODELS = ["default"]
DOGRAH_STT_LANGUAGES = DEEPGRAM_LANGUAGES
# Languages auto-detected when the Dograh STT language is "multi". Dograh STT runs
# Deepgram Flux multilingual under the hood, which only auto-detects this subset —
# not the full DOGRAH_STT_LANGUAGES list offered for explicit single-language selection.
DOGRAH_MULTILINGUAL_AUTODETECT_LANGUAGES = DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGES


@register_stt(provider=DOGRAH_PROVIDER)
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


@register_stt(provider=SARVAM_PROVIDER)
class SarvamSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.SARVAM] = ServiceProviders.SARVAM
    model: str = Field(
        default="saarika:v2.5",
        description=(
            "Sarvam STT model. saarika:v2.5 transcribes in the spoken language; "
            "saaras:v3 is the recommended model with flexible output modes."
        ),
        json_schema_extra={"examples": SARVAM_STT_MODELS},
    )
    language: str = Field(
        default="unknown",
        description=(
            "BCP-47 language code. Use unknown for automatic language detection."
        ),
        json_schema_extra={
            "examples": SARVAM_STT_LANGUAGES_V25,
            "model_options": {
                "saarika:v2.5": SARVAM_STT_LANGUAGES_V25,
                "saaras:v3": SARVAM_STT_LANGUAGES_V3,
            },
        },
    )


@register_stt(provider=SPEECHMATICS_PROVIDER)
class SpeechmaticsSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.SPEECHMATICS] = ServiceProviders.SPEECHMATICS
    model: str = Field(
        default="linden-1",
        description="Speechmatics Agent STT model.",
        json_schema_extra={"examples": ["linden-1"]},
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


@register_stt(provider=SPEACHES_PROVIDER)
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


HUGGINGFACE_STT_MODELS = [
    "openai/whisper-large-v3-turbo",
    "openai/whisper-large-v3",
]


@register_stt(provider=HUGGINGFACE_PROVIDER)
class HuggingFaceSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.HUGGINGFACE] = ServiceProviders.HUGGINGFACE
    model: str = Field(
        default="openai/whisper-large-v3-turbo",
        description="Hugging Face ASR model identifier served through Inference Providers.",
        json_schema_extra={
            "examples": HUGGINGFACE_STT_MODELS,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="https://router.huggingface.co/hf-inference",
        description="Hugging Face Inference Providers router base URL.",
    )
    bill_to: str | None = Field(
        default=None,
        description="Optional Hugging Face organization or user to bill using X-HF-Bill-To.",
    )
    return_timestamps: bool = Field(
        default=False,
        description="Request timestamp chunks when supported by the selected provider/model.",
    )


ASSEMBLYAI_STT_MODELS = ["u3-rt-pro"]
ASSEMBLYAI_STT_LANGUAGES = ["en", "es", "de", "fr", "pt", "it"]


@register_stt(provider=ASSEMBLYAI_PROVIDER)
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


@register_stt(provider=GLADIA_PROVIDER)
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


@register_stt(provider=SONIOX_PROVIDER)
class SonioxSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.SONIOX] = ServiceProviders.SONIOX
    model: str = Field(
        default="stt-rt-v5",
        description="Soniox real-time STT model.",
        json_schema_extra={"examples": SONIOX_STT_MODELS, "allow_custom_input": True},
    )
    language: str = Field(
        default="multi",
        description=(
            "ISO 639-1 language code, sent as a language hint. 'multi' sends no "
            "hint and lets Soniox auto-detect the language."
        ),
        json_schema_extra={
            "examples": SONIOX_STT_LANGUAGES,
            "docs_url": "https://soniox.com/docs/stt/concepts/supported-languages",
        },
    )


@register_stt(provider=AZURE_SPEECH_PROVIDER)
class AzureSpeechSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.AZURE_SPEECH] = ServiceProviders.AZURE_SPEECH
    model: str = Field(
        default="latest_long",
        description="Azure Speech recognition model (use 'latest_long' for continuous recognition).",
        json_schema_extra={"examples": ["latest_long", "latest_short"]},
    )
    region: str = Field(
        default="eastus",
        description="Azure region for Speech Services (e.g. 'eastus', 'westeurope').",
        json_schema_extra={
            "examples": AZURE_SPEECH_REGIONS,
        },
    )
    language: str = Field(
        default="en-US",
        description="BCP-47 language code for recognition.",
        json_schema_extra={
            "examples": AZURE_SPEECH_STT_LANGUAGES,
            "allow_custom_input": True,
        },
    )


SMALLEST_STT_MODELS = ["pulse"]
SMALLEST_STT_LANGUAGES = [
    "en",
    "hi",
    "fr",
    "de",
    "es",
    "it",
    "nl",
    "pl",
    "ru",
    "pt",
    "bn",
    "gu",
    "kn",
    "ml",
    "mr",
    "ta",
    "te",
    "pa",
    "or",
    "bg",
    "cs",
    "da",
    "et",
    "fi",
    "hu",
    "lt",
    "lv",
    "mt",
    "ro",
    "sk",
    "sv",
    "uk",
]


@register_stt(provider=ELEVENLABS_PROVIDER)
class ElevenlabsSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.ELEVENLABS] = ServiceProviders.ELEVENLABS
    model: str = Field(
        default="scribe_v2_realtime",
        description="ElevenLabs realtime STT model.",
        json_schema_extra={
            "examples": ELEVENLABS_STT_MODELS,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en",
        description=(
            "ISO 639-1 language code for transcription. "
            "Use 'auto' to let ElevenLabs detect the language."
        ),
        json_schema_extra={
            "examples": ELEVENLABS_STT_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="https://api.elevenlabs.io",
        description=(
            "ElevenLabs API base URL. Override to use a Data Residency endpoint "
            "(e.g. https://api.eu.residency.elevenlabs.io) for GDPR / HIPAA / "
            "regional compliance."
        ),
    )


@register_stt(provider=SMALLEST_PROVIDER)
class SmallestAISTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.SMALLEST] = ServiceProviders.SMALLEST
    model: str = Field(
        default="pulse",
        description="Smallest AI STT model. Supports 38 languages with real-time streaming.",
        json_schema_extra={"examples": SMALLEST_STT_MODELS},
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code for transcription.",
        json_schema_extra={
            "examples": SMALLEST_STT_LANGUAGES,
            "allow_custom_input": True,
        },
    )


STTConfig = Annotated[
    Union[
        DeepgramSTTConfiguration,
        CartesiaSTTConfiguration,
        OpenAISTTConfiguration,
        GoogleSTTConfiguration,
        DograhSTTService,
        SpeechmaticsSTTConfiguration,
        SarvamSTTConfiguration,
        SpeachesSTTConfiguration,
        HuggingFaceSTTConfiguration,
        AssemblyAISTTConfiguration,
        GladiaSTTConfiguration,
        SonioxSTTConfiguration,
        AzureSpeechSTTConfiguration,
        SmallestAISTTConfiguration,
        ElevenlabsSTTConfiguration,
    ],
    Field(discriminator="provider"),
]

###################################################### EMBEDDINGS ########################################################################

OPENAI_EMBEDDING_MODELS = ["text-embedding-3-small"]


@register_embeddings(provider=OPENAI_PROVIDER)
class OpenAIEmbeddingsConfiguration(BaseEmbeddingsConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="text-embedding-3-small",
        description="OpenAI embedding model.",
        json_schema_extra={"examples": OPENAI_EMBEDDING_MODELS},
    )
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Override only if using an OpenAI-compatible API (e.g. Azure-OpenAI, vLLM, LocalAI).",
    )


OPENROUTER_EMBEDDING_MODELS = ["openai/text-embedding-3-small"]


@register_embeddings(provider=OPENROUTER_PROVIDER)
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


@register_embeddings(provider=AZURE_OPENAI_PROVIDER)
class AzureOpenAIEmbeddingsConfiguration(BaseEmbeddingsConfiguration):
    provider: Literal[ServiceProviders.AZURE] = ServiceProviders.AZURE
    model: str = Field(
        default="text-embedding-3-small",
        description=(
            "Azure OpenAI embedding deployment name. The deployment must return "
            "1536-dimensional embeddings."
        ),
        json_schema_extra={
            "examples": AZURE_EMBEDDING_MODELS,
            "allow_custom_input": True,
        },
    )
    endpoint: str = Field(
        description="Azure OpenAI resource endpoint (e.g. https://<resource>.openai.azure.com).",
    )
    api_version: str = Field(
        default="2024-02-15-preview",
        description="Azure OpenAI API version for embeddings.",
    )


DOGRAH_EMBEDDING_MODELS = ["dograh_embedding_v1"]


@register_embeddings(provider=DOGRAH_PROVIDER)
class DograhEmbeddingsConfiguration(BaseEmbeddingsConfiguration):
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: str = Field(
        default="dograh_embedding_v1",
        description="Dograh-managed embedding model.",
        json_schema_extra={"examples": DOGRAH_EMBEDDING_MODELS},
    )


EmbeddingsConfig = Annotated[
    Union[
        OpenAIEmbeddingsConfiguration,
        OpenRouterEmbeddingsConfiguration,
        AzureOpenAIEmbeddingsConfiguration,
        DograhEmbeddingsConfiguration,
    ],
    Field(discriminator="provider"),
]

ServiceConfig = Annotated[
    Union[LLMConfig, RealtimeConfig, TTSConfig, STTConfig, EmbeddingsConfig],
    Field(discriminator="provider"),
]
