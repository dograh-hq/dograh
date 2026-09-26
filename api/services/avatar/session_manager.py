"""Per-run SpatialReal avatar sessions for host-mode driving.

In host mode the backend feeds bot PCM (tapped from the Pipecat pipeline)
into a SpatialReal ``AvatarSession`` and relays the encoded audio and
animation messages it returns to the browser over the avatar relay
WebSocket (``api/routes/avatar_stream.py``). The browser AvatarKit SDK, in
``DrivingServiceMode.host``, decodes and renders them
(``yieldAudioData`` / ``yieldFramesData``).

Relay wire format (binary WS frames, prefixed with a 2-byte header):
    byte 0: message type — 0x01 encoded audio, 0x02 animation frames
    byte 1: flags — bit 0 set on the last message of a conversation round
    bytes 2+: opaque payload from the server SDK (relayed verbatim)

Control messages (session ready / error / interrupted) travel as JSON text
frames on the same socket.

State is in-process only, which is safe because deployments run a single
FastAPI worker (FASTAPI_WORKERS=1 — the same constraint Sakinah simulations
rely on; see README).
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from loguru import logger

from api.constants import (
    SPATIALREAL_API_KEY,
    SPATIALREAL_APP_ID,
    SPATIALREAL_AVATAR_ID,
    SPATIALREAL_CONSOLE_ENDPOINT,
    SPATIALREAL_INGRESS_ENDPOINT,
    SPATIALREAL_MAX_SESSIONS,
    SPATIALREAL_MODE,
)
from api.services.posthog_client import capture_event

MSG_TYPE_AUDIO = 0x01
MSG_TYPE_FRAMES = 0x02
FLAG_LAST = 0x01

# Bounded so a browser that stops reading can't grow memory without limit;
# dropping oldest keeps the avatar roughly live rather than lagging behind.
RELAY_QUEUE_MAX = 512


def avatar_host_mode_enabled() -> bool:
    return SPATIALREAL_MODE == "host" and bool(
        SPATIALREAL_APP_ID and SPATIALREAL_API_KEY and SPATIALREAL_AVATAR_ID
    )


def resolve_avatar_settings(workflow_configurations: dict | None) -> dict:
    """Effective avatar settings for a run: workflow block over env defaults.

    The workflow's ``avatar_configuration`` opts a workflow in (``enabled``)
    and may override ``avatar_id`` and ``mode``; unset values fall back to
    the deployment-wide SPATIALREAL_* env configuration. Credentials always
    come from the environment. Returns
    ``{"enabled", "mode", "avatar_id"}`` where enabled is False whenever
    credentials are missing or mode is off.
    """
    cfg_block = (workflow_configurations or {}).get("avatar_configuration")
    cfg = cfg_block or {}
    credentials_ok = bool(SPATIALREAL_APP_ID and SPATIALREAL_API_KEY)
    avatar_id = cfg.get("avatar_id") or SPATIALREAL_AVATAR_ID
    mode = cfg.get("mode") or SPATIALREAL_MODE
    # Workflows that have never saved an avatar block follow the deployment
    # default (env-driven on/off); once a block exists its enabled flag rules.
    opted_in = (
        bool(cfg.get("enabled", False)) if cfg_block is not None else True
    )
    enabled = (
        opted_in
        and credentials_ok
        and bool(avatar_id)
        and mode in ("sdk", "host")
    )
    return {
        "enabled": enabled,
        "mode": mode if enabled else "off",
        "avatar_id": avatar_id if enabled else None,
    }


class AvatarRunSession:
    """Owns one SpatialReal session for one workflow run.

    The pipeline tap calls :meth:`send_audio` / :meth:`interrupt`; the relay
    WebSocket consumes :attr:`relay_queue`. Failures flip :attr:`failed` and
    enqueue an error control message — the call itself must never break
    because the avatar backend is unavailable.
    """

    def __init__(self, workflow_run_id: int, avatar_id: str | None = None):
        self.workflow_run_id = workflow_run_id
        self.avatar_id = avatar_id or SPATIALREAL_AVATAR_ID
        self.relay_queue: asyncio.Queue = asyncio.Queue(maxsize=RELAY_QUEUE_MAX)
        self.failed = False
        self.started = False
        self._session = None
        self._closing = False
        self._started_at: float | None = None
        self._first_audio_sent_at: float | None = None
        self._first_frame_latency_ms: int | None = None

    async def start(self) -> bool:
        """Init token + ingress WebSocket. Returns False on failure."""
        try:
            from avatarkit import (
                AudioFormat,
                OggOpusEncoderConfig,
                new_avatar_session,
            )

            self._session = new_avatar_session(
                avatar_id=self.avatar_id,
                api_key=SPATIALREAL_API_KEY,
                app_id=SPATIALREAL_APP_ID,
                console_endpoint_url=SPATIALREAL_CONSOLE_ENDPOINT,
                ingress_endpoint_url=SPATIALREAL_INGRESS_ENDPOINT,
                expire_at=datetime.now(timezone.utc) + timedelta(hours=12),
                sample_rate=16000,
                # Ogg Opus in-SDK encoding: send_audio() accepts raw PCM and
                # on_encoded_audio yields browser-playable encoded audio for
                # the relay (raw PCM would otherwise never reach the client).
                audio_format=AudioFormat.OGG_OPUS,
                ogg_opus_encoder=OggOpusEncoderConfig(),
                on_encoded_audio=self._on_encoded_audio,
                transport_frames=self._on_frames,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            await self._session.init()
            await self._session.start()
            self.started = True
            self._started_at = time.monotonic()
            self._enqueue_control({"type": "avatar-ready"})
            logger.info(
                f"Avatar host session started for run {self.workflow_run_id}"
            )
            capture_event(
                distinct_id=f"workflow_run:{self.workflow_run_id}",
                event="avatar_session_started",
                properties={
                    "workflow_run_id": self.workflow_run_id,
                    "avatar_id": self.avatar_id,
                },
            )
            return True
        except Exception as e:
            logger.error(
                f"Avatar host session failed to start for run "
                f"{self.workflow_run_id}: {e}"
            )
            self._fail(str(e))
            return False

    async def send_audio(self, pcm: bytes, end: bool = False) -> None:
        if self.failed or not self.started or self._closing:
            return
        try:
            if self._first_audio_sent_at is None and pcm:
                self._first_audio_sent_at = time.monotonic()
            await self._session.send_audio(pcm, end=end)
        except Exception as e:
            logger.error(
                f"Avatar send_audio failed for run {self.workflow_run_id}: {e}"
            )
            self._fail(str(e))

    async def interrupt(self) -> None:
        """User barge-in — stop current round server-side and tell the client."""
        if self.failed or not self.started or self._closing:
            return
        try:
            await self._session.interrupt()
            self._enqueue_control({"type": "avatar-interrupted"})
        except Exception as e:
            # Interruption is best-effort; a failure here shouldn't kill the session.
            logger.warning(
                f"Avatar interrupt failed for run {self.workflow_run_id}: {e}"
            )

    async def close(self) -> None:
        self._closing = True
        if self._session is not None:
            try:
                await self._session.close()
            except Exception as e:
                logger.warning(
                    f"Avatar session close failed for run {self.workflow_run_id}: {e}"
                )
        if self._started_at is not None:
            summary = {
                "mode": "host",
                "avatar_id": self.avatar_id,
                "duration_seconds": int(time.monotonic() - self._started_at),
                "first_frame_latency_ms": self._first_frame_latency_ms,
                "failed": self.failed,
            }
            capture_event(
                distinct_id=f"workflow_run:{self.workflow_run_id}",
                event="avatar_session_closed",
                properties={"workflow_run_id": self.workflow_run_id, **summary},
            )
            # annotations shallow-merge on the run row, so touching only the
            # "avatar" key can't clobber QA/integration writers.
            try:
                from api.db import db_client

                await db_client.update_workflow_run(
                    self.workflow_run_id, annotations={"avatar": summary}
                )
            except Exception as e:
                logger.warning(
                    f"Failed to persist avatar summary for run "
                    f"{self.workflow_run_id}: {e}"
                )
        self._session = None
        self.started = False

    # ── SDK callbacks (sync, invoked from the SDK's receive loop) ──

    def _on_encoded_audio(self, req_id: str, encoded_audio: bytes) -> None:
        # Signature per avatarkit's _notify_encoded_audio(req_id, bytes) — the
        # round's end is signaled via transport_frames' is_last, not here.
        self._enqueue_binary(MSG_TYPE_AUDIO, encoded_audio, False)

    def _on_frames(self, data: bytes, is_last: bool) -> None:
        if (
            self._first_frame_latency_ms is None
            and self._first_audio_sent_at is not None
        ):
            self._first_frame_latency_ms = int(
                (time.monotonic() - self._first_audio_sent_at) * 1000
            )
        self._enqueue_binary(MSG_TYPE_FRAMES, data, is_last)

    def _on_error(self, err: Exception) -> None:
        logger.error(f"Avatar session error for run {self.workflow_run_id}: {err}")
        self._fail(str(err))

    def _on_close(self) -> None:
        if not self._closing:
            logger.warning(
                f"Avatar session closed unexpectedly for run {self.workflow_run_id}"
            )
            self._fail("session closed")

    # ── queue helpers ──

    def _enqueue_binary(self, msg_type: int, payload: bytes, is_last: bool) -> None:
        flags = FLAG_LAST if is_last else 0
        self._enqueue(bytes((msg_type, flags)) + payload)

    def _enqueue_control(self, message: dict) -> None:
        self._enqueue(message)

    def _enqueue(self, item) -> None:
        try:
            self.relay_queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self.relay_queue.get_nowait()
                self.relay_queue.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def _fail(self, detail: str) -> None:
        first_failure = not self.failed
        self.failed = True
        self._enqueue_control({"type": "avatar-error", "detail": detail})
        if first_failure:
            capture_event(
                distinct_id=f"workflow_run:{self.workflow_run_id}",
                event="avatar_session_failed",
                properties={
                    "workflow_run_id": self.workflow_run_id,
                    "avatar_id": self.avatar_id,
                    "detail": detail[:200],
                },
            )


_sessions: Dict[int, AvatarRunSession] = {}


def get_avatar_session(workflow_run_id: int) -> Optional[AvatarRunSession]:
    return _sessions.get(workflow_run_id)


async def get_or_create_avatar_session(
    workflow_run_id: int,
    avatar_id: str | None = None,
) -> Optional[AvatarRunSession]:
    """Create (and start) the run's avatar session. None when disabled/failed."""
    existing = _sessions.get(workflow_run_id)
    if existing is not None:
        return existing
    active = sum(1 for s in _sessions.values() if s.started and not s.failed)
    if active >= SPATIALREAL_MAX_SESSIONS:
        logger.warning(
            f"Avatar session cap reached ({SPATIALREAL_MAX_SESSIONS}); "
            f"run {workflow_run_id} proceeds audio-only"
        )
        return None
    session = AvatarRunSession(workflow_run_id, avatar_id=avatar_id)
    _sessions[workflow_run_id] = session
    if not await session.start():
        return session  # kept so the relay WS can deliver the error message
    return session


async def close_avatar_session(workflow_run_id: int) -> None:
    session = _sessions.pop(workflow_run_id, None)
    if session is not None:
        await session.close()
