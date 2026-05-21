from typing import Optional, TypedDict

import openai
from deepgram import DeepgramClient
from groq import Groq

# try:
#     from pyneuphonic import Neuphonic
# except ImportError:
#     Neuphonic = None
from api.schemas.user_configuration import (
    UserConfiguration,
)
from api.services.configuration.registry import ServiceConfig, ServiceProviders
from api.services.mps_service_key_client import mps_service_key_client

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
            ServiceProviders.ELEVENLABS.value: self._validate_elevenlabs_api_key,
            ServiceProviders.GOOGLE.value: self._check_google_api_key,
            ServiceProviders.AZURE.value: self._check_azure_api_key,
            ServiceProviders.CARTESIA.value: self._check_cartesia_api_key,
            ServiceProviders.DOGRAH.value: self._check_dograh_api_key,
            ServiceProviders.SARVAM.value: self._check_sarvam_api_key,
            ServiceProviders.SPEECHMATICS.value: self._check_speechmatics_api_key,
            ServiceProviders.CAMB.value: self._check_camb_api_key,
            ServiceProviders.AWS_BEDROCK.value: self._check_aws_bedrock_api_key,
            ServiceProviders.SPEACHES.value: self._check_speaches_api_key,
            ServiceProviders.OPENAI_REALTIME.value: self._check_openai_api_key,
            ServiceProviders.GOOGLE_REALTIME.value: self._check_google_api_key,
            ServiceProviders.GOOGLE_VERTEX_REALTIME.value: self._check_google_vertex_realtime_api_key,
            ServiceProviders.ASSEMBLYAI.value: self._check_assemblyai_api_key,
            ServiceProviders.GLADIA.value: self._check_gladia_api_key,
            ServiceProviders.RIME.value: self._check_rime_api_key,
        }

    async def validate(
        self,
        configuration: UserConfiguration,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> APIKeyStatusResponse:
        self._auth_context: AuthContext = {
            "organization_id": organization_id,
            "created_by": created_by,
        }
        status_list = []

        status_list.extend(self._validate_service(configuration.llm, "llm"))
        status_list.extend(self._validate_service(configuration.stt, "stt"))
        status_list.extend(self._validate_service(configuration.tts, "tts"))
        # Embeddings is optional - only validate if configured
        status_list.extend(
            self._validate_service(
                configuration.embeddings, "embeddings", required=False
            )
        )
        # Realtime is optional - only validate if is_realtime is enabled
        if configuration.is_realtime:
            status_list.extend(
                self._validate_service(
                    configuration.realtime, "realtime", required=True
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

        api_key = service_config.api_key

        try:
            if not self._check_api_key(provider, api_key):
                return [
                    {"model": service_name, "message": f"Invalid {provider} API key"}
                ]
        except ValueError as e:
            return [{"model": service_name, "message": str(e)}]

        return []

    def _check_api_key(self, provider: str, api_key: str) -> bool:
        """Check if an API key for a provider is valid."""
        validator = self._validator_map.get(provider)
        if not validator:
            return False

        return validator(provider, api_key)

    def _check_openai_api_key(self, model: str, api_key: str) -> bool:
        client = openai.OpenAI(api_key=api_key)
        try:
            client.models.list()
            return True
        except openai.AuthenticationError:
            return False

    def _check_deepgram_api_key(self, model: str, api_key: str) -> bool:
        try:
            deepgram = DeepgramClient(api_key=api_key)
            deepgram.manage.v1.projects.list()
            return True
        except Exception:
            return False

    def _check_groq_api_key(self, model: str, api_key: str) -> bool:
        client = Groq(api_key=api_key)
        try:
            client.models.list()
            return True
        except Exception:
            return False

    def _validate_elevenlabs_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_google_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_azure_api_key(self, model: str, api_key: str) -> bool:
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

    def _check_speechmatics_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_camb_api_key(self, model: str, api_key: str) -> bool:
        return True

    def _check_speaches_api_key(self, model: str, service_config) -> bool:
        if not getattr(service_config, "base_url", None):
            raise ValueError("base_url is required for Speaches services")
        return True

    def _check_google_vertex_realtime_api_key(self, model: str, service_config) -> bool:
        if not getattr(service_config, "project_id", None):
            raise ValueError("project_id is required for Google Vertex Realtime")
        if not getattr(service_config, "location", None):
            raise ValueError("location is required for Google Vertex Realtime")
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
