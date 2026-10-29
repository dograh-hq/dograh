"""Short-lived, capability-protected transfer audio shared across API workers.

Small bounded clips live in Redis for five minutes, avoiding public MinIO
configuration and permanent recordings of a private transfer introduction.
"""

import base64
import hashlib
import secrets

from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.utils.common import get_backend_endpoints

TRANSFER_AUDIO_TTL = 300
MAX_TRANSFER_AUDIO_BYTES = 1_440_044  # 15 seconds, mono PCM16 at up to 48 kHz + WAV


def _audio_key(token: str) -> str:
    return "transfer:audio:" + hashlib.sha256(token.encode()).hexdigest()


async def store_transfer_audio(audio: bytes) -> str:
    if not audio or len(audio) > MAX_TRANSFER_AUDIO_BYTES:
        raise ValueError("Invalid transfer audio size")
    token = secrets.token_urlsafe(32)
    backend, _ = await get_backend_endpoints()
    manager = await get_call_transfer_manager()
    redis = await manager._get_redis()
    await redis.setex(
        _audio_key(token), TRANSFER_AUDIO_TTL, base64.b64encode(audio).decode("ascii")
    )
    return f"{backend}/api/v1/telephony/twilio/transfer-audio/{token}"


async def get_transfer_audio(token: str) -> bytes | None:
    if len(token) != 43 or not all(c.isalnum() or c in "-_" for c in token):
        return None
    manager = await get_call_transfer_manager()
    redis = await manager._get_redis()
    value = await redis.get(_audio_key(token))
    return base64.b64decode(value) if value else None
