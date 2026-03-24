"""Generative AI services for embeddings and document processing.

The embedding stack pulls in optional document-processing dependencies such as
docling. Keep those imports lazy so modules that only need the JSON parser can
import this package without paying the heavier startup cost.
"""

from .json_parser import parse_llm_json

__all__ = [
    "BaseEmbeddingService",
    "EmbeddingAPIKeyNotConfiguredError",
    "OpenAIEmbeddingService",
    "parse_llm_json",
]


def __getattr__(name: str):
    if name in {
        "BaseEmbeddingService",
        "EmbeddingAPIKeyNotConfiguredError",
        "OpenAIEmbeddingService",
    }:
        from .embedding import (
            BaseEmbeddingService,
            EmbeddingAPIKeyNotConfiguredError,
            OpenAIEmbeddingService,
        )

        globals().update(
            {
                "BaseEmbeddingService": BaseEmbeddingService,
                "EmbeddingAPIKeyNotConfiguredError": EmbeddingAPIKeyNotConfiguredError,
                "OpenAIEmbeddingService": OpenAIEmbeddingService,
            }
        )
        return globals()[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
