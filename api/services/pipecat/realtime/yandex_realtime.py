"""Yandex authentication and transport with Dograh's OpenAI realtime behavior."""

from api.services.pipecat.realtime.openai_realtime import DograhOpenAIRealtimeLLMService
from pipecat.services.yandex.realtime.llm import YandexRealtimeLLMService


class DograhYandexRealtimeLLMService(DograhOpenAIRealtimeLLMService, YandexRealtimeLLMService):
    """Share OpenAI conversation handling while retaining Yandex's constructor."""
