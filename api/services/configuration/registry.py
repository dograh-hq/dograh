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


class BaseServiceConfiguration(BaseModel):
    provider: Literal[
        ServiceProviders.OPENAI,
        ServiceProviders.DEEPGRAM,
        ServiceProviders.GROQ,
        ServiceProviders.ELEVENLABS,
        ServiceProviders.GOOGLE,
        ServiceProviders.AZURE,
        ServiceProviders.DOGRAH,
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


class OpenAIModel(str, Enum):
    GPT3_5_TURBO = "gpt-3.5-turbo"
    GPT4_1 = "gpt-4.1"
    GPT4_1_MINI = "gpt-4.1-mini"
    GPT4_1_NANO = "gpt-4.1-nano"
    GPT5 = "gpt-5"
    GPT5_MINI = "gpt-5-mini"
    GPT5_NANO = "gpt-5-nano"


@register_llm
class OpenAILLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: OpenAIModel = OpenAIModel.GPT4_1
    api_key: str


class GoogleModel(str, Enum):
    GEMINI_2_0_FLASH = "gemini-2.0-flash"
    GEMINI_2_0_FLASH_LITE = "gemini-2.0-flash-lite"
    GEMINI_2_5_FLASH = "gemini-2.5-flash"
    GEMINI_2_5_FLASH_LITE = "gemini-2.5-flash-lite"


@register_llm
class GoogleLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.GOOGLE] = ServiceProviders.GOOGLE
    model: GoogleModel = GoogleModel.GEMINI_2_0_FLASH
    api_key: str


class GroqModel(str, Enum):
    LLAMA_3_3_70B = "llama-3.3-70b-versatile"
    DEEPSEEK_R1_DISTILL_LLAMA_70B = "deepseek-r1-distill-llama-70b"
    QUEN_QWQ_32B = "qwen-qwq-32b"
    LLAMA_4_SCOUT_17B_16E_INSTRUCT = "meta-llama/llama-4-scout-17b-16e-instruct"
    LLAMA_4_MAVERICK_17B_128E_INSTRUCT = "meta-llama/llama-4-maverick-17b-128e-instruct"
    GEMMA2_9B_IT = "gemma2-9b-it"
    LLAMA_3_1_8B_INSTANT = "llama-3.1-8b-instant"
    OPENAI_GPT_OSS_120B = "openai/gpt-oss-120b"


@register_llm
class GroqLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.GROQ] = ServiceProviders.GROQ
    model: GroqModel = GroqModel.LLAMA_3_3_70B
    api_key: str


class AzureModel(str, Enum):
    GPT4_1_MINI = "gpt-4.1-mini"


@register_llm
class AzureLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.AZURE] = ServiceProviders.AZURE
    model: AzureModel = AzureModel.GPT4_1_MINI
    api_key: str
    endpoint: str


# Dograh LLM Service
class DograhLLMModel(str, Enum):
    DEFAULT = "default"
    ACCURATE = "accurate"
    FAST = "fast"
    LITE = "lite"
    ZEN = "zen"
    ZEN_LITE = "zen_lite"


@register_llm
class DograhLLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: DograhLLMModel = DograhLLMModel.DEFAULT
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


class DeepgramVoice(str, Enum):
    HELENA = "aura-2-helena-en"
    THALIA = "aura-2-thalia-en"


@register_tts
class DeepgramTTSConfiguration(BaseServiceConfiguration):
    provider: Literal[ServiceProviders.DEEPGRAM] = ServiceProviders.DEEPGRAM
    voice: DeepgramVoice = DeepgramVoice.HELENA
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


