from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.agent_builder import router
from api.services.auth.depends import get_user

ORG_ID = 11
USER_ID = 1


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=USER_ID,
        provider_id="provider-1",
        selected_organization_id=ORG_ID,
    )
    return app


def _mock_created_workflow(name: str = "My Agent"):
    return SimpleNamespace(id=42, name=name)


# ---------------------------------------------------------------------------
# Templates endpoint
# ---------------------------------------------------------------------------


def test_templates_endpoint_lists_four_templates():
    client = TestClient(_make_test_app())

    response = client.get("/agent-builder/templates")

    assert response.status_code == 200
    templates = response.json()
    assert len(templates) == 4
    assert {t["id"] for t in templates} == {
        "real_estate_cold_caller",
        "appointment_setter",
        "lead_qualifier",
        "support_callback",
    }
    for t in templates:
        assert t["name"]
        assert t["description"]
        assert t["fields"] == ["name", "industry", "details", "language"]


# ---------------------------------------------------------------------------
# Create from template
# ---------------------------------------------------------------------------


def test_create_from_template_builds_org_scoped_workflow_with_filled_prompt():
    client = TestClient(_make_test_app())

    with patch("api.services.agent_builder.service.db_client") as mock_db:
        mock_db.create_workflow = AsyncMock(return_value=_mock_created_workflow())
        response = client.post(
            "/agent-builder/create",
            json={
                "mode": "template",
                "template_id": "real_estate_cold_caller",
                "business": {
                    "name": "Sunrise Homes",
                    "industry": "real estate",
                    "details": "2BHK and 3BHK flats in Pune starting 45 lakh.",
                    "language": "Hinglish",
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {"workflow_id": 42, "name": "My Agent"}

    mock_db.create_workflow.assert_awaited_once()
    name, definition, user_id, organization_id = mock_db.create_workflow.await_args.args
    assert organization_id == ORG_ID
    assert user_id == USER_ID
    assert "Sunrise Homes" in name

    nodes = {n["type"]: n for n in definition["nodes"]}
    assert set(nodes) == {"startCall", "agentNode", "endCall"}
    agent_prompt = nodes["agentNode"]["data"]["prompt"]
    assert "Sunrise Homes" in agent_prompt
    assert "2BHK and 3BHK flats in Pune starting 45 lakh." in agent_prompt
    assert "Hinglish" in agent_prompt
    assert nodes["startCall"]["data"]["greeting"]
    assert "Sunrise Homes" in nodes["startCall"]["data"]["greeting"]
    # start → agent → end wiring
    edges = {(e["source"], e["target"]) for e in definition["edges"]}
    assert edges == {("start-1", "agent-1"), ("agent-1", "end-1")}


def test_create_from_unknown_template_returns_422():
    client = TestClient(_make_test_app())

    with patch("api.services.agent_builder.service.db_client") as mock_db:
        response = client.post(
            "/agent-builder/create",
            json={
                "mode": "template",
                "template_id": "does_not_exist",
                "business": {"name": "Acme"},
            },
        )

    assert response.status_code == 422
    assert mock_db.mock_calls == []


# ---------------------------------------------------------------------------
# Describe mode
# ---------------------------------------------------------------------------


def test_describe_mode_uses_llm_and_returns_workflow():
    client = TestClient(_make_test_app())

    generated = {
        "name": "Tutoring Outreach Agent",
        "system_prompt": "You are a tutoring outreach agent for Acme Tutors.",
        "greeting": "Namaste! Main Acme Tutors se bol rahi hoon.",
    }

    with (
        patch("api.services.agent_builder.service.db_client") as mock_db,
        patch(
            "api.services.agent_builder.service._generate_via_llm",
            new=AsyncMock(return_value=generated),
        ) as mock_llm,
    ):
        mock_db.create_workflow = AsyncMock(
            return_value=_mock_created_workflow("Tutoring Outreach Agent")
        )
        response = client.post(
            "/agent-builder/create",
            json={
                "mode": "describe",
                "description": "Call parents and offer maths tuition demo classes.",
                "business": {"name": "Acme Tutors"},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"workflow_id": 42, "name": "Tutoring Outreach Agent"}

    mock_llm.assert_awaited_once()
    name, definition, user_id, organization_id = mock_db.create_workflow.await_args.args
    assert name == "Tutoring Outreach Agent"
    assert organization_id == ORG_ID
    nodes = {n["type"]: n for n in definition["nodes"]}
    assert nodes["agentNode"]["data"]["prompt"] == generated["system_prompt"]
    assert nodes["startCall"]["data"]["greeting"] == generated["greeting"]


def test_describe_mode_falls_back_to_deterministic_prompt_when_llm_fails():
    client = TestClient(_make_test_app())

    description = "Call leads about our solar panel installation offers."

    with (
        patch("api.services.agent_builder.service.db_client") as mock_db,
        patch(
            "api.services.agent_builder.service._generate_via_llm",
            new=AsyncMock(side_effect=RuntimeError("no llm")),
        ),
    ):
        mock_db.create_workflow = AsyncMock(return_value=_mock_created_workflow())
        response = client.post(
            "/agent-builder/create",
            json={
                "mode": "describe",
                "description": description,
                "business": {"name": "SunVolt"},
            },
        )

    assert response.status_code == 200
    _, definition, _, organization_id = mock_db.create_workflow.await_args.args
    assert organization_id == ORG_ID
    nodes = {n["type"]: n for n in definition["nodes"]}
    agent_prompt = nodes["agentNode"]["data"]["prompt"]
    assert description in agent_prompt
    assert "SunVolt" in agent_prompt


def test_describe_mode_requires_description():
    client = TestClient(_make_test_app())

    with patch("api.services.agent_builder.service.db_client") as mock_db:
        response = client.post(
            "/agent-builder/create",
            json={
                "mode": "describe",
                "business": {"name": "Acme"},
            },
        )

    assert response.status_code == 422
    assert mock_db.mock_calls == []
