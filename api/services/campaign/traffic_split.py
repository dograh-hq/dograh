"""Campaign allocation and validation. Assignments are made only at dispatch."""

import hashlib
import secrets

from api.schemas.campaign import (
    CampaignTrafficStatsResponse,
    TrafficDefinitionStats,
    TrafficSplitRequest,
    TrafficSplitResponse,
    TrafficVariantResponse,
    TrafficVariantStats,
)
from api.services.campaign.source_sync import CampaignSourceSyncService
from api.services.workflow.run_creation import definition_to_run


def variant_id(workflow_id: int, definition_id: int | None) -> str:
    return f"{workflow_id}:{definition_id if definition_id is not None else 'latest'}"


def build_split(request: TrafficSplitRequest, previous: dict | None = None) -> dict:
    """Call under the campaign row lock when updating an existing split.

    Retain surviving variants' order; append new pairs in request order. A UI
    reorder alone must not change allocation. Cumulative weight boundaries can
    still move unchanged-weight variants when other weights are edited.
    """
    variants = {
        variant_id(v.workflow_id, v.workflow_definition_id): {
            **v.model_dump(),
            "id": variant_id(v.workflow_id, v.workflow_definition_id),
        }
        for v in request.variants
    }
    previous = previous or {}
    ordered = []
    for old in previous.get("variants", []):
        if old["id"] in variants:
            ordered.append(variants.pop(old["id"]))
    ordered.extend(variants.values())
    changed = ordered != previous.get("variants")
    return {
        "seed": previous.get("seed") or secrets.token_hex(16),
        "revision": previous.get("revision", 0) + int(changed),
        "variants": ordered,
    }


def campaign_split(campaign) -> dict:
    split = (campaign.orchestrator_metadata or {}).get("traffic_split")
    if split:
        return split
    return {
        "seed": "",
        "revision": 0,
        "variants": [
            {
                "id": variant_id(campaign.workflow_id, None),
                "workflow_id": campaign.workflow_id,
                "workflow_definition_id": None,
                "weight": 100,
            }
        ],
    }


def primary_workflow_id(split: dict) -> int:
    return max(split["variants"], key=lambda v: v["weight"])["workflow_id"]


def pick_variant(split: dict, phone_number: str) -> dict:
    digest = hashlib.sha256(f"{split['seed']}:{phone_number.strip()}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % 10_000
    edge = 0
    for variant in split["variants"]:
        edge += variant["weight"] * 100
        if bucket < edge:
            return variant
    raise ValueError("Invalid campaign traffic split")


async def resolve_variants(db, variants: list[dict], organization_id: int) -> list:
    resolved = []
    for variant in variants:
        workflow = await db.get_workflow(
            variant["workflow_id"], organization_id=organization_id
        )
        if workflow is None:
            raise ValueError("Traffic split agent not found")
        definition = await definition_to_run(
            db, workflow, definition_id=variant.get("workflow_definition_id")
        )
        if definition is None or definition.status not in {"published", "archived"}:
            raise ValueError("Traffic split versions must be published or archived")
        resolved.append((workflow, definition))
    return resolved


def validate_variant_templates(resolved: list, source) -> None:
    from api.services.workflow.dto import ReactFlowDTO
    from api.services.workflow.workflow_graph import WorkflowGraph

    required = set()
    for _, definition in resolved:
        graph = WorkflowGraph(
            ReactFlowDTO(**definition.workflow_json),
            skip_instance_constraints_for={"trigger"},
        )
        required.update(graph.get_required_template_variables())
    if required:
        result = CampaignSourceSyncService.validate_template_columns(
            source.headers or [], source.rows or [], required
        )
        if not result.is_valid:
            raise ValueError(result.error.message)


async def split_response(db, campaign, workflow_name: str, labels=None):
    split = campaign_split(campaign)
    if labels is None:
        labels = (
            await db.get_campaign_variant_labels(
                [v["workflow_id"] for v in split["variants"]], campaign.organization_id
            )
            if split["revision"]
            else {campaign.workflow_id: {"name": workflow_name, "versions": {}}}
        )
    return TrafficSplitResponse(
        revision=split["revision"],
        variants=[
            TrafficVariantResponse(
                **v,
                workflow_name=labels.get(v["workflow_id"], {}).get(
                    "name", "Unknown agent"
                ),
                version_number=labels.get(v["workflow_id"], {})
                .get("versions", {})
                .get(v["workflow_definition_id"]),
            )
            for v in split["variants"]
        ],
    )


async def traffic_stats(db, campaign) -> CampaignTrafficStatsResponse:
    rows = await db.get_campaign_traffic_counts(campaign.id, campaign.organization_id)
    current = {v["id"]: v for v in campaign_split(campaign)["variants"]}
    workflow_ids = {v["workflow_id"] for v in current.values()} | {
        r["workflow_id"] for r in rows
    }
    labels = await db.get_campaign_variant_labels(
        list(workflow_ids), campaign.organization_id
    )
    variants = {}

    def add(key, workflow_id, pinned_id):
        label = labels.get(workflow_id, {})
        variants[key] = TrafficVariantStats(
            id=key,
            workflow_id=workflow_id,
            workflow_name=label.get("name", "Unknown agent"),
            workflow_definition_id=pinned_id,
            version_number=label.get("versions", {}).get(pinned_id),
            target_weight=current.get(key, {}).get("weight"),
        )

    for key, v in current.items():
        add(key, v["workflow_id"], v["workflow_definition_id"])
    total = 0
    for row in rows:
        key = row["variant_id"] or variant_id(row["workflow_id"], None)
        if key not in variants:
            add(key, row["workflow_id"], row["pinned_id"])
        v = variants[key]
        count = row["attempts"]
        total += count
        v.attempts += count
        v.completed += row["completed"]
        v.states[row["state"]] = v.states.get(row["state"], 0) + count
        v.outcomes[row["outcome"]] = v.outcomes.get(row["outcome"], 0) + count
        definition = next(
            (d for d in v.definitions if d.definition_id == row["definition_id"]), None
        )
        if definition is None:
            definition = TrafficDefinitionStats(
                definition_id=row["definition_id"],
                version_number=labels.get(row["workflow_id"], {})
                .get("versions", {})
                .get(row["definition_id"]),
                attempts=0,
            )
            v.definitions.append(definition)
        definition.attempts += count
    for v in variants.values():
        v.actual_percentage = 100 * v.attempts / total if total else 0
    return CampaignTrafficStatsResponse(
        total_attempts=total, variants=list(variants.values())
    )
