from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class WorkflowDraft(BaseModel):
    name: str = Field(..., description="Name of the voice agent")
    call_type: Literal["inbound", "outbound"] = Field(
        default="inbound", description="Call type: inbound or outbound"
    )
    language: str = Field(default="en", description="Language code (e.g. en, hi, es)")
    first_message: str = Field(
        ..., description="Initial greeting spoken by the agent when call connects"
    )
    system_prompt: str = Field(
        ..., description="Comprehensive system prompt defining persona, goals, rules"
    )
    questions_to_ask: List[str] = Field(
        default_factory=list,
        description="Key information points or questions the agent must gather",
    )
    workflow_definition: Dict[str, Any] = Field(
        default_factory=dict,
        description="ReactFlow graph definition containing nodes and edges for Dograh canvas",
    )


class AgentCopilotResponse(BaseModel):
    reply_message: str = Field(
        ..., description="Conversational reply / follow-up question for the user"
    )
    suggested_quick_replies: List[str] = Field(
        default_factory=list,
        description="2-4 short suggested reply pills the user can click to answer quickly",
    )
    is_ready_to_test: bool = Field(
        default=False,
        description="True when enough details (name, first_message, prompt) have been gathered to run a test call",
    )
    workflow_draft: WorkflowDraft = Field(
        ..., description="The currently constructed/refined agent workflow draft"
    )
    workflow_id: Optional[int] = Field(
        default=None,
        description="Database workflow ID if the draft has been saved to DB",
    )


class CopilotChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(
        ..., description="Full conversation history between user and copilot"
    )
    current_workflow_id: Optional[int] = Field(
        default=None,
        description="Existing workflow ID if this conversation is updating an already created agent",
    )
    current_workflow_draft: Optional[WorkflowDraft] = Field(
        default=None,
        description="Previous workflow draft to refine in this turn",
    )
    save_draft: bool = Field(
        default=False,
        description="If True, auto-persists or updates the draft workflow in the database",
    )


class InCanvasCopilotRequest(BaseModel):
    messages: List[ChatMessage] = Field(
        ..., description="Recent conversation messages between user and in-canvas copilot"
    )
    current_nodes: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Optional live nodes from the ReactFlow canvas"
    )
    current_edges: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Optional live edges from the ReactFlow canvas"
    )


class InCanvasCopilotResponse(BaseModel):
    assistant_message: str = Field(
        ..., description="Conversational explanation of changes performed"
    )
    workflow_definition: Dict[str, Any] = Field(
        ..., description="Updated ReactFlow workflow graph (nodes and edges)"
    )
    modified_node_ids: List[str] = Field(
        default_factory=list, description="IDs of nodes modified or added during this turn"
    )
    suggested_quick_replies: List[str] = Field(
        default_factory=list, description="2-4 contextual action chips"
    )
    trigger_test_call: bool = Field(
        default=False, description="True if test call should be launched immediately"
    )

