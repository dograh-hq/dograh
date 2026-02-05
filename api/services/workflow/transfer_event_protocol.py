"""Transfer call event protocol for Redis-based coordination.

Simple protocol for awaiting transfer completion signal from external trigger.
"""

import asyncio
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL


class TransferEventType(str, Enum):
    """Types of transfer events."""

    TRANSFER_PROCEED = "transfer_proceed"
    TRANSFER_CANCEL = "transfer_cancel"


@dataclass
class TransferEvent:
    """Event sent to signal transfer status."""

    type: str
    workflow_run_id: int
    message: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "TransferEvent":
        return cls(**json.loads(data))


class TransferRedisChannels:
    """Redis channel naming for transfer events."""

    @staticmethod
    def transfer_await(workflow_run_id: int) -> str:
        """Channel for awaiting transfer completion."""
        return f"transfer:await:{workflow_run_id}"


async def wait_for_transfer_signal(
    workflow_run_id: int,
    timeout_seconds: float = 30.0,
) -> Optional[TransferEvent]:
    """Wait for a transfer signal on Redis pub/sub.

    Args:
        workflow_run_id: The workflow run ID to wait for
        timeout_seconds: How long to wait before timing out

    Returns:
        TransferEvent if received, None if timed out
    """
    channel = TransferRedisChannels.transfer_await(workflow_run_id)
    redis_client = await aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = redis_client.pubsub()

    try:
        await pubsub.subscribe(channel)
        logger.info(f"Waiting for transfer signal on channel: {channel}")

        async def listen_for_event() -> Optional[TransferEvent]:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    event = TransferEvent.from_json(message["data"])
                    logger.info(f"Received transfer event: {event.type}")
                    return event
            # pubsub.listen() ended (connection closed) - return None
            return None

        # Wait with timeout
        event = await asyncio.wait_for(listen_for_event(), timeout=timeout_seconds)
        return event

    except asyncio.TimeoutError:
        logger.warning(f"Transfer signal timed out after {timeout_seconds}s")
        return None
    except Exception as e:
        logger.error(f"Error waiting for transfer signal: {e}")
        return None
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await redis_client.aclose()


async def send_transfer_signal(
    workflow_run_id: int,
    event_type: TransferEventType = TransferEventType.TRANSFER_PROCEED,
    message: Optional[str] = None,
) -> bool:
    """Send a transfer signal to unblock a waiting handler.

    Args:
        workflow_run_id: The workflow run ID to signal
        event_type: Type of signal (proceed or cancel)
        message: Optional message

    Returns:
        True if signal was sent successfully
    """
    channel = TransferRedisChannels.transfer_await(workflow_run_id)
    redis_client = await aioredis.from_url(REDIS_URL, decode_responses=True)

    try:
        event = TransferEvent(
            type=event_type.value,
            workflow_run_id=workflow_run_id,
            message=message,
        )
        await redis_client.publish(channel, event.to_json())
        logger.info(f"Sent transfer signal to channel: {channel}")
        return True
    except Exception as e:
        logger.error(f"Error sending transfer signal: {e}")
        return False
    finally:
        await redis_client.aclose()
