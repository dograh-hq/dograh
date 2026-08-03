"""Public projections of text-chat sessions for the embed widget.

The authenticated text-chat responses expose the full session state, including
the serialized LLM context (system prompt) in ``checkpoint`` and raw error text
in turn ``events``. The embed widget serves anonymous third-party visitors, so
its responses are built from this allowlist instead.
"""

from pydantic import BaseModel, Field


class PublicEmbedChatMessage(BaseModel):
    text: str
    created_at: str | None = None


class PublicEmbedChatTurn(BaseModel):
    id: str
    status: str
    user_message: PublicEmbedChatMessage | None = None
    assistant_message: PublicEmbedChatMessage | None = None


class PublicEmbedChatSessionResponse(BaseModel):
    revision: int
    state: str
    is_completed: bool
    turns: list[PublicEmbedChatTurn]


class PublicEmbedChatMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    expected_revision: int | None = None
