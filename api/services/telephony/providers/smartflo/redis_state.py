"""Redis state management for Smartflo calls.

Maps short-lived call identifiers (ref_id, call_id, phone numbers)
to Dograh agent_id, workflow_id, workflow_run_id, and organization_id.
"""

import json
from typing import Any, Dict, Optional
from loguru import logger
import redis.asyncio as redis

from api.constants import REDIS_URL

_redis_pool: Optional[redis.Redis] = None


def get_redis_client() -> redis.Redis:
    """Get or create the async Redis client."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=5.0,
        )
    return _redis_pool


async def save_smartflo_call_state(
    ref_id: Optional[str],
    call_id: Optional[str],
    customer_number: Optional[str],
    state: Dict[str, Any],
    ttl: int = 7200,  # 2 hours TTL
) -> None:
    """Save call state indexed by multiple lookup keys for fail-safe retrieval."""
    client = get_redis_client()
    payload = json.dumps(state)

    keys = set()
    if ref_id:
        keys.add(f"smartflo:call:ref:{ref_id}")
        keys.add(f"smartflo:call:{ref_id}")
    if call_id:
        keys.add(f"smartflo:call:id:{call_id}")
        keys.add(f"smartflo:call:{call_id}")
    if customer_number:
        clean_num = str(customer_number).strip().lstrip("+")
        keys.add(f"smartflo:call:phone:{clean_num}")

    for k in keys:
        try:
            await client.set(k, payload, ex=ttl)
        except Exception as e:
            logger.warning(f"Failed to cache Smartflo call state on key {k}: {e}")


async def get_smartflo_call_state(identifier: str) -> Optional[Dict[str, Any]]:
    """Retrieve call state by ref_id, call_id, or phone number."""
    if not identifier:
        return None
    client = get_redis_client()
    clean_id = str(identifier).strip().lstrip("+")

    candidate_keys = [
        f"smartflo:call:{clean_id}",
        f"smartflo:call:ref:{clean_id}",
        f"smartflo:call:id:{clean_id}",
        f"smartflo:call:phone:{clean_id}",
    ]

    for k in candidate_keys:
        try:
            raw = await client.get(k)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"Error reading Redis key {k}: {e}")

    return None


async def delete_smartflo_call_state(
    ref_id: Optional[str] = None,
    call_id: Optional[str] = None,
    customer_number: Optional[str] = None,
) -> None:
    """Clean up call state keys."""
    client = get_redis_client()
    keys = set()
    if ref_id:
        keys.add(f"smartflo:call:ref:{ref_id}")
        keys.add(f"smartflo:call:{ref_id}")
    if call_id:
        keys.add(f"smartflo:call:id:{call_id}")
        keys.add(f"smartflo:call:{call_id}")
    if customer_number:
        clean_num = str(customer_number).strip().lstrip("+")
        keys.add(f"smartflo:call:phone:{clean_num}")

    for k in keys:
        try:
            await client.delete(k)
        except Exception as e:
            logger.debug(f"Redis cleanup error for {k}: {e}")


async def get_did_mapping(to_number: Optional[str]) -> Optional[str]:
    """Check if a DID is mapped to an agent in Redis (e.g. did_map:{toNumber})."""
    if not to_number:
        return None
    client = get_redis_client()
    raw_to = str(to_number).strip()
    clean_to = raw_to.lstrip("+")
    for key in (f"did_map:{raw_to}", f"did_map:{clean_to}", f"smartflo:did_map:{clean_to}"):
        try:
            val = await client.get(key)
            if val:
                return str(val)
        except Exception as e:
            logger.debug(f"Error checking DID mapping for {key}: {e}")
    return None


async def get_default_agent_id() -> Optional[str]:
    """Check default fallback agent id in Redis."""
    client = get_redis_client()
    for key in ("default_agent_id", "smartflo:default_agent_id"):
        try:
            val = await client.get(key)
            if val:
                return str(val)
        except Exception:
            pass
    return None
