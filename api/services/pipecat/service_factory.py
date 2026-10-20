import hashlib
import json
import re
from functools import wraps
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode, urlparse, urlunparse

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.constants import MPS_API_URL
from api.errors.failure import (
    ErrorSource,
    annotate_failure_metadata,
    classify_exception,
    log_failure,
)
from api.services.configuration.options import (
    DEEPGRAM_DEFAULT_BASE_URL,
    DEEPGRAM_FLUX_MODELS,
    GOOGLE_VERTEX_DEFAULT_LOCATION,
)
from api.services.configuration.registry import (
    ATLASCLOUD_API_BASE_URL,
    ELEVENLABS_DIALOGUE_TTS_MODEL_PATTERN,
    HOPPER_API_BASE_URL,
    REGISTRY,
    BaseSTTConfiguration,
    ServiceProviders,
    ServiceType,
)
from api.services.configuration.temperature import resolve_temperature
from api.services.pipecat.fallback_llm import FallbackLLMProcessor, FallbackRoute
from api.services.pipecat.gemini_json_schema_adapter import (
    DograhGeminiJSONSchemaAdapter,
)
from api.services.pipecat.gemini_spoken_tool_calls import GeminiSpokenToolCallMixin
from api.services.pipecat.google_credentials import (
    get_google_credentials,
    prepare_google_credentials,
)
from api.services.pipecat.openai_spoken_tool_calls import OpenAISpokenToolCallMixin
from api.services.pipecat.spoken_tool_calls import SpokenToolCallMixin
from api.utils.url_security import validate_user_configured_service_url
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.services.assemblyai.stt import (
    AssemblyAISTTService,
    AssemblyAISTTSettings,
    is_u3_pro_model,
)
from pipecat.services.aws.llm import AWSBedrockLLMService, AWSBedrockLLMSettings
from pipecat.services.azure.llm import AzureLLMService as BaseAzureLLMService
from pipecat.services.azure.llm import AzureLLMSettings
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
from pipecat.services.deepgram.flux.tts import (
    DeepgramFluxTTSService,
    DeepgramFluxTTSSettings,
)
from pipecat.services.deepgram.stt import DeepgramSTTService, DeepgramSTTSettings
from pipecat.services.deepgram.tts import DeepgramTTSService, DeepgramTTSSettings
from pipecat.services.dograh.flux.stt import DograhFluxSTTService
from pipecat.services.dograh.llm import DograhLLMService as BaseDograhLLMService
from pipecat.services.dograh.stt import DograhSTTService, DograhSTTSettings
from pipecat.services.dograh.tts import DograhTTSService, DograhTTSSettings
from pipecat.services.elevenlabs.dialogue.tts import (
    ElevenLabsDialogueTTSService,
    ElevenLabsDialogueTTSSettings,
)
from pipecat.services.elevenlabs.stt import (
    CommitStrategy,
    ElevenLabsRealtimeSTTService,
    ElevenLabsRealtimeSTTSettings,
)
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService, ElevenLabsTTSSettings
from pipecat.services.gladia.stt import GladiaSTTService, GladiaSTTSettings
from pipecat.services.google.llm import GoogleLLMService, GoogleLLMSettings
from pipecat.services.google.stt import GoogleSTTService, GoogleSTTSettings
from pipecat.services.google.tts import GoogleTTSService, GoogleTTSSettings
from pipecat.services.google.vertex.llm import (
    GoogleVertexLLMService,
    GoogleVertexLLMSettings,
)
from pipecat.services.groq.llm import GroqLLMService as BaseGroqLLMService
from pipecat.services.groq.llm import GroqLLMSettings
from pipecat.services.huggingface.llm import (
    HuggingFaceLLMService as BaseHuggingFaceLLMService,
)
from pipecat.services.huggingface.llm import HuggingFaceLLMSettings
from pipecat.services.huggingface.stt import (
    HuggingFaceSTTService,
    HuggingFaceSTTSettings,
)
from pipecat.services.inworld.tts import InworldTTSService, InworldTTSSettings
from pipecat.services.minimax.llm import MiniMaxLLMService as BaseMiniMaxLLMService
from pipecat.services.minimax.tts import MiniMaxTTSService, MiniMaxTTSSettings
from pipecat.services.openai._constants import OPENAI_SAMPLE_RATE
from pipecat.services.openai.base_llm import OpenAILLMSettings
from pipecat.services.openai.llm import OpenAILLMService as BaseOpenAILLMService
from pipecat.services.openai.stt import (
    OpenAISTTService,
    OpenAISTTSettings,
)
from pipecat.services.openai.tts import OpenAITTSService, OpenAITTSSettings
from pipecat.services.openrouter.llm import (
    OpenRouterLLMService as BaseOpenRouterLLMService,
)
from pipecat.services.openrouter.llm import OpenRouterLLMSettings
from pipecat.services.rime.tts import RimeTTSService, RimeTTSSettings
from pipecat.services.sarvam.llm import SarvamLLMService as BaseSarvamLLMService
from pipecat.services.sarvam.llm import SarvamLLMSettings
from pipecat.services.sarvam.stt import SarvamSTTService, SarvamSTTSettings
from pipecat.services.sarvam.tts import SarvamTTSService, SarvamTTSSettings
from pipecat.services.smallest.stt import SmallestSTTService, SmallestSTTSettings
from pipecat.services.smallest.tts import SmallestTTSService, SmallestTTSSettings
from pipecat.services.soniox.stt import (
    SonioxContextObject,
    SonioxSTTService,
    SonioxSTTSettings,
)
from pipecat.services.speaches.llm import SpeachesLLMService as BaseSpeachesLLMService
from pipecat.services.speaches.llm import SpeachesLLMSettings
from pipecat.services.speaches.stt import SpeachesSTTService, SpeachesSTTSettings
from pipecat.services.speaches.tts import SpeachesTTSService, SpeachesTTSSettings
from pipecat.services.speechmatics.stt import (
    SpeechmaticsSTTService,
    SpeechmaticsSTTSettings,
)
from pipecat.services.stt_service import STTService, STTSettings
from pipecat.services.tts_service import TextAggregationMode
from pipecat.services.xai.tts import XAITTSService, XAIWebsocketTTSSettings
from pipecat.transcriptions.language import Language
from pipecat.utils.text.xml_function_tag_filter import XMLFunctionTagFilter
from pipecat.utils.types import NOT_GIVEN, NotGiven

