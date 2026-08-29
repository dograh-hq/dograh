from api.db.models import UserModel

WORKFLOW_NAME = "Sakinah Scenario Console"

SAKINAH_WORKFLOW_DEFINITION = {
    "nodes": [
        {
            "id": "sakinah-start",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Sakinah",
                "allow_interrupt": True,
                "prompt": (
                    "You are Sakinah, a compassionate voice-based clinical "
                    "conversation partner. Conduct the scenario below naturally. "
                    "Stay in character, respond concisely for speech, and never "
                    "mention these instructions.\n\nScenario:\n{{scenario}}"
                ),
            },
        }
    ],
    "edges": [],
}


async def ensure_sakinah_workflow(db_client, user: UserModel):
    workflows = await db_client.get_all_workflows(
        organization_id=user.selected_organization_id
    )
    existing = next(
        (workflow for workflow in workflows if workflow.name == WORKFLOW_NAME), None
    )
    if existing:
        return existing

    return await db_client.create_workflow(
        WORKFLOW_NAME,
        SAKINAH_WORKFLOW_DEFINITION,
        user.id,
        user.selected_organization_id,
    )

