"""Public text-chat endpoints for the embed widget.

Anonymous counterpart of api/routes/workflow_text_chat.py: gated by an embed
session token instead of user auth, and responding with the lean public
projection instead of the full session state. Error details stay generic —
this surface is reachable from any third-party page.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.db.models import EmbedTokenModel, WorkflowRunTextSessionModel
from api.enums import WorkflowRunMode
from api.routes.public_embed import (
    _allow_embed_origin,
    _session_preflight_response,
    get_request_origin,
    resolve_embed_session,
)
from api.schemas.embed_chat import (
    PublicEmbedChatMessageRequest,
    PublicEmbedChatSessionResponse,
)
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow.embed_chat_limiter import allow_embed_chat_message
from api.services.workflow.embed_text_chat_service import (
    EmbedChatTurnLimitExceededError,
    append_embed_text_chat_message,
    build_public_chat_session_response,
)
from api.services.workflow.text_chat_session_service import (
    TextChatPendingTurnLostError,
    TextChatSessionExecutionError,
    TextChatSessionRevisionConflictError,
)

router = APIRouter(prefix="/public/embed/chat")


async def _load_chat_session(
    session_token: str, request: Request, response: Response
) -> tuple[EmbedTokenModel, WorkflowRunTextSessionModel]:
    origin = get_request_origin(request)
    embed_session, embed_token = await resolve_embed_session(session_token, origin)
    if origin:
        _allow_embed_origin(response, origin)

    run_id = embed_session.workflow_run_id
    if run_id is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    set_current_run_id(run_id)

    text_session = await db_client.get_workflow_run_text_session(
        run_id, organization_id=embed_token.organization_id
    )
    if not text_session or not text_session.workflow_run:
        raise HTTPException(status_code=404, detail="Chat session not found")
    if text_session.workflow_run.workflow_id != embed_token.workflow_id:
        raise HTTPException(status_code=404, detail="Chat session not found")
    if text_session.workflow_run.mode != WorkflowRunMode.TEXTCHAT.value:
        raise HTTPException(status_code=400, detail="Not a chat session")
    return embed_token, text_session


def _revision_conflict_detail(
    e: TextChatSessionRevisionConflictError,
) -> dict[str, Any]:
    return {
        "message": "Text chat session revision conflict",
        "expected_revision": e.expected_revision,
        "actual_revision": e.actual_revision,
    }


@router.get("/{session_token}", response_model=PublicEmbedChatSessionResponse)
async def get_public_chat_session(
    session_token: str, request: Request, response: Response
) -> PublicEmbedChatSessionResponse:
    """Current transcript for an embed chat session (used for 409 resync)."""
    _, text_session = await _load_chat_session(session_token, request, response)
    return build_public_chat_session_response(text_session)


@router.post("/{session_token}/messages", response_model=PublicEmbedChatSessionResponse)
async def post_public_chat_message(
    session_token: str,
    body: PublicEmbedChatMessageRequest,
    request: Request,
    response: Response,
) -> PublicEmbedChatSessionResponse:
    embed_token, text_session = await _load_chat_session(
        session_token, request, response
    )
    workflow_run = text_session.workflow_run
    if workflow_run.is_completed:
        raise HTTPException(
            status_code=400,
            detail={"code": "chat_completed", "message": "Conversation has ended"},
        )

    if not await allow_embed_chat_message(workflow_run.id):
        raise HTTPException(
            status_code=429, detail="Too many messages. Please try again shortly"
        )

    quota_result = await authorize_workflow_run_start(
        workflow_id=embed_token.workflow_id,
        organization_id=embed_token.organization_id,
        workflow_run_id=workflow_run.id,
        actor_user=await db_client.get_user_by_id(embed_token.created_by),
    )
    if not quota_result.has_quota:
        raise HTTPException(
            status_code=402, detail="The agent is unavailable right now"
        )

    try:
        text_session = await append_embed_text_chat_message(
            workflow_id=embed_token.workflow_id,
            run_id=workflow_run.id,
            text_session=text_session,
            text=body.text,
            expected_revision=body.expected_revision,
        )
    except EmbedChatTurnLimitExceededError:
        raise HTTPException(
            status_code=429, detail="Message limit reached for this conversation"
        )
    except TextChatSessionRevisionConflictError as e:
        raise HTTPException(status_code=409, detail=_revision_conflict_detail(e))
    except (TextChatPendingTurnLostError, TextChatSessionExecutionError) as e:
        logger.error(f"Embed chat turn failed for run {workflow_run.id}: {e}")
        raise HTTPException(status_code=500, detail="Assistant failed to respond")

    return build_public_chat_session_response(text_session)


@router.options("/{session_token}")
async def options_public_chat_session(request: Request, session_token: str):
    """Fallback OPTIONS handler; browser preflights hit PublicEmbedCORSMiddleware."""
    return await _session_preflight_response(
        session_token, request.headers.get("origin", ""), "GET, POST, OPTIONS"
    )


@router.options("/{session_token}/messages")
async def options_public_chat_messages(request: Request, session_token: str):
    """Fallback OPTIONS handler; browser preflights hit PublicEmbedCORSMiddleware."""
    return await _session_preflight_response(
        session_token, request.headers.get("origin", ""), "GET, POST, OPTIONS"
    )