if TYPE_CHECKING:
    from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
    from api.services.pipecat.audio_config import AudioConfig


def _report_service_factory_failures(
    source: ErrorSource,
    *,
    config_section: str | None = None,
    provider_argument: int | None = None,
):
    """Classify constructor failures and tag successful services for ErrorFrames."""

    def decorator(factory):
        @wraps(factory)
        def wrapped(*args, **kwargs):
            provider = None
            if config_section:
                user_config = args[0] if args else kwargs.get("user_config")
                config = getattr(user_config, config_section, None)
                provider = getattr(config, "provider", None)
            elif provider_argument is not None:
                if len(args) > provider_argument:
                    provider = args[provider_argument]
                else:
                    provider = kwargs.get("provider")

            provider_value = getattr(provider, "value", provider)
            error_owner = (
                "operator" if str(provider_value).lower() == "dograh" else "user"
            )
            try:
                service = factory(*args, **kwargs)
            except Exception as exc:
                log_failure(
                    classify_exception(
                        exc,
                        source=source,
                        provider=provider,
                        error_owner=error_owner,
                    )
                )
                raise

            service = annotate_failure_metadata(
                service,
                source=source,
                provider=provider,
                error_owner=error_owner,
            )
            from api.services.campaign.circuit_breaker import (
                register_campaign_provider_error_handler,
            )

            register_campaign_provider_error_handler(
                service, provider=provider, source=source
            )
            return service

        return wrapped

    return decorator


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


def _resolve_deepgram_flux_language_hint(language: str | None) -> Language | None:
    """Resolve a supported BCP-47 language or locale to its Flux base language."""
    base_language = (language or "").split("-", 1)[0].lower()
    return DEEPGRAM_FLUX_LANGUAGE_HINTS.get(base_language)


def _resolve_deepgram_flux_language_hints(
    language: str | None, hints: list[str] | None
) -> list[Language]:
    return list(
        dict.fromkeys(
            hint
            for hint in map(
                _resolve_deepgram_flux_language_hint, [language, *(hints or [])]
            )
            if hint
        )
    )


def dograh_stt_uses_flux_language(language: str | None) -> bool:
    if not language or language.lower() == "multi":
        return True
    return _resolve_deepgram_flux_language_hint(language) is not None


def _resolve_elevenlabs_stt_language(
    language_code: str | None,
) -> Language | str | None:
    if not language_code or language_code == "auto":
        return None
    try:
        return Language(language_code)
    except ValueError:
        return language_code


def _elevenlabs_websocket_url(base_url: str) -> str:
    """Normalize an ElevenLabs API base URL for WebSocket clients."""
    base_url = base_url.strip()
    parsed = urlparse(base_url)
    if not parsed.netloc:
        return base_url.rstrip("/")

    websocket_scheme = {
        "http": "ws",
        "https": "wss",
    }.get(parsed.scheme, parsed.scheme)
    return urlunparse(
        parsed._replace(
            scheme=websocket_scheme,
            path=parsed.path.rstrip("/"),
        )
    )


def _elevenlabs_realtime_stt_host(base_url: str) -> str:
    """Return the host/path prefix Pipecat's ElevenLabs realtime STT expects.

    Pipecat's realtime STT service builds
    ``wss://{host}/v1/speech-to-text/realtime`` internally, so remove the scheme
    from the same normalized WebSocket URL used by ElevenLabs TTS. Preserve
    netloc (including optional ports) and any path prefix used by BYOK proxies.
    """
    websocket_url = _elevenlabs_websocket_url(base_url)
    parsed = urlparse(websocket_url)
    if parsed.netloc:
        path = parsed.path
        return f"{parsed.netloc}{path}" if path else parsed.netloc
    return websocket_url


def stt_uses_external_turns(user_config) -> bool:
    if user_config.stt.provider == ServiceProviders.DEEPGRAM.value:
        return user_config.stt.model in DEEPGRAM_FLUX_MODELS
    if user_config.stt.provider == ServiceProviders.DOGRAH.value:
        return dograh_stt_uses_flux_language(getattr(user_config.stt, "language", None))
    if user_config.stt.provider == ServiceProviders.CARTESIA.value:
        return user_config.stt.model == "ink-2"
    return False


class AzureLLMService(OpenAISpokenToolCallMixin, BaseAzureLLMService):
    """Azure streaming service with spoken-tool recovery."""


class DograhLLMService(OpenAISpokenToolCallMixin, BaseDograhLLMService):
    """Dograh streaming service with spoken-tool recovery."""


class GroqLLMService(OpenAISpokenToolCallMixin, BaseGroqLLMService):
    """Groq streaming service with spoken-tool recovery."""


class HuggingFaceLLMService(OpenAISpokenToolCallMixin, BaseHuggingFaceLLMService):
    """HuggingFace streaming service with spoken-tool recovery."""


class MiniMaxLLMService(OpenAISpokenToolCallMixin, BaseMiniMaxLLMService):
    """MiniMax streaming service with spoken-tool recovery."""


class OpenAILLMService(OpenAISpokenToolCallMixin, BaseOpenAILLMService):
    """OpenAI streaming service with spoken-tool recovery."""


class OpenRouterLLMService(OpenAISpokenToolCallMixin, BaseOpenRouterLLMService):
    """OpenRouter streaming service with spoken-tool recovery."""


class SarvamLLMService(OpenAISpokenToolCallMixin, BaseSarvamLLMService):
    """Sarvam streaming service with spoken-tool recovery."""


class SpeachesLLMService(OpenAISpokenToolCallMixin, BaseSpeachesLLMService):
    """Speaches streaming service with spoken-tool recovery."""


class DograhGoogleLLMService(GeminiSpokenToolCallMixin, GoogleLLMService):
    adapter_class = DograhGeminiJSONSchemaAdapter


