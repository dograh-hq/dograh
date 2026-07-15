"""Paygent REST API client (pure httpx, no SDK).

All network I/O goes through ``post_paygent`` which is the single delivery
coroutine used by the completion handler.  The individual tracker functions
(session, STT, TTS, LLM, STS, indicator) mirror the exact shape of the
Paygent REST API documented in ``paygent_sdk/voice_client.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

_DEFAULT_BASE_URL = "https://cp-api.withpaygent.com"
_REQUEST_TIMEOUT = 15  # seconds – generous for post-call delivery


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------


class PaygentDeliveryConfig(BaseModel):
    """Validated delivery configuration, filled from the node data."""

    base_url: str = _DEFAULT_BASE_URL
    api_key: str
    agent_id: str
    customer_id: str

    @field_validator("api_key", "agent_id", "customer_id")
    @classmethod
    def _must_not_be_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def _normalise_base_url(cls, value: str) -> str:
        return (value or _DEFAULT_BASE_URL).rstrip("/")


# ---------------------------------------------------------------------------
# Live-call snapshot (collected during the call, delivered after)
# ---------------------------------------------------------------------------


@dataclass
class PaygentCallSnapshot:
    """Immutable snapshot produced at call-finish; passed to ``deliver``."""

    session_id: str
    agent_id: str
    customer_id: str
    is_realtime: bool

    # Usage buckets filled from PipelineMetricsAggregator + user_config
    stt_provider: str = ""
    stt_model: str = ""
    stt_audio_seconds: float = 0.0

    llm_provider: str = ""
    llm_model: str = ""
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_cached_tokens: int = 0

    tts_provider: str = ""
    tts_model: str = ""
    tts_characters: int = 0

    sts_provider: str = ""
    sts_model: str = ""
    sts_usage_metadata: dict[str, Any] | None = None

    # Final call status / total duration seconds
    call_disposition: str = "completed"
    total_duration_seconds: int = 0
    indicator: str = "per-minute-call"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "customer_id": self.customer_id,
            "is_realtime": self.is_realtime,
            "stt": {
                "provider": self.stt_provider,
                "model": self.stt_model,
                "audio_seconds": self.stt_audio_seconds,
            },
            "llm": {
                "provider": self.llm_provider,
                "model": self.llm_model,
                "prompt_tokens": self.llm_prompt_tokens,
                "completion_tokens": self.llm_completion_tokens,
                "cached_tokens": self.llm_cached_tokens,
            },
            "tts": {
                "provider": self.tts_provider,
                "model": self.tts_model,
                "characters": self.tts_characters,
            },
            "sts": {
                "provider": self.sts_provider,
                "model": self.sts_model,
                "usage_metadata": self.sts_usage_metadata,
            },
            "call_disposition": self.call_disposition,
            "total_duration_seconds": self.total_duration_seconds,
            "indicator": self.indicator,
        }


# ---------------------------------------------------------------------------
# REST delivery helpers
# ---------------------------------------------------------------------------


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "paygent-api-key": api_key,
    }


async def _post(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    payload: dict[str, Any],
    *,
    label: str,
) -> None:
    """POST ``payload`` to ``url``; raises on 4xx/5xx or network failure.

    Intentionally non-swallowing: callers in ``deliver()`` each wrap this in
    their own try/except to build the ``errors`` list and the ``status`` field.
    """
    resp = await client.post(url, json=payload, headers=_headers(api_key))
    resp.raise_for_status()


async def deliver(
    config: PaygentDeliveryConfig,
    snapshot: PaygentCallSnapshot,
) -> dict[str, Any]:
    """
    Execute the full Paygent REST call sequence for one completed call:

    1. initialize_voice_session
    2. track_stt            (if STT is used, i.e. not realtime-only)
    3. track_llm
    4. track_tts            (if TTS is used, i.e. not realtime-only)
    5. track_sts            (if realtime / STS model used)
    6. set_indicator        (always; marks end of session)

    Returns a result dict merged into ``workflow_run.annotations``.
    """
    base = config.base_url
    api_key = config.api_key
    session_id = snapshot.session_id

    delivered_steps: list[str] = []
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        # 1. Initialize voice session ----------------------------------------
        try:
            await _post(
                client,
                f"{base}/api/v1/voice/session",
                api_key,
                {
                    "sessionId": session_id,
                    "agentId": snapshot.agent_id,
                    "customerId": snapshot.customer_id,
                },
                label="initialize_voice_session",
            )
            delivered_steps.append("session_init")
        except Exception as exc:
            errors.append(f"session_init: {exc}")

        # 2. Track STT (only for non-realtime pipelines) ---------------------
        if not snapshot.is_realtime and snapshot.stt_audio_seconds > 0:
            try:
                await _post(
                    client,
                    f"{base}/api/v1/voice/stt",
                    api_key,
                    {
                        "sessionId": session_id,
                        "audioMinutes": snapshot.stt_audio_seconds / 60.0,
                        "provider": snapshot.stt_provider,
                        "model": snapshot.stt_model,
                        "plan": "",
                    },
                    label="track_stt",
                )
                delivered_steps.append("track_stt")
            except Exception as exc:
                errors.append(f"track_stt: {exc}")

        # 3. Track LLM -------------------------------------------------------
        if snapshot.llm_prompt_tokens > 0 or snapshot.llm_completion_tokens > 0:
            llm_payload: dict[str, Any] = {
                "sessionId": session_id,
                "provider": snapshot.llm_provider,
                "model": snapshot.llm_model,
                "plan": "",
                "promptTokens": snapshot.llm_prompt_tokens,
                "completionTokens": snapshot.llm_completion_tokens,
            }
            if snapshot.llm_cached_tokens > 0:
                llm_payload["cachedTokens"] = snapshot.llm_cached_tokens
            try:
                await _post(
                    client,
                    f"{base}/api/v1/voice/llm",
                    api_key,
                    llm_payload,
                    label="track_llm",
                )
                delivered_steps.append("track_llm")
            except Exception as exc:
                errors.append(f"track_llm: {exc}")

        # 4. Track TTS (only for non-realtime pipelines) ---------------------
        if not snapshot.is_realtime and snapshot.tts_characters > 0:
            try:
                await _post(
                    client,
                    f"{base}/api/v1/voice/tts",
                    api_key,
                    {
                        "sessionId": session_id,
                        "provider": snapshot.tts_provider,
                        "model": snapshot.tts_model,
                        "plan": "",
                        "characters": snapshot.tts_characters,
                    },
                    label="track_tts",
                )
                delivered_steps.append("track_tts")
            except Exception as exc:
                errors.append(f"track_tts: {exc}")

        # 5. Track STS (Speech-to-Speech) for Realtime Models ----------------
        if snapshot.is_realtime:
            metadata = snapshot.sts_usage_metadata or {}
            # Only append connection minutes if we don't already have a rich token payload
            # (e.g. from OpenAI Realtime or Gemini Live)
            if (
                "connection" not in metadata
                and "prompt_tokens" not in metadata
                and "input" not in metadata
            ):
                metadata["connection"] = {
                    "minutes": snapshot.total_duration_seconds / 60.0
                }

            try:
                await _post(
                    client,
                    f"{base}/api/v1/voice/speech-to-speech",
                    api_key,
                    {
                        "sessionId": session_id,
                        "provider": snapshot.sts_provider,
                        "model": snapshot.sts_model,
                        "plan": "",
                        "usageMetadata": metadata,
                    },
                    label="track_sts",
                )
                delivered_steps.append("track_sts")
            except Exception as exc:
                errors.append(f"track_sts: {exc}")

        # 6. Set indicator (end-of-session marker) ---------------------------
        try:
            await _post(
                client,
                f"{base}/api/v1/voice/indicator",
                api_key,
                {
                    "sessionId": session_id,
                    "indicator": snapshot.indicator,
                    "totalDuration": snapshot.total_duration_seconds / 60.0,
                },
                label="set_indicator",
            )
            delivered_steps.append("set_indicator")
        except Exception as exc:
            errors.append(f"set_indicator: {exc}")

    return _result(session_id, delivered_steps, errors)


def _result(
    session_id: str,
    delivered_steps: list[str],
    errors: list[str],
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "delivered_steps": delivered_steps,
        "errors": errors,
        "status": "ok" if not errors else ("partial" if delivered_steps else "failed"),
    }
