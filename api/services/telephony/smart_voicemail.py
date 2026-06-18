"""Smart-voicemail screening orchestrator (Strategy B).

Coordinates the inbound "screen-and-forward" flow for a single workflow run:

1. ``start()`` — park the caller in a private Vonage conference (hearing
   ringback), place an outbound *screening* leg to the designated human number
   answered by a listen-only AI pipeline, and persist the leg/conference state
   in Redis.
2. ``on_screening_result()`` — the terminal decision, called from either the
   screening pipeline's voicemail detector or the screening leg's Vonage event
   webhook (no-answer/busy/failed):
   - ``human``     → transfer the human leg into the caller's conference (bridge).
   - ``voicemail`` / ``no_answer`` → hang up the human leg and transfer the
     caller to the real AI workflow websocket.

The detector result and the no-answer event can race; a Redis SETNX latch
(``sv:resolved:{run}``) makes the first result win and the rest no-ops.

State only holds leg UUIDs, ids and URLs — provider credentials are reloaded
per action via the factory, scoped to the run's org + telephony config.
"""

import json
import os
from typing import Any, Dict, Optional

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL
from api.enums import WorkflowRunState

# Ringback / hold audio the caller hears while we screen the human leg. Must be
# a publicly reachable mp3. Falls back to silence (no MOH) when unset.
SMART_VOICEMAIL_RINGBACK_URL = os.getenv("SMART_VOICEMAIL_RINGBACK_URL") or None

# Default time to wait after the human answers but before the detector fires
# (silent pick-up) before defaulting to a human bridge.
SILENT_ANSWER_TIMEOUT_S = 8.0

_STATE_TTL_S = 600  # 10 minutes — longer than any reasonable screening window


def _state_key(run_id: int) -> str:
    return f"sv:run:{run_id}"


def _latch_key(run_id: int) -> str:
    return f"sv:resolved:{run_id}"


