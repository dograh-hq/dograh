"""Compatibility wrapper for the newer OpenAI Realtime Dograh service.

Keep this import path alive for tests and any older code, but route the actual
construction through the main-style implementation in
``api.services.pipecat.realtime.openai_realtime``.
"""

from api.services.pipecat.realtime.openai_realtime import (
    DograhOpenAIRealtimeLLMService,
    create_openai_realtime_llm_service,
)
