import copy
import re
import uuid
from dataclasses import dataclass
from typing import Optional

TRIGGER_PATH_MAX_LENGTH = 36
TRIGGER_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class TriggerPathIssue:
    node_id: str | None
    trigger_path: str
    message: str


def extract_trigger_paths(workflow_definition: Optional[dict]) -> list[str]:
    """Extract trigger paths from a workflow definition."""
    if not workflow_definition:
        return []

    trigger_paths: list[str] = []
    for node in workflow_definition.get("nodes") or []:
        if node.get("type") != "trigger":
            continue
        trigger_path = (node.get("data") or {}).get("trigger_path")
        if isinstance(trigger_path, str) and trigger_path:
            trigger_paths.append(trigger_path)
    return trigger_paths


def trigger_path_to_node_id(workflow_definition: Optional[dict]) -> dict[str, str]:
    """Map each trigger node's trigger_path to its node id."""
    if not workflow_definition:
        return {}

    out: dict[str, str] = {}
    for node in workflow_definition.get("nodes") or []:
        if node.get("type") != "trigger":
            continue
        trigger_path = (node.get("data") or {}).get("trigger_path")
        if isinstance(trigger_path, str) and trigger_path:
            out[trigger_path] = node.get("id")
    return out


def regenerate_trigger_uuids(workflow_definition: Optional[dict]) -> Optional[dict]:
    """Regenerate UUIDs for all trigger nodes in a workflow definition."""
    if not workflow_definition:
        return workflow_definition

    updated_definition = copy.deepcopy(workflow_definition)
    for node in updated_definition.get("nodes") or []:
        if node.get("type") != "trigger":
            continue
        data = node.setdefault("data", {})
        data["trigger_path"] = str(uuid.uuid4())
    return updated_definition


def ensure_trigger_paths(workflow_definition: Optional[dict]) -> Optional[dict]:
    """Mint UUIDs for trigger nodes that do not already have a path."""
    if not workflow_definition:
        return workflow_definition

    out = copy.deepcopy(workflow_definition)
    for node in out.get("nodes") or []:
        if node.get("type") != "trigger":
            continue
        data = node.setdefault("data", {})
        if not data.get("trigger_path"):
            data["trigger_path"] = str(uuid.uuid4())
    return out


def validate_trigger_paths(
    workflow_definition: Optional[dict],
) -> list[TriggerPathIssue]:
    """Validate custom trigger paths before they reach persistence/runtime."""
    if not workflow_definition:
        return []

    issues: list[TriggerPathIssue] = []
    seen_paths: dict[str, str | None] = {}

    for node in workflow_definition.get("nodes") or []:
        if node.get("type") != "trigger":
            continue

        node_id = node.get("id")
        trigger_path = (node.get("data") or {}).get("trigger_path")
        if not trigger_path:
            continue

        if not isinstance(trigger_path, str):
            issues.append(
                TriggerPathIssue(
                    node_id=node_id,
                    trigger_path=repr(trigger_path),
                    message="Trigger path must be a string.",
                )
            )
            continue

        if len(trigger_path) > TRIGGER_PATH_MAX_LENGTH:
            issues.append(
                TriggerPathIssue(
                    node_id=node_id,
                    trigger_path=trigger_path,
                    message=(
                        f"Trigger path must be {TRIGGER_PATH_MAX_LENGTH} "
                        "characters or fewer."
                    ),
                )
            )

        if not TRIGGER_PATH_PATTERN.fullmatch(trigger_path):
            issues.append(
                TriggerPathIssue(
                    node_id=node_id,
                    trigger_path=trigger_path,
                    message=(
                        "Trigger path must be a single URL path segment using "
                        "only letters, numbers, hyphens, and underscores."
                    ),
                )
            )

        if trigger_path not in seen_paths:
            seen_paths[trigger_path] = node_id
        else:
            issues.append(
                TriggerPathIssue(
                    node_id=node_id,
                    trigger_path=trigger_path,
                    message="Trigger path is duplicated in this workflow.",
                )
            )

    return issues