class SmartVoicemailOrchestrator:
    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self._redis = redis_client

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    async def _store_state(self, run_id: int, state: Dict[str, Any]) -> None:
        redis = await self._get_redis()
        await redis.setex(_state_key(run_id), _STATE_TTL_S, json.dumps(state))

    async def _load_state(self, run_id: int) -> Optional[Dict[str, Any]]:
        redis = await self._get_redis()
        raw = await redis.get(_state_key(run_id))
        return json.loads(raw) if raw else None

    async def get_state(self, run_id: int) -> Optional[Dict[str, Any]]:
        """Read-only accessor for the persisted screening state of a run.

        Used by the screening websocket route to resolve the screening leg's
        Vonage call UUID before building the listen-only pipeline. Returns the
        same dict shape stored by ``start()`` (or ``None`` if absent/expired).
        """
        return await self._load_state(run_id)

    async def _claim_resolution(self, run_id: int, result: str) -> bool:
        """Return True iff this caller won the race to resolve the screening."""
        redis = await self._get_redis()
        won = await redis.set(_latch_key(run_id), result, nx=True, ex=_STATE_TTL_S)
        return bool(won)

    # ------------------------------------------------------------------
    async def start(
        self,
        *,
        provider,
        smart_voicemail_config: Dict[str, Any],
        normalized_data,
        organization_id: int,
        telephony_configuration_id: int,
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
        backend_endpoint: str,
        wss_backend_endpoint: str,
    ) -> list:
        """Begin screening. Returns the NCCO (list) to answer the *caller* with.

        On any failure placing the screening leg, degrades gracefully to
        connecting the caller straight to the AI workflow websocket so the call
        is never dropped.
        """
        forward_to = smart_voicemail_config.get("forward_to_number")
        ring_timeout = int(smart_voicemail_config.get("ring_timeout_seconds", 25))
        conference_name = f"sv-{workflow_run_id}"
        caller_leg_uuid = normalized_data.call_id
        caller_id = normalized_data.from_number  # original caller's number

        screening_ws_url = (
            f"{wss_backend_endpoint}/api/v1/telephony/ws/screening/"
            f"{workflow_id}/{user_id}/{workflow_run_id}"
        )
        ai_ws_url = (
            f"{wss_backend_endpoint}/api/v1/telephony/ws/"
            f"{workflow_id}/{user_id}/{workflow_run_id}"
        )
        event_url = (
            f"{backend_endpoint}/api/v1/telephony/vonage/smart-voicemail/events/"
            f"{workflow_run_id}"
        )

        fallback_from = (
            provider.from_numbers[0] if getattr(provider, "from_numbers", None) else None
        )

        state: Dict[str, Any] = {
            "organization_id": organization_id,
            "telephony_configuration_id": telephony_configuration_id,
            "workflow_id": workflow_id,
            "user_id": user_id,
            "workflow_run_id": workflow_run_id,
            "caller_leg_uuid": caller_leg_uuid,
            "conference_name": conference_name,
            "screening_leg_uuid": None,
            "forward_to_number": forward_to,
            "ai_ws_url": ai_ws_url,
        }
        await self._store_state(workflow_run_id, state)

        try:
            resp = await provider.place_call_with_ncco(
                to_number=forward_to,
                from_number=caller_id,
                ncco=provider.build_screening_connect_ncco(screening_ws_url),
                event_url=event_url,
                ringing_timer=ring_timeout,
                fallback_from=fallback_from,
            )
            state["screening_leg_uuid"] = resp.get("uuid")
            await self._store_state(workflow_run_id, state)
            logger.info(
                f"[run {workflow_run_id}] smart-voicemail screening leg placed "
                f"to {forward_to} (uuid={state['screening_leg_uuid']})"
            )
        except Exception as e:
            logger.error(
                f"[run {workflow_run_id}] failed to place screening leg: {e}; "
                f"connecting caller directly to AI"
            )
            # Degrade: AI answers the caller directly.
            return provider.build_ai_connect_ncco(ai_ws_url)

        return provider.build_caller_hold_ncco(
            conference_name, SMART_VOICEMAIL_RINGBACK_URL
        )

    # ------------------------------------------------------------------
    async def on_screening_result(self, workflow_run_id: int, result: str) -> None:
        """Terminal decision. ``result`` ∈ {"human", "voicemail", "no_answer"}.

        Idempotent: only the first caller to claim the latch acts.
        """
        if not await self._claim_resolution(workflow_run_id, result):
            logger.info(
                f"[run {workflow_run_id}] smart-voicemail already resolved; "
                f"ignoring late '{result}'"
            )
            return

        state = await self._load_state(workflow_run_id)
        if not state:
            logger.warning(
                f"[run {workflow_run_id}] no smart-voicemail state for '{result}'"
            )
            return

        from api.services.telephony.factory import get_telephony_provider_by_id

        provider = await get_telephony_provider_by_id(
            state["telephony_configuration_id"], state["organization_id"]
        )

        screening_leg = state.get("screening_leg_uuid")
        caller_leg = state.get("caller_leg_uuid")
        conference_name = state["conference_name"]

        if result == "human":
            # Bridge: move the human leg into the caller's conference.
            if screening_leg:
                await provider.transfer_leg_to_ncco(
                    screening_leg, provider.build_join_conference_ncco(conference_name)
                )
            logger.info(
                f"[run {workflow_run_id}] human answered — bridged to conference "
                f"{conference_name}"
            )
            await self._mark_transferred(workflow_run_id)
        else:
            # voicemail / no_answer → drop the human leg, hand caller to the AI.
            if screening_leg:
                await provider.hangup_leg(screening_leg)
            if caller_leg:
                await provider.transfer_leg_to_ncco(
                    caller_leg, provider.build_ai_connect_ncco(state["ai_ws_url"])
                )
            logger.info(
                f"[run {workflow_run_id}] '{result}' — caller handed to AI workflow"
            )

        await self._cleanup(workflow_run_id)

    async def on_screening_answered(self, workflow_run_id: int) -> None:
        """Arm a watchdog: if the detector never fires after a silent pick-up,
        default to a human bridge."""
        import asyncio

        async def _watchdog():
            await asyncio.sleep(SILENT_ANSWER_TIMEOUT_S)
            await self.on_screening_result(workflow_run_id, "human")

        asyncio.create_task(_watchdog())

    # ------------------------------------------------------------------
    async def _mark_transferred(self, workflow_run_id: int) -> None:
        """Best-effort: the AI never runs on a human bridge, so close the run."""
        try:
            from api.db import db_client

            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                state=WorkflowRunState.COMPLETED.value,
                gathered_context={"smart_voicemail_outcome": "transferred_to_human"},
            )
        except Exception as e:
            logger.warning(
                f"[run {workflow_run_id}] failed to mark run transferred: {e}"
            )

    async def _cleanup(self, workflow_run_id: int) -> None:
        try:
            redis = await self._get_redis()
            await redis.delete(_state_key(workflow_run_id))
        except Exception as e:
            logger.warning(f"[run {workflow_run_id}] smart-voicemail cleanup: {e}")


_orchestrator: Optional[SmartVoicemailOrchestrator] = None


def get_smart_voicemail_orchestrator() -> SmartVoicemailOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SmartVoicemailOrchestrator()
    return _orchestrator