class DograhGoogleVertexLLMService(GeminiSpokenToolCallMixin, GoogleVertexLLMService):
    """Vertex text service with Dograh schemas and nonblocking authentication."""

    adapter_class = DograhGeminiJSONSchemaAdapter

    # The upstream constructor dispatches through this hook. Return a lazy
    # credential object so it never discovers credentials or refreshes tokens.
    _get_credentials = staticmethod(get_google_credentials)

    async def setup(self, setup: FrameProcessorSetup) -> None:
        await super().setup(setup)
        await prepare_google_credentials(self._credentials)

    async def run_inference(
        self,
        context: LLMContext,
        max_tokens: int | None = None,
        system_instruction: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> str | None:
        """Prepare authentication for inference outside a running pipeline."""
        await prepare_google_credentials(self._credentials)
        return await super().run_inference(
            context, max_tokens, system_instruction, response_schema
        )

    async def _stream_content(self, context: LLMContext):
        await prepare_google_credentials(self._credentials)
        return await super()._stream_content(context)


def _validate_runtime_service_url(url: str, field_name: str) -> None:
    try:
        validate_user_configured_service_url(
            url,
            field_name=field_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _deepgram_base_url(service_config) -> str:
    """Resolve the Deepgram endpoint for an STT or TTS config section.

    Deepgram's regional hosts are the only thing that decides which
    jurisdiction processes the audio, so this is the single place the value is
    normalised and checked. Everything downstream builds on the result.
    """
    base_url = (getattr(service_config, "base_url", None) or "").strip()
    if not base_url:
        return DEEPGRAM_DEFAULT_BASE_URL
    # Deepgram documents the regional switch as "replace api.deepgram.com with
    # api.eu.deepgram.com", so operators reasonably type a bare host. The URL
    # validator - and the SaaS SSRF checks behind it - need a scheme, so assume
    # TLS rather than reject a value that is obviously well intentioned.
    if "://" not in base_url:
        base_url = f"https://{base_url}"
    _validate_runtime_service_url(base_url, "base_url")
    return base_url.rstrip("/")


def _deepgram_websocket_url(base_url: str, path: str = "") -> str:
    """Rewrite a Deepgram base URL as the WebSocket URL a service expects.

    Each Deepgram service in pipecat wants a different shape of the same host:
    ``DeepgramSTTService`` takes the base URL and derives both schemes itself,
    the Flux services want a full ``wss://host/v2/listen`` or
    ``wss://host/v2/speak``, and ``DeepgramTTSService`` wants ``wss://host``
    and appends its own path. Deriving them all from one configured value keeps
    a single source of truth
    for where audio goes. Expects the output of :func:`_deepgram_base_url`,
    which guarantees a scheme.
    """
    parsed = urlparse(base_url)
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    return urlunparse(
        parsed._replace(scheme=scheme, path=parsed.path.rstrip("/") + path)
    )


def _google_vertex_location(location: str | None, service: str) -> str:
    """Resolve the Vertex location a service will connect to.

    Vertex derives its endpoint from the location, and only the regional and
    multi-region endpoints keep processing inside a geography - global routes
    anywhere. Falling back to anything other than the configured default would
    mean an operator who left the field alone gets a region nobody chose and
    cannot see, which is the wrong answer for anyone with residency
    obligations.
    """
    resolved = (location or "").strip()
    if resolved:
        # The resolved value is reported by the caller, on the same line as the
        # service being created - one decision, one line.
        return resolved

    # Unset is the one case where nobody picked the endpoint, and the default
    # carries no residency commitment, so it gets a line of its own rather than
    # riding along with the successful cases.
    logger.warning(
        f"Google Vertex {service}: no location configured, falling back to "
        f"{GOOGLE_VERTEX_DEFAULT_LOCATION!r}, which carries no data residency "
        f"guarantee"
    )
    return GOOGLE_VERTEX_DEFAULT_LOCATION


def _stt_configurable_settings(config, *, for_update: bool = False) -> dict[str, Any]:
    """Translate workflow settings for both call startup and live updates.

    Connection identity, model and call-owned keyterms/turn detection are not
    part of this delta. Empty values must be explicit during an update so a
    destination can clear the preceding agent's language hints.
    """
    provider = config.provider
    language = getattr(config, "language", None)
    if provider in {ServiceProviders.DEEPGRAM, ServiceProviders.DOGRAH}:
        is_flux = (
            config.model in DEEPGRAM_FLUX_MODELS
            if provider == ServiceProviders.DEEPGRAM
            else dograh_stt_uses_flux_language(language)
        )
        if is_flux:
            if (
                provider == ServiceProviders.DEEPGRAM
                and config.model == "flux-general-en"
            ):
                return {}
            hints = _resolve_deepgram_flux_language_hints(
                language, getattr(config, "language_hints", None)
            )
            return {"language_hints": hints} if hints or for_update else {}
        return {"language": language or "multi"}
    if provider == ServiceProviders.GOOGLE:
        try:
            languages = [Language(language or "en-US")]
        except ValueError:
            # Clear the preferred enum list when switching to a raw BCP-47
            # code, and clear the raw override when switching back.
            return {"languages": [], "language_codes": [language]}
        return {
            "languages": languages,
            **({"language_codes": None} if for_update else {}),
        }
    if provider == ServiceProviders.CARTESIA:
        return {} if config.model == "ink-2" else {"language": language or "en"}
    if provider == ServiceProviders.SARVAM:
        if not language or language == "unknown":
            return {"language": None}
        if language == "od-IN":
            return {"language": Language.OR_IN}
        try:
            return {"language": Language(language)}
        except ValueError:
            return {"language": language}
    if provider == ServiceProviders.SPEACHES:
        return {"language": language}
    if provider == ServiceProviders.HUGGINGFACE:
        return {"return_timestamps": getattr(config, "return_timestamps", False)}
    if provider == ServiceProviders.ASSEMBLYAI:
        if is_u3_pro_model(config.model):
            return {"language_codes": [Language(language)] if language else []}
        return {}  # Other AssemblyAI models do not support language steering.
    if provider == ServiceProviders.GLADIA:
        from pipecat.services.gladia.config import LanguageConfig

        return {
            "language_config": LanguageConfig(
                languages=[language or "en"], code_switching=False
            )
        }
    if provider == ServiceProviders.SONIOX:
        hints = [Language(language)] if language and language != "multi" else []
        return {"language_hints": hints} if hints or for_update else {}
    if provider == ServiceProviders.SPEECHMATICS:
        return {"language": language or "en"}
    if provider == ServiceProviders.AZURE_SPEECH:
        language = language or "en-US"
        try:
            return {"language": Language(language)}
        except ValueError:
            return {"language": language}
    if provider == ServiceProviders.SMALLEST:
        try:
            return {"language": Language(language or "en")}
        except ValueError:
            return {"language": Language.EN}
    if provider == ServiceProviders.ELEVENLABS:
        return {"language": _resolve_elevenlabs_stt_language(language)}
    return {}  # OpenAI currently exposes no per-agent settings besides model.


def get_stt_settings_update(
    stt: STTService,
    call_config: BaseSTTConfiguration,
    destination_config: BaseSTTConfiguration,
) -> STTSettings | None:
    """Build a settings delta only for a compatible, live-updatable service.

    Having an inherited _update_settings method is not proof of support:
    Gladia and legacy Dograh only change local state, while ink-2 cannot
    switch languages. Supported services own applying/reconnecting their
    streams; the handoff must never call socket lifecycle methods itself.
    """
    connection_fields = (
        "provider",
        "model",
        "base_url",
        "credentials",
        "location",
        "region",
        "bill_to",
    )
    mismatches = [
        field
        for field in connection_fields
        if getattr(call_config, field, None) != getattr(destination_config, field, None)
    ]
    # Reading config.api_key picks a random entry from a pool. Compare the
    # configured pools without selecting a new key for the live connection.
    if set(call_config.get_all_api_keys()) != set(
        destination_config.get_all_api_keys()
    ):
        mismatches.append("api_key")
    if mismatches:
        logger.info(
            f"[transfer] Keeping call STT settings; destination changes {mismatches}"
        )
        return None
    provider = destination_config.provider
    if provider == ServiceProviders.DOGRAH and (
        not dograh_stt_uses_flux_language(getattr(call_config, "language", None))
        or not dograh_stt_uses_flux_language(
            getattr(destination_config, "language", None)
        )
    ):
        logger.info(
            "[transfer] STT settings update unavailable for legacy Dograh STT or a change of backend"
        )
        return None
    if provider == ServiceProviders.GLADIA:
        logger.info("[transfer] Gladia does not apply settings to an active session")
        return None
    settings = _stt_configurable_settings(destination_config, for_update=True)
    return type(stt._settings)(**settings) if settings else None


@_report_service_factory_failures(ErrorSource.STT, config_section="stt")
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
    # Resolved before the line below so that where the audio is processed is
    # recorded alongside what processes it. The endpoint decides which
    # jurisdiction transcribes the call, and that is the only record an operator
    # has that the configured region was the one actually used - but it is one
    # fact about one service, so it belongs on one line.
    is_deepgram = user_config.stt.provider == ServiceProviders.DEEPGRAM.value
    deepgram_base_url = _deepgram_base_url(user_config.stt) if is_deepgram else None
    configurable_settings = _stt_configurable_settings(user_config.stt)

    logger.info(
        f"Creating STT service: provider={user_config.stt.provider}, "
        f"model={user_config.stt.model}"
        + (f", endpoint={deepgram_base_url}" if deepgram_base_url else "")
    )

    if is_deepgram:
        if user_config.stt.model in DEEPGRAM_FLUX_MODELS:
            settings_kwargs = {
                "model": user_config.stt.model,
                "eot_timeout_ms": 3000,
                "eot_threshold": 0.7,
                "eager_eot_threshold": 0.5,
                "keyterm": keyterms or [],
                **configurable_settings,
            }

            return DeepgramFluxSTTService(
                api_key=user_config.stt.api_key,
                # Flux takes a fully-qualified socket URL, not a host.
                url=_deepgram_websocket_url(deepgram_base_url, "/v2/listen"),
                settings=DeepgramFluxSTTSettings(**settings_kwargs),
                should_interrupt=False,  # Let UserAggregator take care of sending InterruptionFrame
                sample_rate=audio_config.transport_in_sample_rate,
            )

        # Other models than flux
        # Use language from user config, defaulting to "multi" for multilingual support
        return DeepgramSTTService(
            api_key=user_config.stt.api_key,
            # Takes the host and derives the wss and https URLs itself.
            base_url=deepgram_base_url,
            settings=DeepgramSTTSettings(
                **configurable_settings,
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
            should_interrupt=False,  # Let UserAggregator own interruption confirmation.
            **kwargs,
        )
    elif user_config.stt.provider == ServiceProviders.GOOGLE.value:
        location = getattr(user_config.stt, "location", None) or "global"
        credentials = getattr(user_config.stt, "credentials", None)

        settings_kwargs = {"model": user_config.stt.model, **configurable_settings}

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

        return CartesiaSTTService(
            api_key=user_config.stt.api_key,
            settings=CartesiaSTTSettings(
                model=user_config.stt.model,
                **configurable_settings,
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
                **configurable_settings,
            }
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
                **configurable_settings,
            ),
            keyterms=keyterms,
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SARVAM.value:
        return SarvamSTTService(
            api_key=user_config.stt.api_key,
            settings=SarvamSTTSettings(
                model=user_config.stt.model,
                **configurable_settings,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SPEACHES.value:
        _validate_runtime_service_url(user_config.stt.base_url, "base_url")
        return SpeachesSTTService(
            base_url=user_config.stt.base_url,
            api_key=user_config.stt.api_key or "none",
            settings=SpeachesSTTSettings(
                model=user_config.stt.model,
                **configurable_settings,
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
                **configurable_settings,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.ASSEMBLYAI.value:
        settings_kwargs = {"model": user_config.stt.model, **configurable_settings}
        if keyterms:
            settings_kwargs["keyterms_prompt"] = keyterms
        return AssemblyAISTTService(
            api_key=user_config.stt.api_key,
            settings=AssemblyAISTTSettings(**settings_kwargs),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.GLADIA.value:
        settings_kwargs = {"model": user_config.stt.model, **configurable_settings}
        return GladiaSTTService(
            api_key=user_config.stt.api_key,
            settings=GladiaSTTSettings(**settings_kwargs),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SONIOX.value:
        settings_kwargs = {"model": user_config.stt.model, **configurable_settings}
        if keyterms:
            settings_kwargs["context"] = SonioxContextObject(terms=keyterms)
        return SonioxSTTService(
            api_key=user_config.stt.api_key,
            settings=SonioxSTTSettings(**settings_kwargs),
            # Local VAD ends the turn and triggers Soniox finalize; Soniox's own
            # endpoint detection stays off so turn strategies behave as for Nova.
            vad_force_turn_endpoint=True,
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SPEECHMATICS.value:
        from pipecat.services.speechmatics.stt import (
            AdditionalVocabEntry,
            Model,
            TurnDetectionMode,
        )

        # Saved configurations may still use the legacy operating-point names.
        model = user_config.stt.model
        if model in ("standard", "enhanced"):
            model = Model.LINDEN_1.value
        # Convert keyterms to AdditionalVocabEntry objects for Speechmatics
        additional_vocab = []
        if keyterms:
            additional_vocab = [AdditionalVocabEntry(content=term) for term in keyterms]
        return SpeechmaticsSTTService(
            api_key=user_config.stt.api_key,
            settings=SpeechmaticsSTTSettings(
                **configurable_settings,
                model=model,
                turn_detection_mode=TurnDetectionMode.EXTERNAL,
                additional_vocab=additional_vocab,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.AZURE_SPEECH.value:
        region = getattr(user_config.stt, "region", None) or "eastus"
        return AzureSTTService(
            api_key=user_config.stt.api_key,
            region=region,
            settings=AzureSTTSettings(**configurable_settings),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SMALLEST.value:
        return SmallestSTTService(
            api_key=user_config.stt.api_key,
            settings=SmallestSTTSettings(
                model=user_config.stt.model,
                **configurable_settings,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.ELEVENLABS.value:
        _validate_runtime_service_url(user_config.stt.base_url, "base_url")
        elevenlabs_host = _elevenlabs_realtime_stt_host(user_config.stt.base_url)

        return ElevenLabsRealtimeSTTService(
            api_key=user_config.stt.api_key,
            base_url=elevenlabs_host,
            commit_strategy=CommitStrategy.VAD,
            settings=ElevenLabsRealtimeSTTSettings(
                model=user_config.stt.model,
                **configurable_settings,
            ),
            should_interrupt=False,
            sample_rate=audio_config.transport_in_sample_rate,
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid STT provider {user_config.stt.provider}"
        )


@_report_service_factory_failures(ErrorSource.TTS, config_section="tts")
def create_tts_service(
    user_config,
    audio_config: "AudioConfig",
    correlation_id: str | None = None,
    *,
    organization_id: int | None = None,
    tts_cache_enabled: bool = False,
):
    """Create and return appropriate TTS service based on user configuration

    Args:
        user_config: User configuration containing TTS settings
        audio_config: Pipeline and transport audio configuration.
        correlation_id: Managed model services correlation ID.
        organization_id: Trusted tenant scope for TTS caching.
        tts_cache_enabled: Whether the workflow enables the speech cache. No provider
            uses it at present; MiniMax logs a warning when it is set.
    """
    # Synthesis carries the same residency question as transcription - the text
    # sent for speaking is drawn from the conversation - so the endpoint is
    # resolved up front and reported on the creation line.
    is_deepgram = user_config.tts.provider == ServiceProviders.DEEPGRAM.value
    deepgram_base_url = _deepgram_base_url(user_config.tts) if is_deepgram else None

    logger.info(
        f"Creating TTS service: provider={user_config.tts.provider}, "
        f"model={user_config.tts.model}, "
        f"voice={getattr(user_config.tts, 'voice', None)}"
        + (f", endpoint={deepgram_base_url}" if deepgram_base_url else "")
    )

    # Create function call filter to prevent TTS from speaking function call tags
    xml_function_tag_filter = XMLFunctionTagFilter()
    if is_deepgram:
        voice = user_config.tts.voice
        speed = getattr(user_config.tts, "speed", None)
        if getattr(user_config.tts, "model", None) == "flux":
            return DeepgramFluxTTSService(
                api_key=user_config.tts.api_key,
                # Flux takes a fully-qualified socket URL, not a host.
                url=_deepgram_websocket_url(deepgram_base_url, "/v2/speak"),
                settings=DeepgramFluxTTSSettings(
                    voice=voice,
                    speed=speed,
                    expressivity=getattr(user_config.tts, "expressivity", None),
                ),
                # Flux can take raw LLM tokens, but the function-tag filter only
                # matches a complete tag, so text is aggregated into sentences
                # first like every other TTS service here.
                text_aggregation_mode=TextAggregationMode.SENTENCE,
                text_filters=[xml_function_tag_filter],
                skip_aggregator_types=["recording_router", "recording"],
                silence_time_s=1.0,
            )
        return DeepgramTTSService(
            api_key=user_config.tts.api_key,
            # Wants wss://host with no path; it appends /v1/speak itself.
            base_url=_deepgram_websocket_url(deepgram_base_url),
            settings=DeepgramTTSSettings(voice=voice, speed=speed),
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
        # ElevenLabs TTS consumes the full normalized WebSocket URL. Realtime
        # STT uses the same normalization before adapting it to Pipecat's
        # scheme-less base_url contract.
        _validate_runtime_service_url(user_config.tts.base_url, "base_url")
        elevenlabs_url = _elevenlabs_websocket_url(user_config.tts.base_url)
        if re.match(ELEVENLABS_DIALOGUE_TTS_MODEL_PATTERN, user_config.tts.model):
            tts_service_cls = ElevenLabsDialogueTTSService
            tts_settings = ElevenLabsDialogueTTSSettings(
                voice=voice_id,
                model=user_config.tts.model,
                stability=0.8,
            )
        else:
            tts_service_cls = ElevenLabsTTSService
            tts_settings = ElevenLabsTTSSettings(
                voice=voice_id,
                model=user_config.tts.model,
                stability=0.8,
                speed=user_config.tts.speed,
                similarity_boost=0.75,
            )
        return tts_service_cls(
            reconnect_on_error=False,
            api_key=user_config.tts.api_key,
            url=elevenlabs_url,
            settings=tts_settings,
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

        model = getattr(user_config.tts, "model", None) or "bulbul:v3"
        default_voice = "shubh"
        if model in ("bulbul:v3", "bulbul:v3-beta"):
            default_voice = "shubh"
        elif model == "bulbul:v4-flash":
            default_voice = "shubh_enhi_ads"

        voice = (
            getattr(user_config.tts, "voice", None) or ""
        ).strip().lower() or default_voice
        speed = getattr(user_config.tts, "speed", None)
        settings_kwargs = {
            "model": model,
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

        if tts_cache_enabled:
            logger.warning(
                "MiniMax TTS runs over the WebSocket API and does not use the speech "
                "cache; every reply is synthesized"
            )
        # One bidirectional WebSocket session serves the whole call: synthesis stops
        # when the caller interrupts, and word timestamps keep the spoken part of an
        # interrupted reply in the transcript. It is served from the same host as
        # /v1/t2a_v2.
        return MiniMaxTTSService(
            api_key=user_config.tts.api_key,
            url=f"wss://{urlparse(base_url).netloc}/ws/v1/t2a_v2_bidi",
            settings=MiniMaxTTSSettings(
                model=user_config.tts.model,
                voice=voice,
                speed=speed,
                volume=getattr(user_config.tts, "volume", 1.0),
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
    elif user_config.tts.provider == ServiceProviders.XAI.value:
        voice = getattr(user_config.tts, "voice", None) or "eve"
        language_code = getattr(user_config.tts, "language", None) or "en"
        if language_code.lower() == "auto":
            pipecat_language = "auto"
        else:
            try:
                pipecat_language = Language(language_code)
            except ValueError:
                pipecat_language = Language.EN
        return XAITTSService(
            api_key=user_config.tts.api_key,
            settings=XAIWebsocketTTSSettings(
                voice=voice,
                language=pipecat_language,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.LMNT.value:
        raise ValueError(
            "LMNT is no longer available. Please select another TTS provider."
        )
    elif user_config.tts.provider == ServiceProviders.SPEECHIFY.value:
        # SpeechifyHttpTTSService ships in upstream pipecat; imported lazily so
        # this module keeps loading on pipecat checkouts that predate it.
        try:
            from api.services.pipecat.speechify_tts import (
                SpeechifyOwnedSessionTTSService,
            )
            from pipecat.services.speechify.tts import SpeechifyTTSSettings
        except ModuleNotFoundError as e:
            missing = e.name or ""
            if missing != "pipecat.services.speechify" and not missing.startswith(
                "pipecat.services.speechify."
            ):
                raise
            raise HTTPException(
                status_code=400,
                detail=(
                    "Speechify TTS requires a pipecat build that includes "
                    "pipecat.services.speechify; the installed pipecat does not."
                ),
            ) from e

        voice = getattr(user_config.tts, "voice", None) or "beatrice_32"
        model = getattr(user_config.tts, "model", None) or "simba-3.2"
        language_code = getattr(user_config.tts, "language", None) or "en"
        language: Language | str
        try:
            language = Language(language_code)
        except ValueError:
            # The config allows custom language codes; codes the pipecat enum
            # doesn't model (e.g. "en-ZA") are sent to Speechify verbatim
            # rather than silently replaced with English.
            language = language_code
        session = aiohttp.ClientSession()
        return SpeechifyOwnedSessionTTSService(
            api_key=user_config.tts.api_key,
            aiohttp_session=session,
            sample_rate=audio_config.transport_out_sample_rate,
            settings=SpeechifyTTSSettings(
                voice=voice,
                model=model,
                language=language,
            ),
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


@_report_service_factory_failures(ErrorSource.LLM, provider_argument=0)
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
    enable_direct_mode: bool = False,
    temperature: float | None | NotGiven = NOT_GIVEN,
    bill_to: str | None = None,
    provider_order: list[str] | None = None,
    usage_context: str | None = None,
):
    """Create an LLM service from explicit provider/model/api_key.

    Also used by create_llm_service which extracts these from user_config.

    Args:
        usage_context: Optional tag describing what the LLM instance is used for
            (e.g. "voicemail_detection"). Sent as request metadata by the Dograh
            provider; ignored by other providers.
    """
    # Vertex builds its endpoint from the location, so it is part of what this
    # service is, not a separate event. Resolved here to keep it on one line.
    vertex_location = (
        _google_vertex_location(location, "LLM")
        if provider == ServiceProviders.GOOGLE_VERTEX.value
        else None
    )

    logger.info(
        f"Creating LLM service: provider={provider}, model={model}"
        + (f", location={vertex_location}" if vertex_location else "")
    )

    config_cls = REGISTRY[ServiceType.LLM].get(provider)
    if config_cls is None:
        raise HTTPException(status_code=400, detail=f"Invalid LLM provider {provider}")
    if isinstance(temperature, NotGiven):
        temperature = config_cls.model_fields["temperature"].default
    try:
        temperature = resolve_temperature(
            provider, model, temperature, base_url=base_url
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # None means omit the parameter, not send JSON null. This matters for
    # reasoning models and for providers with their own sampling defaults.
    sampling_settings = {} if temperature is None else {"temperature": temperature}

    if provider in (
        ServiceProviders.OPENAI.value,
        ServiceProviders.ATLASCLOUD.value,
    ):
        # Voicemail and QA configs with their own provider pass no base_url;
        # without this default the OpenAI client sends the Atlas Cloud key to OpenAI.
        if provider == ServiceProviders.ATLASCLOUD.value and not base_url:
            base_url = ATLASCLOUD_API_BASE_URL
        kwargs = {}
        if base_url:
            _validate_runtime_service_url(base_url, "base_url")
            kwargs["base_url"] = base_url
        if "gpt-5" in model:
            return OpenAILLMService(
                enable_direct_mode=enable_direct_mode,
                api_key=api_key,
                settings=OpenAILLMSettings(
                    model=model,
                    extra={"reasoning_effort": "minimal", "verbosity": "low"},
                ),
                **kwargs,
            )
        return OpenAILLMService(
            enable_direct_mode=enable_direct_mode,
            api_key=api_key,
            settings=OpenAILLMSettings(model=model, **sampling_settings),
            **kwargs,
        )
    elif provider == ServiceProviders.GROQ.value:
        return GroqLLMService(
            enable_direct_mode=enable_direct_mode,
            api_key=api_key,
            settings=GroqLLMSettings(model=model, **sampling_settings),
        )
    elif provider == ServiceProviders.HOPPER.value:
        return OpenAILLMService(
            enable_direct_mode=enable_direct_mode,
            api_key=api_key,
            base_url=HOPPER_API_BASE_URL,
            settings=OpenAILLMSettings(model=model, **sampling_settings),
        )
    elif provider == ServiceProviders.OPENROUTER.value:
        kwargs = {}
        if base_url:
            _validate_runtime_service_url(base_url, "base_url")
            kwargs["base_url"] = base_url
        extra = {}
        if provider_order:
            # OpenRouter's provider preferences are a request-body field the
            # OpenAI client does not know, so they travel in extra_body.
            extra["extra_body"] = {"provider": {"order": provider_order}}
        return OpenRouterLLMService(
            enable_direct_mode=enable_direct_mode,
            api_key=api_key,
            settings=OpenRouterLLMSettings(
                model=model, extra=extra, **sampling_settings
            ),
            **kwargs,
        )
    elif provider in (
        ServiceProviders.GOOGLE.value,
        ServiceProviders.GOOGLE_VERTEX.value,
    ):
        is_vertex = provider == ServiceProviders.GOOGLE_VERTEX.value
        if not is_vertex:
            model = _migrate_deprecated_google_model(model)
        service_class = (
            DograhGoogleVertexLLMService if is_vertex else DograhGoogleLLMService
        )
        settings_class = GoogleVertexLLMSettings if is_vertex else GoogleLLMSettings

        connection: dict[str, Any] = (
            {
                "credentials": credentials,
                "project_id": project_id,
                "location": vertex_location,
            }
            if is_vertex
            else {"api_key": api_key}
        )
        # The provider selects a matching service and settings class together.
        service_options: dict[str, Any] = {
            **connection,
            "enable_direct_mode": enable_direct_mode,
            "settings": settings_class(
                model=model,
                temperature=temperature if temperature is not None else NOT_GIVEN,
                # Pipecat owns tools; the SDK must return calls without executing them.
                extra={"automatic_function_calling": {"disable": True}},
            ),
        }
        service = service_class(**service_options)
        # Gemini's opaque signatures may not transfer between platforms,
        # projects or models. Scope provider-specific context to this target;
        # standard text and tool history remains available to every adapter.
        adapter = cast(DograhGeminiJSONSchemaAdapter, service.get_llm_adapter())
        adapter.signature_scope = hashlib.sha256(
            json.dumps([provider, connection], sort_keys=True).encode()
        ).hexdigest()
        adapter.signature_settings = service._settings
        return service
    elif provider == ServiceProviders.AZURE.value:
        if endpoint:
            _validate_runtime_service_url(endpoint, "endpoint")
        return AzureLLMService(
            enable_direct_mode=enable_direct_mode,
            api_key=api_key,
            endpoint=endpoint,
            settings=AzureLLMSettings(model=model, **sampling_settings),
        )
    elif provider == ServiceProviders.DOGRAH.value:
        return DograhLLMService(
            base_url=f"{MPS_API_URL}/api/v1/llm",
            enable_direct_mode=enable_direct_mode,
            api_key=api_key,
            correlation_id=correlation_id,
            usage_context=usage_context,
            settings=OpenAILLMSettings(model=model, **sampling_settings),
        )
    elif provider == ServiceProviders.AWS_BEDROCK.value:
        return AWSBedrockLLMService(
            enable_direct_mode=enable_direct_mode,
            aws_access_key=aws_access_key,
            aws_secret_key=aws_secret_key,
            aws_region=aws_region,
            settings=AWSBedrockLLMSettings(model=model, **sampling_settings),
        )
    elif provider == ServiceProviders.SPEACHES.value:
        base_url = base_url or "http://localhost:11434/v1"
        _validate_runtime_service_url(base_url, "base_url")
        return SpeachesLLMService(
            base_url=base_url,
            enable_direct_mode=enable_direct_mode,
            api_key=api_key or "none",
            settings=SpeachesLLMSettings(model=model, **sampling_settings),
        )
    elif provider == ServiceProviders.HUGGINGFACE.value:
        base_url = base_url or "https://router.huggingface.co/v1"
        _validate_runtime_service_url(base_url, "base_url")
        return HuggingFaceLLMService(
            enable_direct_mode=enable_direct_mode,
            api_key=api_key,
            base_url=base_url,
            bill_to=bill_to,
            settings=HuggingFaceLLMSettings(model=model, **sampling_settings),
        )
    elif provider == ServiceProviders.MINIMAX.value:
        base_url = base_url or "https://api.minimax.io/v1"
        _validate_runtime_service_url(base_url, "base_url")
        return MiniMaxLLMService(
            enable_direct_mode=enable_direct_mode,
            api_key=api_key,
            base_url=base_url,
            settings=MiniMaxLLMService.Settings(
                model=model,
                **sampling_settings,
            ),
        )
    elif provider == ServiceProviders.SARVAM.value:
        base_url = base_url or "https://api.sarvam.ai/v1"
        _validate_runtime_service_url(base_url, "base_url")
        return SarvamLLMService(
            enable_direct_mode=enable_direct_mode,
            api_key=api_key,
            base_url=base_url,
            settings=SarvamLLMSettings(
                model=model,
                **sampling_settings,
            ),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Invalid LLM provider {provider}")


@_report_service_factory_failures(ErrorSource.LLM, config_section="realtime")
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

    vertex_location = (
        _google_vertex_location(getattr(realtime_config, "location", None), "Realtime")
        if provider == ServiceProviders.GOOGLE_VERTEX_REALTIME.value
        else None
    )

    logger.info(
        f"Creating realtime LLM service: provider={provider}, model={model}, "
        f"voice={voice}, language={language}"
        + (f", location={vertex_location}" if vertex_location else "")
    )

    if provider == ServiceProviders.OPENAI_REALTIME.value and model == "gpt-live-1":
        from api.services.pipecat.realtime.openai_live import DograhOpenAILiveLLMService

        return DograhOpenAILiveLLMService(
            api_key=api_key,
            backend_model=realtime_config.backend_model,
            settings=DograhOpenAILiveLLMService.Settings(
                model=model,
                voice=voice or "marin",
            ),
        )
    elif provider == ServiceProviders.OPENAI_REALTIME.value:
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

        # Pin the transcription language when configured. Without it the model
        # auto-detects per utterance, which misfires on short/noisy telephony
        # audio (e.g. Portuguese transcribed as English or Chinese).
        transcription_kwargs = {}
        if language:
            transcription_kwargs["language"] = language

        return DograhOpenAIRealtimeLLMService(
            api_key=api_key,
            settings=DograhOpenAIRealtimeLLMService.Settings(
                model=model,
                session_properties=SessionProperties(
                    audio=AudioConfiguration(
                        input=AudioInput(
                            transcription=InputAudioTranscription(
                                **transcription_kwargs
                            ),
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
        from pipecat.services.xai.realtime.events import (
            AudioConfiguration,
            AudioInput,
            InputAudioTranscription,
            SessionProperties,
        )

        grok_voice = voice or "ara"
        if grok_voice.lower() in {"ara", "rex", "sal", "eve", "leo"}:
            grok_voice = grok_voice.lower()

        return DograhGrokRealtimeLLMService(
            api_key=api_key,
            settings=DograhGrokRealtimeLLMService.Settings(
                model=model,
                session_properties=SessionProperties(
                    voice=grok_voice,
                    audio=AudioConfiguration(
                        input=AudioInput(
                            transcription=InputAudioTranscription(),
                        ),
                    ),
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
                temperature=realtime_config.temperature,
            ),
            settings=DograhUltravoxRealtimeLLMService.Settings(
                model=model,
                output_medium="voice",
            ),
        )
    elif provider == ServiceProviders.AWS_NOVA_SONIC.value:
        from api.services.pipecat.realtime.aws_nova_sonic import (
            DograhAWSNovaSonicLLMService,
        )
        from pipecat.services.aws.nova_sonic.llm import AudioConfig as NovaAudioConfig

        return DograhAWSNovaSonicLLMService(
            secret_access_key=realtime_config.aws_secret_key,
            access_key_id=realtime_config.aws_access_key,
            session_token=realtime_config.aws_session_token or None,
            region=realtime_config.aws_region,
            audio_config=NovaAudioConfig(
                input_sample_rate=audio_config.transport_in_sample_rate,
                output_sample_rate=audio_config.transport_out_sample_rate,
            ),
            settings=DograhAWSNovaSonicLLMService.Settings(
                model=model,
                voice=voice or "matthew",
                endpointing_sensitivity=realtime_config.endpointing_sensitivity,
                temperature=realtime_config.temperature,
                max_tokens=realtime_config.max_tokens,
                top_p=realtime_config.top_p,
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
            location=vertex_location,
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
        api_version = getattr(realtime_config, "api_version", None) or "v1"
        parsed_endpoint = urlparse(endpoint)
        if api_version == "v1":
            # Azure's GA Realtime API uses the deployment name as `model` and
            # deliberately has no date-based api-version query parameter.
            path = "/openai/v1/realtime"
            query = urlencode({"model": model})
        else:
            # Preserve explicitly configured preview deployments while users
            # migrate. Microsoft deprecated this protocol on April 30, 2026.
            path = "/openai/realtime"
            query = urlencode({"api-version": api_version, "deployment": model})
        wss_url = urlunparse(
            (
                "wss",
                parsed_endpoint.netloc,
                path,
                "",
                query,
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


def get_llm_runtime_configuration(user_config) -> dict:
    """Describe the resolved policy without credentials or credential pins."""
    llm = user_config.llm
    configuration = {"llm_provider": llm.provider, "llm_model": llm.model}
    if llm.provider == ServiceProviders.GOOGLE_VERTEX.value:
        configuration["llm_location"] = llm.location
    policy = getattr(user_config, "llm_fallback", None)
    configuration["llm_fallback"] = {
        "version": 1,
        "rules": (
            [
                {
                    "condition": rule.condition.model_dump(mode="json"),
                    "target": {
                        "provider": rule.target.provider,
                        "model": rule.target.model,
                        **(
                            {"location": rule.target.location}
                            if hasattr(rule.target, "location")
                            else {}
                        ),
                    },
                }
                for rule in policy.rules
            ]
            if policy
            else []
        ),
    }
    return configuration


def create_llm_service(
    user_config,
    correlation_id: str | None = None,
    usage_context: str | None = None,
    *,
    spoken_output: bool = True,
):
    """Compose independently configured text LLM connections under one policy.

    ``spoken_output=False`` marks replies that are read rather than synthesized,
    so markup and JSON in them are kept.
    """
    policy = getattr(user_config, "llm_fallback", None)
    if policy is None:
        return _create_single_llm_service(
            user_config, correlation_id, usage_context, spoken_output=spoken_output
        )
    primary = _create_single_llm_service(
        user_config,
        correlation_id,
        usage_context,
        enable_direct_mode=bool(policy.rules),
        spoken_output=spoken_output,
    )
    if not policy.rules:
        return primary
    services = {}
    routes = []
    for rule in policy.rules:
        # Reuse the request when both triggers select the same connection/model.
        # Resolved credentials stay in memory; never log this identity.
        identity = rule.target.model_dump_json(exclude_computed_fields=True)
        if identity not in services:
            target_config = user_config.model_copy(update={"llm": rule.target})
            services[identity] = _create_single_llm_service(
                target_config,
                correlation_id,
                usage_context,
                enable_direct_mode=True,
                spoken_output=spoken_output,
            )
        routes.append(
            FallbackRoute(condition=rule.condition, service=services[identity])
        )
    return FallbackLLMProcessor(primary, routes=routes)


def _create_single_llm_service(
    user_config,
    correlation_id: str | None = None,
    usage_context: str | None = None,
    *,
    enable_direct_mode: bool = False,
    spoken_output: bool = True,
):
    """Create and return appropriate LLM service based on user configuration."""
    provider = user_config.llm.provider
    model = user_config.llm.model
    api_key = user_config.llm.api_key

    kwargs = {}
    if provider in (
        ServiceProviders.OPENAI.value,
        ServiceProviders.ATLASCLOUD.value,
    ):
        kwargs["base_url"] = user_config.llm.base_url
    elif provider == ServiceProviders.OPENROUTER.value:
        kwargs["base_url"] = user_config.llm.base_url
        kwargs["provider_order"] = getattr(user_config.llm, "provider_order", None)
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
    elif provider == ServiceProviders.SARVAM.value:
        kwargs["base_url"] = getattr(user_config.llm, "base_url", None)

    service = create_llm_service_from_provider(
        provider,
        model,
        api_key,
        correlation_id=correlation_id,
        usage_context=usage_context,
        temperature=getattr(user_config.llm, "temperature", NOT_GIVEN),
        enable_direct_mode=enable_direct_mode,
        **kwargs,
    )
    if isinstance(service, SpokenToolCallMixin):
        service.spoken_output = spoken_output
    return service


def create_llm_service_with_model_override(
    user_config: "EffectiveAIModelConfiguration",
    model_override: str | None,
    correlation_id: str | None = None,
    usage_context: str | None = None,
):
    """Create an LLM service with an optional model override.

    The copied configuration is delegated to ``create_llm_service`` so provider-
    specific settings continue to be extracted in one place.
    """
    if model_override is None:
        return create_llm_service(
            user_config,
            correlation_id=correlation_id,
            usage_context=usage_context,
        )

    if user_config.llm is None:
        raise ValueError("Cannot override the model without an LLM configuration")

    llm_config = user_config.llm.model_copy(update={"model": model_override})
    overridden_config = user_config.model_copy(update={"llm": llm_config})
    return create_llm_service(
        overridden_config,
        correlation_id=correlation_id,
        usage_context=usage_context,
    )
