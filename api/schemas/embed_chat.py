"""Public projections of text-chat sessions for the embed widget.

The authenticated text-chat responses expose the full session state, including
the serialized LLM context (system prompt) in ``checkpoint`` and raw error text
in turn ``events``. The embed widget serves anonymous third-party visitors, so
its responses are built from this allowlist instead.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class PublicEmbedChatMessage(BaseModel):
    text: str
    created_at: str | None = None


class PublicEmbedChatTurn(BaseModel):
    id: str
    status: str
    user_message: PublicEmbedChatMessage | None = None
    assistant_message: PublicEmbedChatMessage | None = None
    # None identifies legacy turns with only a combined assistant_message.
    assistant_messages: list[PublicEmbedChatMessage] | None = None


class PublicEmbedChatSessionResponse(BaseModel):
    revision: int
    state: str
    is_completed: bool
    turns: list[PublicEmbedChatTurn]


class PublicEmbedChatMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    expected_revision: int | None = None


class PublicEmbedChatEndRequest(BaseModel):
    expected_revision: int | None = None


class PublicEmbedChatSessionStreamEvent(BaseModel):
    type: Literal["session", "complete"]
    session: PublicEmbedChatSessionResponse
    # Present on initialization so an interrupted greeting can recover via GET.
    session_token: str | None = None
    workflow_run_id: int | None = None


class PublicEmbedChatMessageStreamEvent(BaseModel):
    type: Literal["message"] = "message"
    turn_id: str
    index: int
    message: PublicEmbedChatMessage


class PublicEmbedChatStreamError(BaseModel):
    type: Literal["error"] = "error"
    message: str = "Assistant failed to respond"


PublicEmbedChatStreamEvent = Annotated[
    PublicEmbedChatSessionStreamEvent
    | PublicEmbedChatMessageStreamEvent
    | PublicEmbedChatStreamError,
    Field(discriminator="type"),
]
