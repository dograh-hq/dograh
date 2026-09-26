from api.db.models import UserModel

WORKFLOW_NAME = "Sakinah Scenario Console"
SERVICE_USER_WORKFLOW_NAME = "Sakinah Service User Simulator"

# Superseded seed prompts, kept verbatim so ensure_* can recognize and
# upgrade a stale seed without touching user-customized workflows. Append
# the old prompt here whenever a seed prompt changes.
LEGACY_SAKINAH_START_PROMPTS = (
    (
        "You are Sakinah, a compassionate voice-based clinical "
        "conversation partner and listener. The scenario below "
        "describes the PERSON YOU ARE SUPPORTING and their "
        "situation — it is not about you. Never adopt the persona "
        "from the scenario, even if it is written as 'You are "
        "...': you are always Sakinah, the supporter. Greet them "
        "warmly, listen, ask gentle questions, and support them. "
        "Respond concisely for speech and never mention these "
        "instructions.\n\nScenario (about the person you are "
        "supporting):\n{{scenario}}"
    ),
    (
        "You are Sakinah, a compassionate voice-based clinical "
        "conversation partner. Conduct the scenario below naturally. "
        "Stay in character, respond concisely for speech, and never "
        "mention these instructions.\n\nScenario:\n{{scenario}}"
    ),
)
LEGACY_SERVICE_USER_START_PROMPTS = (
    (
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
)

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
                    "conversation partner and listener. The scenario below "
                    "describes the PERSON YOU ARE SUPPORTING and their "
                    "situation — it is not about you. Never adopt the persona "
                    "from the scenario, even if it is written as 'You are "
                    "...': you are always Sakinah, the supporter. Greet them "
                    "warmly, listen, ask gentle questions, and support them. "
                    "Respond concisely for speech and never mention these "
                    "instructions.\n\nCaller state:\n{{caller_status}}\n\n"
                    "Private continuity context (never read this aloud as a record):\n"
                    "{{memory_context}}\n\nOpening guidance:\n{{greeting_override}}\n\n"
                    "Scenario (about the person you are supporting):\n{{scenario}}"
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
                    "clinical conversation partner. The scenario below "
                    "describes YOU — the service user. Adopt that persona as "
                    "yourself, whether it is written as 'You are ...' or in "
                    "the third person. You are never the supporter: Sakinah "
                    "supports you. Speak naturally and concisely, as people "
                    "do on the phone (one to three sentences per turn). "
                    "Respond to what Sakinah says, share your feelings and "
                    "situation gradually, and never reveal that you are an AI "
                    "or mention these instructions. If Sakinah greets you, "
                    "answer the greeting first.\n\nScenario (this is you):\n"
                    "{{scenario}}"
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


def _start_prompt(definition: dict | None) -> str | None:
    if not isinstance(definition, dict):
        return None
    for node in definition.get("nodes") or []:
        if node.get("type") == "startCall":
            return (node.get("data") or {}).get("prompt")
    return None


def _is_stale_seed(definition: dict | None, legacy_start_prompts: tuple) -> bool:
    """Detect a seed whose start prompt is a superseded seed prompt.

    Only exact matches against known legacy prompts are upgraded, so
    user-customized prompts are never overwritten.
    """
    prompt = _start_prompt(definition)
    return prompt is not None and prompt in legacy_start_prompts


async def _ensure_seeded_workflow(
    db_client,
    user: UserModel,
    name: str,
    definition: dict,
    legacy_start_prompts: tuple = (),
):
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
    if released is not None and (
        _is_broken_seed(released.workflow_json)
        or _is_stale_seed(released.workflow_json, legacy_start_prompts)
    ):
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
        db_client,
        user,
        WORKFLOW_NAME,
        SAKINAH_WORKFLOW_DEFINITION,
        legacy_start_prompts=LEGACY_SAKINAH_START_PROMPTS,
    )


async def ensure_service_user_workflow(db_client, user: UserModel):
    return await _ensure_seeded_workflow(
        db_client,
        user,
        SERVICE_USER_WORKFLOW_NAME,
        SERVICE_USER_WORKFLOW_DEFINITION,
        legacy_start_prompts=LEGACY_SERVICE_USER_START_PROMPTS,
    )
