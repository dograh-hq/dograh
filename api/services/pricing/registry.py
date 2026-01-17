"""
Main pricing registry that combines all service type pricing models.
"""

from typing import Dict

from .embeddings import EMBEDDINGS_PRICING
from .llm import LLM_PRICING
from .stt import STT_PRICING
from .tts import TTS_PRICING

# Combined pricing registry for all service types
PRICING_REGISTRY: Dict = {
    "llm": LLM_PRICING,
    "tts": TTS_PRICING,
    "stt": STT_PRICING,
    "embeddings": EMBEDDINGS_PRICING,
}
