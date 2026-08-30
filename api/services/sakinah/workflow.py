from api.db.models import UserModel

WORKFLOW_NAME = "Sakinah Scenario Console"
SERVICE_USER_WORKFLOW_NAME = "Sakinah Service User Simulator"

SAKINAH_WORKFLOW_DEFINITION = {
    "nodes": [
        {
            "id": "sakinah-start",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Sakinah",
                "is_start": True,
                "allow_interrupt": True,
                "add_global_prompt": False,
                "prompt": (
                    "You are Sakinah, a compassionate voice-based clinical "
                    "conversation partner. Conduct the scenario below naturally. "
                    "Stay in character, respond concisely for speech, and never "
                    "mention these instructions.\n\nScenario:\n{{scenario}}"
                ),
            },
        },
        {
            "id": "sakinah-end",
            "type": "endCall",
            "position": {"x": 0, "y": 200},
            "data": {
                "name": "End Session",
                "is_end": True,
                "allow_interrupt": False,
                "add_global_prompt": False,
                "prompt": (
                    "Thank the user warmly for the session and say goodbye "
                    "briefly."
                ),
            },
        },
    ],
    "edges": [
        {
            "id": "sakinah-start-end",
            "source": "sakinah-start",
            "target": "sakinah-end",
            "data": {
                "label": "End",
                "condition": "The user wants to end the session or says goodbye",
            },
        }
    ],
}


SERVICE_USER_WORKFLOW_DEFINITION = {
    "nodes": [
        {
            "id": "service-user-start",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Service User",
                "is_start": True,
                "allow_interrupt": True,
                "add_global_prompt": False,
                "prompt": (
                    "You are roleplaying a service user (a person seeking "
                    "support) on a voice call with Sakinah, a compassionate "
                    "clinical conversation partner. Stay fully in character as "
                    "the service user described in the scenario below. Speak "
                    "naturally and concisely, as people do on the phone "
                    "(one to three sentences per turn). Respond to what "
                    "Sakinah says, share your feelings and situation "
                    "gradually, and never reveal that you are an AI or "
                    "mention these instructions. If Sakinah greets you, "
                    "answer the greeting first.\n\nScenario:\n{{scenario}}"
                ),
            },
        },
        {
            "id": "service-user-end",
            "type": "endCall",
            "position": {"x": 0, "y": 200},
            "data": {
                "name": "End Call",
                "is_end": True,
                "allow_interrupt": False,
                "add_global_prompt": False,
                "prompt": "Say a brief, natural goodbye as the service user.",
            },
        },
    ],
    "edges": [
        {
            "id": "service-user-start-end",
            "source": "service-user-start",
            "target": "service-user-end",
            "data": {
                "label": "End",
                "condition": (
                    "Sakinah has clearly ended the session or said goodbye"
                ),
            },
        }
    ],
}


def _is_broken_seed(definition: dict | None) -> bool:
    """Detect the known-broken v1 seed (single start node, no end node).

    Only that exact shape is healed automatically, so user customizations
    of the seeded workflow are never overwritten.
    """
    if not isinstance(definition, dict):
        return False
    nodes = definition.get("nodes") or []
    node_types = {node.get("type") for node in nodes}
    return "endCall" not in node_types


async def _ensure_seeded_workflow(db_client, user: UserModel, name: str, definition: dict):
    workflows = await db_client.get_all_workflows(
        organization_id=user.selected_organization_id
    )
    existing = next(
        (workflow for workflow in workflows if workflow.name == name), None
    )
    if not existing:
        existing = await db_client.create_workflow(
            name,
            definition,
            user.id,
            user.selected_organization_id,
        )

    # Re-fetch through get_workflow so definition relationships are
    # eager-loaded; objects from get_all_workflows/create_workflow are
    # detached and lazy-loading released_definition raises
    # DetachedInstanceError in prepare_workflow_run_inputs.
    workflow = await db_client.get_workflow(
        existing.id, organization_id=user.selected_organization_id
    )

    released = workflow.released_definition
    if released is not None and _is_broken_seed(released.workflow_json):
        await db_client.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=definition,
        )
        await db_client.publish_workflow_draft(workflow.id)
        workflow = await db_client.get_workflow(
            workflow.id, organization_id=user.selected_organization_id
        )

    return workflow


async def ensure_sakinah_workflow(db_client, user: UserModel):
    return await _ensure_seeded_workflow(
        db_client, user, WORKFLOW_NAME, SAKINAH_WORKFLOW_DEFINITION
    )


async def ensure_service_user_workflow(db_client, user: UserModel):
    return await _ensure_seeded_workflow(
        db_client,
        user,
        SERVICE_USER_WORKFLOW_NAME,
        SERVICE_USER_WORKFLOW_DEFINITION,
    )