class ElevenlabsVoice(str, Enum):
    ALEXANDRA = "Alexandra - 3dzJXoCYueSQiptQ6euE"
    AMY = "Amy - oGn4Ha2pe2vSJkmIJgLQ"
    ANGELA = "Angela - FUfBrNit0NNZAwb58KWH"
    ARIA = "Aria - 9BWtsMINqrJLrRacOk9x"
    CHELSEA = "Chelsea - NHRgOEwqx5WZNClv5sat"
    CHRISTINA = "Christina - X03mvPuTfprif8QBAVeJ"
    CLARA = "Clara - ZIlrSGI4jZqobxRKprJz"
    CLYDE = "Clyde - 2EiwWnXFnvU5JabPnv8n"
    DAVE = "Dave - CYw3kZ02Hs0563khs1Fj"
    DOMI = "Domi - AZnzlk1XvdvUeBnXmlld"
    DREW = "Drew - 29vD33N1CtxCmqQRPOHJ"
    ELENA = "Elena_German - iFJwt4O7E3aafIpJFfcu"
    EVE = "Eve - BZgkqPqms7Kj9ulSkVzn"
    FIN = "Fin - D38z5RcWu1voky8WS1ja"
    HOPE_BESTIE = "Hope_Bestie - uYXf8XasLslADfZ2MB4u"
    HOPE_NATURAL = "Hope_Natural - OYTbf65OHHFELVut7v2H"
    JARNATHAN = "Jarnathan - c6SfcYrb2t09NHXiT80T"
    JENNA = "Jenna - C2BkQxlGNzBn7WD2bqfR"
    JESSICA = "Jessica - cgSgspJ2msm6clMCkdW9"
    JOHANNA = "Johanna_German - YYDsZT3K2y6tv7X1aj6N"
    JUNIPER = "Juniper - aMSt68OGf4xUZAnLpTU8"
    LAUREN = "Lauren - 3liN8q8YoeB9Hk6AboKe"
    LINA = "Lina - oWjuL7HSoaEJRMDMP3HD"
    MONIKA = "Monika_Hindi_8 - 2bNrEsM0omyhLiEyOwqY"
    OLIVIA = "Olivia - 1rviaVF7GGGkTU36HNpz"
    PAUL = "Paul - 5Q0t7uMcjvnagumLfvZi"
    RACHEL = "Rachel - 21m00Tcm4TlvDq8ikWAM"
    ROGER = "Roger - CwhRBWXzGAHq8TQ4Fs17"
    SAMI_REAL = "Sami_Real - O4cGUVdAocn0z4EpQ9yF"
    SARAH = "Sarah - EXAVITQu4vr4xnSDxMaL"
    SIA = "Sia_Hindi_10 - ryIIztHPLYSJ74ueXxnO"


class ElevenlabsModel(str, Enum):
    FLASH_2 = "eleven_flash_v2_5"


@register_tts
class ElevenlabsTTSConfiguration(BaseServiceConfiguration):
    provider: Literal[ServiceProviders.ELEVENLABS] = ServiceProviders.ELEVENLABS
    voice: ElevenlabsVoice = ElevenlabsVoice.RACHEL
    speed: float = Field(default=1.0, ge=0.1, le=2.0, description="Speed of the voice")
    model: ElevenlabsModel = ElevenlabsModel.FLASH_2
    api_key: str


class OpenAIVoice(str, Enum):
    ALLY = "alloy"


class OpenAITTSModel(str, Enum):
    GPT_4o_MINI = "gpt-4o-mini-tts"


@register_tts
class OpenAITTSService(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: OpenAITTSModel = OpenAITTSModel.GPT_4o_MINI
    voice: OpenAIVoice = OpenAIVoice.ALLY
    api_key: str


# class NeuphonicVoice(str, Enum):
#     EMILY = "Emily - fc854436-2dac-4d21-aa69-ae17b54e98eb"


# @register_tts
# class NeuphonicTTSService(BaseTTSConfiguration):
#     provider: Literal[ServiceProviders.NEUPHONIC] = ServiceProviders.NEUPHONIC
#     voice: NeuphonicVoice = NeuphonicVoice.EMILY
#     model: str = "NA"
#     api_key: str


# Dograh TTS Service
class DograhVoice(str, Enum):
    DEFAULT = "default"
    JOEY = "joey"
    RACHEL = "rachel"


class DograhTTSModel(str, Enum):
    DEFAULT = "default"


@register_tts
class DograhTTSService(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: DograhTTSModel = DograhTTSModel.DEFAULT
    voice: DograhVoice = DograhVoice.DEFAULT
    api_key: str


TTSConfig = Annotated[
    Union[
        DeepgramTTSConfiguration,
        OpenAITTSService,
        ElevenlabsTTSConfiguration,
        DograhTTSService,
    ],
    Field(discriminator="provider"),
]

###################################################### STT ########################################################################


class DeepgramSTTModel(str, Enum):
    NOVA_3_GENERAL = "nova-3-general"


@register_stt
class DeepgramSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.DEEPGRAM] = ServiceProviders.DEEPGRAM
    model: DeepgramSTTModel = DeepgramSTTModel.NOVA_3_GENERAL
    api_key: str


@register_stt
class CartesiaSTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.CARTESIA] = ServiceProviders.CARTESIA
    api_key: str


class OpenAISTTModel(str, Enum):
    GPT_4o_TRANSCRIBE = "gpt-4o-transcribe"


@register_stt
class OpenAISTTConfiguration(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: OpenAISTTModel = OpenAISTTModel.GPT_4o_TRANSCRIBE
    api_key: str


# Dograh STT Service
class DograhSTTModel(str, Enum):
    DEFAULT = "default"


@register_stt
class DograhSTTService(BaseSTTConfiguration):
    provider: Literal[ServiceProviders.DOGRAH] = ServiceProviders.DOGRAH
    model: DograhSTTModel = DograhSTTModel.DEFAULT
    api_key: str


STTConfig = Annotated[
    Union[DeepgramSTTConfiguration, OpenAISTTConfiguration, DograhSTTService],
    Field(discriminator="provider"),
]

ServiceConfig = Annotated[
    Union[LLMConfig, TTSConfig, STTConfig], Field(discriminator="provider")
]
