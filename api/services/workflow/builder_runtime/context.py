"""Bound model context while keeping source, history and tool results retrievable."""

import json

from langchain_core.messages import HumanMessage

TRANSCRIPT_CHARACTERS = 12_000
MODEL_HISTORY_CHARACTERS = 48_000
INLINE_RESULT_CHARACTERS = 8_000


def recent_conversation(messages: list[dict]) -> list[dict]:
    recent = []
    size = 0
    for message in reversed(messages):
        entry = {"role": message["role"], "content": message["content"]}
        length = len(entry["content"])
        if size + length > TRANSCRIPT_CHARACTERS:
            if not recent:
                recent.append(
                    {**entry, "content": entry["content"][-TRANSCRIPT_CHARACTERS:]}
                )
            break
        recent.append(entry)
        size += length
    return list(reversed(recent))


def bounded_messages(messages: list, *, budget: int = MODEL_HISTORY_CHARACTERS) -> list:
    """Evict complete tool exchanges, never orphaning a tool result or call."""
    if not messages:
        return messages
    groups = []
    for message in messages[1:]:
        if message.type == "tool" and groups:
            groups[-1].append(message)
        else:
            groups.append([message])
    kept = []
    size = len(str(messages[0].content))
    for group in reversed(groups):
        length = sum(len(json.dumps(m.model_dump(), default=str)) for m in group)
        if kept and size + length > budget:
            break
        kept.append(group)
        size += length
    tail = [m for group in reversed(kept) for m in group]
    if len(tail) == len(messages) - 1:
        return messages
    return [
        messages[0],
        HumanMessage(
            content=(
                "Earlier tool exchanges were removed to keep context bounded. The working copy and "
                "stored tool results remain authoritative. Use read_conversation for earlier user input, "
                "or the source/result retrieval tools available in this stage; do not repeat completed mutations."
            )
        ),
        *tail,
    ]


def merge_tool_results(current: dict, new: dict) -> dict:
    # Stable IDs make concurrent independent reads commutative. Results live in
    # checkpoints, not the prompt; retrieve only the required page.
    return {**current, **new}


def capture_tool_results(result, calls: list[dict], *, request_id: str = ""):
    """Keep full results durable and replace oversized model-facing payloads."""
    from langgraph.types import Command

    if isinstance(result, list):
        return [
            capture_tool_results(item, calls, request_id=request_id) for item in result
        ]
    update = result.update if isinstance(result, Command) else result
    if not isinstance(update, dict):
        return result
    for message in update.get("build_messages", []):
        if message.type != "tool":
            continue
        call = next((c for c in calls if c["id"] == message.tool_call_id), None)
        if not call or call["name"] in {"read_tool_result", "read_conversation"}:
            continue
        content = (
            message.content
            if isinstance(message.content, str)
            else json.dumps(message.content)
        )
        result_id = (
            f"{request_id}:{message.tool_call_id}"
            if request_id
            else message.tool_call_id
        )
        update.setdefault("tool_results", {})[result_id] = {
            "tool": call["name"],
            "arguments": call["args"],
            "content": content,
        }
        if (
            call["name"] in {"patch_workflow", "edit_workflow"}
            and message.status != "error"
        ):
            # Retain the full diff in the checkpoint, not in every model request.
            payload = json.loads(content)
            payload.pop("diff", None)
            content = json.dumps(payload)
            message.content = content
        if len(content) > INLINE_RESULT_CHARACTERS:
            message.content = json.dumps(
                {
                    "result_id": result_id,
                    "characters": len(content),
                    "truncated": True,
                    "preview": content[:INLINE_RESULT_CHARACTERS],
                    "next_offset": INLINE_RESULT_CHARACTERS,
                    "instruction": "Use read_tool_result for the remaining content.",
                }
            )
    return result
