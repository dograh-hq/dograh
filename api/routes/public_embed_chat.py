"""Public text-chat endpoints for the embed widget.

Anonymous counterpart of api/routes/workflow_text_chat.py: gated by an embed
session token instead of user auth, and responding with the lean public
projection instead of the full session state. Error details stay generic —
this surface is reachable from any third-party page.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from api.routes.public_embed import (
    _allow_embed_origin,
    _session_preflight_response,
    get_request_origin,
)
from api.schemas.embed_chat import (
    PublicEmbedChatMessageRequest,
    PublicEmbedChatSessionResponse,
)
from api.services.workflow.embed_session_service import (
    EmbedSessionNotFoundError,
    EmbedSessionValidationError,
    EmbedTokenNotFoundError,
)
from api.services.workflow.embed_text_chat_service import (
    EmbedChatCompletedError,
    EmbedChatModeError,
    EmbedChatQuotaExceededError,
    EmbedChatRateLimitExceededError,
    EmbedChatSessionNotFoundError,
    EmbedChatTurnLimitExceededError,
    build_public_chat_session_response,
    load_embed_text_chat_session,
    process_embed_text_chat_message,
)
from api.services.workflow.text_chat_session_service import (
    TextChatPendingTurnLostError,
    TextChatSessionExecutionError,
    TextChatSessionRevisionConflictError,
)

router = APIRouter(prefix="/public/embed/chat")


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
    origin = get_request_origin(request)
    try:
        _, text_session = await load_embed_text_chat_session(
            session_token=session_token, origin=origin
        )
    except (EmbedSessionNotFoundError, EmbedTokenNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except EmbedSessionValidationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except EmbedChatSessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except EmbedChatModeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if origin:
        _allow_embed_origin(response, origin)
    return build_public_chat_session_response(text_session)


@router.post("/{session_token}/messages", response_model=PublicEmbedChatSessionResponse)
async def post_public_chat_message(
    session_token: str,
    body: PublicEmbedChatMessageRequest,
    request: Request,
    response: Response,
) -> PublicEmbedChatSessionResponse:
    origin = get_request_origin(request)
    try:
        text_session = await process_embed_text_chat_message(
            session_token=session_token,
            origin=origin,
            text=body.text,
            expected_revision=body.expected_revision,
        )
    except (EmbedSessionNotFoundError, EmbedTokenNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except EmbedSessionValidationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except EmbedChatSessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except EmbedChatModeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except EmbedChatCompletedError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "chat_completed", "message": str(e)},
        ) from e
    except EmbedChatRateLimitExceededError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except EmbedChatQuotaExceededError as e:
        raise HTTPException(status_code=402, detail=str(e)) from e
    except EmbedChatTurnLimitExceededError as e:
        raise HTTPException(
            status_code=429, detail="Message limit reached for this conversation"
        ) from e
    except TextChatSessionRevisionConflictError as e:
        raise HTTPException(status_code=409, detail=_revision_conflict_detail(e)) from e
    except (TextChatPendingTurnLostError, TextChatSessionExecutionError) as e:
        raise HTTPException(
            status_code=500, detail="Assistant failed to respond"
        ) from e

    if origin:
        _allow_embed_origin(response, origin)
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
