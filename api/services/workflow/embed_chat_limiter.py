"""Isolated abuse limits for anonymous embed-chat execution.

These sliding-window buckets deliberately do not use the organization call
concurrency service: text chat must not consume voice-call slots or affect the
fleet autoscaling projection.
"""

from api.services.call_concurrency.rate_limiter import rate_limiter

EMBED_CHAT_INIT_RATE_LIMIT_PER_SECOND = 5
EMBED_CHAT_MESSAGE_RATE_LIMIT_PER_SECOND = 5


async def allow_embed_chat_init(embed_token_id: int) -> bool:
    """Limit greeting-producing initializations for one public embed token."""
    return await rate_limiter.acquire_token(
        embed_token_id,
        EMBED_CHAT_INIT_RATE_LIMIT_PER_SECOND,
        scope_key=f"public_embed_chat:init:{embed_token_id}",
    )


async def allow_embed_chat_message(workflow_run_id: int) -> bool:
    """Limit billable message execution independently for each chat session."""
    return await rate_limiter.acquire_token(
        workflow_run_id,
        EMBED_CHAT_MESSAGE_RATE_LIMIT_PER_SECOND,
        scope_key=f"public_embed_chat:messages:{workflow_run_id}",
    )
