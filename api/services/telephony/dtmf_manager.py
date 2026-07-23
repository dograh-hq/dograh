"""Redis-based DTMF event coordination service

Handles DTMF event publishing and subscription
"""

import asyncio
from typing import AsyncGenerator, Dict, Optional

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL
from api.services.telephony.dtmf_event_protocol import DTMFEvent, DTMFRedisChannels


class DTMFManager:
    """Manages DTMF events using Redis Pub/Sub."""

    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self._redis_client = redis_client
        self._pubsub_connections: Dict[str, aioredis.client.PubSub] = {}

    async def _get_redis(self) -> aioredis.Redis:
        """Get Redis client instance."""
        if not self._redis_client:
            self._redis_client = await aioredis.from_url(
                REDIS_URL, decode_responses=True
            )
        return self._redis_client

    async def publish_dtmf_event(self, call_id: str, event: DTMFEvent) -> None:
        """Publish a DTMF event to the specific call channel."""
        try:
            redis = await self._get_redis()
            channel = DTMFRedisChannels.dtmf_channel(call_id)
            await redis.publish(channel, event.to_json())
            logger.debug(f"Published DTMF event for call {call_id}: {event.digit}")
        except Exception as e:
            logger.error(f"Failed to publish DTMF event for call {call_id}: {e}")

    async def subscribe_dtmf_events(self, call_id: str) -> AsyncGenerator[DTMFEvent, None]:
        """Subscribe to DTMF events for a specific call."""
        redis = await self._get_redis()
        pubsub = redis.pubsub()
        channel = DTMFRedisChannels.dtmf_channel(call_id)
        
        try:
            await pubsub.subscribe(channel)
            self._pubsub_connections[call_id] = pubsub
            logger.debug(f"Subscribed to DTMF events for call {call_id}")
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        event = DTMFEvent.from_json(message["data"])
                        yield event
                    except Exception as e:
                        logger.error(f"Failed to parse DTMF event: {e}")
        except asyncio.CancelledError:
            logger.debug(f"DTMF subscription cancelled for call {call_id}")
            raise
        finally:
            await self.unsubscribe_dtmf_events(call_id)

    async def unsubscribe_dtmf_events(self, call_id: str) -> None:
        """Unsubscribe from DTMF events for a specific call."""
        pubsub = self._pubsub_connections.pop(call_id, None)
        if pubsub:
            try:
                channel = DTMFRedisChannels.dtmf_channel(call_id)
                await pubsub.unsubscribe(channel)
                await pubsub.close()
                logger.debug(f"Unsubscribed from DTMF events for call {call_id}")
            except Exception as e:
                logger.error(f"Failed to unsubscribe from DTMF events for call {call_id}: {e}")

# Global instance for easy import
dtmf_manager = DTMFManager()
