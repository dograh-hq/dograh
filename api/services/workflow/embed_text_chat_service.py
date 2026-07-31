"""Embed-widget orchestration over the text-chat session service.

The embed chat endpoints are public (token-gated, anonymous visitors), so this
module adds the two things the authenticated flow doesn't need: a per-session
turn cap and an allowlist projection of the session state.
"""

from typing import Any

from api.db import db_client
from api.db.models import WorkflowRunTextSessionModel
from api.schemas.embed_chat import (
    PublicEmbedChatMessage,
    PublicEmbedChatSessionResponse,
    PublicEmbedChatTurn,
)
from api.services.workflow.text_chat_session_service import (
    append_text_chat_user_message,
    default_text_chat_checkpoint,
    default_text_chat_session_data,
    execute_pending_text_chat_turn,
    initialize_text_chat_session,
    normalize_text_chat_session_data,
)

EMBED_CHAT_MAX_TURNS = 50


class EmbedChatTurnLimitExceededError(Exception):
    """Raised when an embed chat session reaches EMBED_CHAT_MAX_TURNS."""


async def start_embed_text_chat(
    *, workflow_id: int, run_id: int
) -> WorkflowRunTextSessionModel:
    """Seed the text session for an embed run and execute the greeting turn."""
    text_session = await db_client.ensure_workflow_run_text_session(
        run_id,
        session_data=default_text_chat_session_data(),
        checkpoint=default_text_chat_checkpoint(),
    )
    text_session = await initialize_text_chat_session(
        run_id=run_id, text_session=text_session
    )
    return await execute_pending_text_chat_turn(
        workflow_id=workflow_id, run_id=run_id, text_session=text_session
    )


async def append_embed_text_chat_message(
    *,
    workflow_id: int,
    run_id: int,
    text_session: WorkflowRunTextSessionModel,
    text: str,
    expected_revision: int | None,
) -> WorkflowRunTextSessionModel:
    turns = normalize_text_chat_session_data(text_session.session_data)["turns"]
    if len(turns) >= EMBED_CHAT_MAX_TURNS:
        raise EmbedChatTurnLimitExceededError(
            f"Embed chat session reached the {EMBED_CHAT_MAX_TURNS}-turn limit"
        )

    text_session = await append_text_chat_user_message(
        run_id=run_id,
        text_session=text_session,
        user_text=text,
        expected_revision=expected_revision,
    )
    return await execute_pending_text_chat_turn(
        workflow_id=workflow_id, run_id=run_id, text_session=text_session
    )


def build_public_chat_session_response(
    text_session: WorkflowRunTextSessionModel,
) -> PublicEmbedChatSessionResponse:
    """Project the session state for anonymous embed visitors.

    Built strictly by allowlist: ``checkpoint`` carries the serialized LLM
    context (system prompt included), ``events`` carries raw exception text on
    failed turns, and ``usage``/``gathered_context``/``initial_context`` are
    operator-facing. None of those may ever appear in a public response.
    """
    workflow_run = text_session.workflow_run
    session_data = normalize_text_chat_session_data(text_session.session_data)
    state = workflow_run.state
    return PublicEmbedChatSessionResponse(
        revision=text_session.revision,
        state=state.value if hasattr(state, "value") else str(state),
        is_completed=workflow_run.is_completed,
        turns=[
            PublicEmbedChatTurn(
                id=turn.get("id", ""),
                status=turn.get("status", ""),
                user_message=_public_message(turn.get("user_message")),
                assistant_message=_public_message(turn.get("assistant_message")),
            )
            for turn in session_data["turns"]
        ],
    )


def _public_message(message: dict[str, Any] | None) -> PublicEmbedChatMessage | None:
    if not message or message.get("text") is None:
        return None
    return PublicEmbedChatMessage(
        text=message["text"], created_at=message.get("created_at")
    )
