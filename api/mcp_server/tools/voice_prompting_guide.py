"""MCP tool that surfaces voice-prompting guidance to the workflow-authoring LLM.

The guide is split into stages (plan / create / review) and atoms
(topics). Stage calls return a tight briefing — an intro plus a list of
relevant topics with one-line lenses. Topic calls return the full
reference content for one atom. No-arg calls return a flat index.

The LLM is expected to read the briefing for the current stage first,
then drill into specific topics only when complexity warrants it. The
authoritative guidance lives in `api.services.voice_prompting_guide`;
this tool is a thin MCP-facing projection.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.services.voice_prompting_guide import (
    Stage,
    build_briefing,
    get_topic,
    list_topic_index,
)


@traced_tool
async def get_voice_prompting_guide(
    stage: Optional[str] = None,
    topic: Optional[str] = None,
    node_type: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch staged voice-prompting guidance for authoring Dograh workflows.

    Call this BEFORE composing or revising any prompt field on a node. The
    guide is the authoritative source for prompt-authoring craft (turn-taking,
    persona, readback rules, disfluencies); product-mechanics questions
    (how a node type works at runtime) belong in `search_docs` / `read_doc`.

    Args:
        stage: "plan" | "create" | "review". Returns a stage briefing — a
            short intro plus the list of topics relevant at this stage,
            each with a one-line lens. Combine with `node_type` during the
            create stage to narrow to topics that apply to that node type's
            prompts (e.g. `node_type="agent"`).
        topic: A topic id from a prior briefing. Returns the full content
            for that atom. Use after the briefing flags a topic worth
            drilling into. Mutually exclusive with `stage`.
        node_type: Optional filter. Most useful with `stage="create"`.

    Returns:
        - With `topic`: { id, title, severity, content, stages_relevant,
          applies_to_node_types?, cross_refs? }.
        - With `stage`: { stage, intro, topics: [{id, title, lens}],
          drill_in, filtered_to_node_type? }.
        - With no args: { topics: [{id, title}], next }.

    Briefings are designed to be cheap — read the lens, decide what to
    drill into, then ask for full content for the 1–3 topics that matter
    for the prompt you're about to write. Do not pull every topic.
    """
    await authenticate_mcp_request()

    if topic is not None and stage is not None:
        raise ValueError(
            "Pass either `topic` or `stage`, not both. Use `stage` for a "
            "briefing index; use `topic` for full content of one atom."
        )

    if topic is not None:
        atom = get_topic(topic)
        if atom is None:
            available = ", ".join(t["id"] for t in list_topic_index())
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Unknown voice-prompting topic: {topic!r}. "
                    f"Available topics: {available or '(none registered)'}."
                ),
            )
        return atom.to_deep_dict()

    if stage is not None:
        try:
            stage_enum = Stage(stage)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown stage: {stage!r}. "
                    f"Use one of: {', '.join(s.value for s in Stage)}."
                ),
            )
        return build_briefing(stage_enum, node_type=node_type)

    return {
        "topics": list_topic_index(),
        "next": (
            "Call with stage='plan'|'create'|'review' for a briefing, or "
            "topic=<id> for the full content of one atom."
        ),
    }
