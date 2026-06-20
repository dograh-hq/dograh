from typing import Optional, TypedDict

import httpx
import openai
from deepgram import DeepgramClient
from groq import Groq

# try:
#     from pyneuphonic import Neuphonic
# except ImportError:
#     Neuphonic = None
from api.schemas.ai_model_configuration import (
    EffectiveAIModelConfiguration,
)
from api.services.configuration.registry import ServiceConfig, ServiceProviders
from api.services.mps_service_key_client import mps_service_key_client
from api.utils.url_security import validate_user_configured_service_url

AuthContext = TypedDict(
    "AuthContext",
    {"organization_id": Optional[int], "created_by": Optional[str]},
    total=False,
)


class APIKeyStatus(TypedDict):
    model: str
    message: str


class APIKeyStatusResponse(TypedDict):
    status: list[APIKeyStatus]


class UserConfigurationValidator:
    def __init__(self):
        self._validator_map = {
            ServiceProviders.OPENAI.value: self._check_openai_api_key,
            ServiceProviders.DEEPGRAM.value: self._check_deepgram_api_key,
            ServiceProviders.GROQ.value: self._check_groq_api_key,
            ServiceProviders.OPENROUTER.value: self._check_openrouter_api_key,
            ServiceProviders.INWORLD.value: self._check_inworld_api_key,
            ServiceProviders.ELEVENLABS.value: self._validate_elevenlabs_api_key,
            ServiceProviders.GOOGLE.value: self._check_google_api_key,
            ServiceProviders.AZURE.value: self._check_azure_api_key,
            ServiceProviders.AZURE_SPEECH.value: self._check_azure_speech_api_key,
            ServiceProviders.CARTESIA.value: self._check_cartesia_api_key,
            ServiceProviders.DOGRAH.value: self._check_dograh_api_key,
            ServiceProviders.SARVAM.value: self._check_sarvam_api_key,
            ServiceProviders.SPEECHMATICS.value: self._check_speechmatics_api_key,
            ServiceProviders.CAMB.value: self._check_camb_api_key,
            ServiceProviders.AWS_BEDROCK.value: self._check_aws_bedrock_api_key,
            ServiceProviders.SPEACHES.value: self._check_speaches_api_key,
            ServiceProviders.HUGGINGFACE.value: self._check_huggingface_api_key,
            ServiceProviders.GOOGLE_VERTEX.value: self._check_google_vertex_llm_api_key,
            ServiceProviders.OPENAI_REALTIME.value: self._check_openai_api_key,
            ServiceProviders.GROK_REALTIME.value: self._check_grok_realtime_api_key,
            ServiceProviders.ULTRAVOX_REALTIME.value: self._check_ultravox_realtime_api_key,
            ServiceProviders.GOOGLE_REALTIME.value: self._check_google_api_key,
            ServiceProviders.GOOGLE_VERTEX_REALTIME.value: self._check_google_vertex_realtime_api_key,
            ServiceProviders.AZURE_REALTIME.value: self._check_azure_realtime_api_key,
            ServiceProviders.ASSEMBLYAI.value: self._check_assemblyai_api_key,
            ServiceProviders.GLADIA.value: self._check_gladia_api_key,
            ServiceProviders.RIME.value: self._check_rime_api_key,
            ServiceProviders.MINIMAX.value: self._check_minimax_api_key,
            ServiceProviders.SMALLEST.value: self._check_smallest_api_key,
        }

    async def validate(
        self,
        configuration: EffectiveAIModelConfiguration,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> APIKeyStatusResponse:
        self._auth_context: AuthContext = {
            "organization_id": organization_id,
            "created_by": created_by,
        }
        status_list = []

        status_list.extend(self._validate_service(configuration.llm, "llm"))
        if configuration.is_realtime:
            status_list.extend(
                self._validate_service(
                    configuration.realtime, "realtime", required=True
                )
            )
        else:
            status_list.extend(self._validate_service(configuration.stt, "stt"))
            status_list.extend(self._validate_service(configuration.tts, "tts"))
        # Embeddings is optional - only validate if configured
        status_list.extend(
            self._validate_service(
                configuration.embeddings, "embeddings", required=False
            )
        )

        if status_list:
            raise ValueError(status_list)

        return {"status": [{"model": "all", "message": "ok"}]}

    def _validate_service(
        self,
        service_config: Optional[ServiceConfig],
        service_name: str,
        required: bool = True,
    ) -> list[APIKeyStatus]:
        """Validate a service configuration and return any error statuses."""
        if not service_config:
            if required:
                return [{"model": service_name, "message": "API key is missing"}]
            return []  # Optional service not configured is OK

        provider = service_config.provider

        for url_field in ("base_url", "endpoint"):
            url = getattr(service_config, url_field, None)
            if url:
                try:
                    validate_user_configured_service_url(
                        url,
                        field_name=url_field,
                    )
                except ValueError as e:
                    return [{"model": service_name, "message": str(e)}]

        # Speaches doesn't require an API key
        if provider == ServiceProviders.SPEACHES.value:
            try:
                if not self._check_speaches_api_key(provider, service_config):
                    return [
                        {
                            "model": service_name,
                            "message": f"Invalid {provider} configuration",
                        }
                    ]
            except ValueError as e:
                return [{"model": service_name, "message": str(e)}]
            return []

        # Vertex Realtime uses service-account credentials (or ADC) instead of api_key
        if provider == ServiceProviders.GOOGLE_VERTEX_REALTIME.value:
            try:
                if not self._check_google_vertex_realtime_api_key(
                    provider, service_config
                ):
                    return [
                        {
                            "model": service_name,
                            "message": f"Invalid {provider} configuration",
                        }
                    ]
            except ValueError as e:
                return [{"model": service_name, "message": str(e)}]
            return []

        # Vertex LLM uses service-account credentials (or ADC) instead of api_key
        if provider == ServiceProviders.GOOGLE_VERTEX.value:
            try:
                if not self._check_google_vertex_llm_api_key(provider, service_config):
                    return [
                        {
                            "model": service_name,
                            "message": f"Invalid {provider} configuration",
                        }
                    ]
            except ValueError as e:
                return [{"model": service_name, "message": str(e)}]
            return []

        # AWS Bedrock uses AWS credentials instead of api_key
        if provider == ServiceProviders.AWS_BEDROCK.value:
            try:
                if not self._check_aws_bedrock_api_key(provider, service_config):
                    return [
                        {
                            "model": service_name,
                            "message": f"Invalid {provider} credentials",
                        }
                    ]
            except ValueError as e:
                return [{"model": service_name, "message": str(e)}]
            return []

        # MiniMax TTS requires a group_id alongside the API key.
        # LLM configs don't expose group_id, so only check when the field exists.
        if provider == ServiceProviders.MINIMAX.value and hasattr(
            service_config, "group_id"
        ):
            if not getattr(service_config, "group_id", None):
                return [
                    {
                        "model": service_name,
                        "message": "group_id is required for MiniMax TTS",
                    }
                ]

        api_key = service_config.api_key

        try:
            if not self._check_api_key(provider, api_key, service_config):
                return [
                    {
                        "model": service_name,
                        "message": (
                            f"Invalid {provider} API key. Please verify your API key is "
                            f"correct, has not expired, and has the required permissions."
                        ),
                    }
                ]
        except ValueError as e:
            return [{"model": service_name, "message": str(e)}]

        return []

    def _check_api_key(
        self,
        provider: str,
        api_key: str,
        service_config: Optional[ServiceConfig] = None,
    ) -> bool:
        """Check if an API key for a provider is valid."""
        validator = self._validator_map.get(provider)
        if not validator:
            return False

        if provider in (
            ServiceProviders.OPENAI.value,
            ServiceProviders.OPENAI_REALTIME.value,
        ):
            return validator(provider, api_key, service_config)
        return validator(provider, api_key)

    def _check_openai_api_key(
        self, model: str, api_key: str, service_config: Optional[ServiceConfig] = None
    ) -> bool:
        client_kwargs: dict[str, str] = {"api_key": api_key}
        base_url = getattr(service_config, "base_url", None) if service_config else None
        if base_url:
            client_kwargs["base_url"] = base_url
        client = openai.OpenAI(**client_kwargs)
        try:
            client.models.list()
            return True
        except openai.AuthenticationError:
            if base_url and "openai.com" not in base_url:
                raise ValueError(
                    f"Invalid OpenAI API key. The key was rejected by the API at {base_url}. "
                    "Please check that your API key is correct and has not been revoked."
                )
            raise ValueError(
                "Invalid OpenAI API key. The key was rejected by the OpenAI API. "
                "Please check that your API key is correct and has not been revoked. "
                "You can verify your keys at https://platform.openai.com/api-keys."
            )
        except openai.APIConnectionError:
            if base_url:
                raise ValueError(
                    f"Could not connect to the OpenAI-compatible API at {base_url}. "
                    "Please verify that the base_url is correct and reachable, and try again."
                )
            raise ValueError(
                "Could not connect to the OpenAI API. Please check your network connection "
                "and try again."
            )
        except openai.APIError:
            if base_url:
                raise ValueError(
                    f"The OpenAI-compatible API at {base_url} returned an error while "
                    "validating the API key. Please verify that the base_url is correct, "
                    "the service is available, and the API key is valid."
                )
            raise ValueError(
                "The OpenAI API returned an error while validating the API key. "
                "Please try again later."
            )
        except Exception:
            if base_url:
                raise ValueError(
                    f"Failed to validate the OpenAI API key using the API at {base_url}. "
                    "Please verify that the base_url is correct and reachable, and that the "
                    "API key is valid."
                )
            raise ValueError(
                "Failed to validate the OpenAI API key. Please try again later."
            )

    def _check_deepgram_api_key(self, model: str, api_key: str) -> bool:
        try:
            deepgram = DeepgramClient(api_key=api_key)
            deepgram.manage.v1.projects.list()
            return True
        except Exception:
            raise ValueError(
                "Invalid Deepgram API key. The key was rejected by the Deepgram API. "
                "Please check that your API key is correct and active. "
                "You can verify your keys at https://console.deepgram.com/."
            )

    def _check_groq_api_key(self, model: str, api_key: str) -> bool:
        client = Groq(api_key=api_key)
        try:
            client.models.list()
            return True
        except Exception:
            raise ValueError(
                "Invalid Groq API key. The key was rejected by the Groq API. "
                "Please check that your API key is correct and active. "
                "You can verify your keys at https://console.groq.com/keys."
            )

    def _validate_elevenlabs_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_google_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_azure_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_azure_speech_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_azure_realtime_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_cartesia_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_dograh_api_key(self, model: str, api_key: str) -> bool:
        if api_key.startswith("dgr"):
            raise ValueError(
                "You provided a Dograh API key (dgr...) instead of a service key. "
                "Please use a service key (mps...)."
            )
        auth = getattr(self, "_auth_context", {})
        return mps_service_key_client.validate_service_key(
            api_key,
            organization_id=auth.get("organization_id"),
            created_by=auth.get("created_by"),
        )

    def _check_sarvam_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_openrouter_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_inworld_api_key(self, model: str, api_key: str) -> bool:
        try:
            response = httpx.get(
                "https://api.inworld.ai/voices/v1/voices",
                headers={"Authorization": f"Basic {api_key}"},
                params={"pageSize": 1},
                timeout=10.0,
            )
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise ValueError(
                    "Invalid Inworld API key. The key was rejected by the Inworld API. "
                    "Please verify that your API key is correct, active, and has voice read access."
                ) from exc
            raise ValueError(
                "The Inworld API returned an error while validating the API key. "
                "Please try again later."
            ) from exc
        except httpx.RequestError as exc:
            raise ValueError(
                "Could not connect to the Inworld API. Please check your network connection "
                "and try again."
            ) from exc

    def _check_grok_realtime_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_ultravox_realtime_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_speechmatics_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_camb_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_speaches_api_key(self, model: str, service_config) -> bool:
        if not getattr(service_config, "base_url", None):
            raise ValueError("base_url is required for Speaches services")
        return True

    def _check_huggingface_api_key(self, model: str, api_key: str) -> bool:
        if not api_key.startswith("hf_"):
            raise ValueError(
                "Invalid Hugging Face API token format. Use a token that starts with "
                "'hf_' and has Inference Providers permission."
            )
        return True

    def _check_google_vertex_realtime_api_key(self, model: str, service_config) -> bool:
        if not getattr(service_config, "project_id", None):
            raise ValueError("project_id is required for Google Vertex Realtime")
        if not getattr(service_config, "location", None):
            raise ValueError("location is required for Google Vertex Realtime")
        return True

    def _check_google_vertex_llm_api_key(self, model: str, service_config) -> bool:
        if not getattr(service_config, "project_id", None):
            raise ValueError("project_id is required for Google Vertex")
        if not getattr(service_config, "location", None):
            raise ValueError("location is required for Google Vertex")
        return True

    def _check_aws_bedrock_api_key(self, model: str, service_config) -> bool:
        if not service_config.aws_access_key or not service_config.aws_secret_key:
            raise ValueError("AWS access key and secret key are required for Bedrock")
        return True

    def _check_assemblyai_api_key(self, model: str, service_config) -> bool:
        return True

    def _check_gladia_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_rime_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_minimax_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_smallest_api_key(self, model: str, api_key: str) -> bool:
        return True
