"""
Embeddings pricing models for different providers.

Prices are per token for embedding models.
"""

from decimal import Decimal
from typing import Dict

from api.services.configuration.registry import ServiceProviders

from .models import PricingModel


class EmbeddingPricingModel(PricingModel):
    """Pricing model for token-based embedding services."""

    def __init__(self, token_price: Decimal):
        """Initialize with price per token.

        Args:
            token_price: Cost per token for embedding
        """
        self.token_price = token_price

    def calculate_cost(self, token_count: int) -> Decimal:
        """Calculate cost for embedding token usage."""
        return Decimal(token_count) * self.token_price


# Embeddings pricing registry
EMBEDDINGS_PRICING: Dict[str, Dict[str, EmbeddingPricingModel]] = {
    ServiceProviders.OPENAI: {
        "text-embedding-3-small": EmbeddingPricingModel(
            token_price=Decimal("0.02") / 1_000_000,  # $0.02 per 1M tokens
        ),
        "text-embedding-3-large": EmbeddingPricingModel(
            token_price=Decimal("0.13") / 1_000_000,  # $0.13 per 1M tokens
        ),
        "text-embedding-ada-002": EmbeddingPricingModel(
            token_price=Decimal("0.10") / 1_000_000,  # $0.10 per 1M tokens (legacy)
        ),
    },
}
