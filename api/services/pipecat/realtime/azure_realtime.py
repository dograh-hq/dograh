"""Azure authentication and transport with Dograh's OpenAI realtime behavior."""

from api.services.pipecat.realtime.openai_realtime import DograhOpenAIRealtimeLLMService
from pipecat.services.azure.realtime.llm import AzureRealtimeLLMService


class DograhAzureRealtimeLLMService(
    DograhOpenAIRealtimeLLMService, AzureRealtimeLLMService
):
    """Share OpenAI conversation handling while retaining Azure's constructor."""
