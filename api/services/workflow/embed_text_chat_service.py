"""Public embed-widget orchestration over the text-chat session service.

The embed chat endpoints are token-gated but anonymous, so this module owns
their authenticated session loading, per-session limits, quota authorization,
turn execution, and allowlist projection.
"""

from typing import Any

from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.db.models import EmbedTokenModel, WorkflowRunTextSessionModel
from api.enums import WorkflowRunMode
from api.schemas.embed_chat import (
    PublicEmbedChatMessage,
    PublicEmbedChatSessionResponse,
    PublicEmbedChatTurn,
)
from api.services.workflow.embed_chat_limiter import allow_embed_chat_message
from api.services.workflow.embed_session_service import (
    authorize_embed_workflow_run_start,
    resolve_embed_session,
)
from api.services.workflow.text_chat_session_service import (
    TextChatPendingTurnLostError,
    TextChatSessionExecutionError,
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


class EmbedChatSessionNotFoundError(Exception):
    """Raised when an embed session does not own a usable text-chat session."""


class EmbedChatModeError(Exception):
    """Raised when an embed session points at a non-text-chat workflow run."""


class EmbedChatCompletedError(Exception):
    """Raised when a visitor tries to append to a completed chat."""


class EmbedChatRateLimitExceededError(Exception):
    """Raised when an embed chat exceeds its per-session message rate."""


class EmbedChatQuotaExceededError(Exception):
    """Raised when an embed chat is not authorized to perform billable work."""


async def load_embed_text_chat_session(
    *, session_token: str, origin: str
) -> tuple[EmbedTokenModel, WorkflowRunTextSessionModel]:
    """Authenticate and load the org-scoped text session for a public token."""
    embed_session, embed_token = await resolve_embed_session(session_token, origin)
    run_id = embed_session.workflow_run_id
    if run_id is None:
        raise EmbedChatSessionNotFoundError("Chat session not found")

    set_current_run_id(run_id)
    text_session = await db_client.get_workflow_run_text_session(
        run_id, organization_id=embed_token.organization_id
    )
    if not text_session or not text_session.workflow_run:
        raise EmbedChatSessionNotFoundError("Chat session not found")
    if text_session.workflow_run.workflow_id != embed_token.workflow_id:
        raise EmbedChatSessionNotFoundError("Chat session not found")
    if text_session.workflow_run.mode != WorkflowRunMode.TEXTCHAT.value:
        raise EmbedChatModeError("Not a chat session")

    return embed_token, text_session


async def process_embed_text_chat_message(
    *,
    session_token: str,
    origin: str,
    text: str,
    expected_revision: int | None,
) -> WorkflowRunTextSessionModel:
    """Authorize and execute one visitor message for an embed chat session."""
    embed_token, text_session = await load_embed_text_chat_session(
        session_token=session_token, origin=origin
    )
    workflow_run = text_session.workflow_run

    if workflow_run.is_completed:
        raise EmbedChatCompletedError("Conversation has ended")

    if not await allow_embed_chat_message(workflow_run.id):
        raise EmbedChatRateLimitExceededError(
            "Too many messages. Please try again shortly"
        )

    quota_result = await authorize_embed_workflow_run_start(
        embed_token=embed_token, workflow_run_id=workflow_run.id
    )
    if not quota_result.has_quota:
        raise EmbedChatQuotaExceededError("The agent is unavailable right now")

    try:
        return await append_embed_text_chat_message(
            workflow_id=embed_token.workflow_id,
            run_id=workflow_run.id,
            text_session=text_session,
            text=text,
            expected_revision=expected_revision,
        )
    except (TextChatPendingTurnLostError, TextChatSessionExecutionError) as e:
        logger.error(f"Embed chat turn failed for run {workflow_run.id}: {e}")
        raise


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
