"""Regression coverage for authenticated Sakinah persistence."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from api.routes.sakinah import _wait_for_workflow_artifacts
from api.services.sakinah.simulation import SAKINAH_ROLE


async def _make_user(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    organization, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, organization.id)
    return await db_session.get_user_by_id(user.id)


def _scenario_payload(title: str) -> dict[str, str]:
    return {
        "title": title,
        "mode": "structured",
        "persona": "A person who is hesitant to disclose information.",
        "behaviour": "Answer briefly at first, then disclose more when asked calmly.",
        "age": "34",
        "gender": "Female",
        "language": "English",
        "emotion": "Guarded",
        "communication_style": "Short, cautious answers",
        "initial_information": "I need help with a difficult situation.",
        "hidden_information": "There is additional context to disclose later.",
        "disclosure": "Disclose the hidden information after rapport is built.",
        "background": "Has had a stressful week.",
        "additional_factors": "Avoid assumptions.",
        "notes": "Persistence regression fixture.",
        "freestyle_prompt": "",
    }


async def test_scenario_crud_is_persistent_and_user_scoped(
    test_client_factory, db_session
):
    owner = await _make_user(db_session, "sakinah_persistence_owner")
    outsider = await _make_user(db_session, "sakinah_persistence_outsider")

    async with test_client_factory(owner) as client:
        created = await client.post(
            "/api/v1/sakinah/scenarios", json=_scenario_payload("Persistent scenario")
        )
        assert created.status_code == 200, created.text
        scenario = created.json()
        scenario_id = scenario["id"]

        listed = await client.get("/api/v1/sakinah/scenarios")
        assert [item["id"] for item in listed.json()["scenarios"]] == [scenario_id]

        updated_payload = _scenario_payload("Updated persistent scenario")
        updated_payload["notes"] = "Updated after re-authentication."
        updated = await client.put(
            f"/api/v1/sakinah/scenarios/{scenario_id}", json=updated_payload
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Updated persistent scenario"

    async with test_client_factory(outsider) as client:
        assert (await client.get("/api/v1/sakinah/scenarios")).json() == {
            "scenarios": []
        }
        assert (
            await client.put(
                f"/api/v1/sakinah/scenarios/{scenario_id}",
                json=_scenario_payload("Should not be allowed"),
            )
        ).status_code == 404
        assert (
            await client.delete(f"/api/v1/sakinah/scenarios/{scenario_id}")
        ).status_code == 404

    async with test_client_factory(owner) as client:
        assert (await client.get("/api/v1/sakinah/scenarios")).json()["scenarios"][0][
            "notes"
        ] == "Updated after re-authentication."
        assert (
            await client.delete(f"/api/v1/sakinah/scenarios/{scenario_id}")
        ).status_code == 204
        assert (await client.get("/api/v1/sakinah/scenarios")).json() == {
            "scenarios": []
        }


async def test_run_summary_persists_transcript_preview_and_recording_refs(
    test_client_factory, db_session
):
    owner = await _make_user(db_session, "sakinah_run_owner")
    outsider = await _make_user(db_session, "sakinah_run_outsider")
    session_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)

    await db_session.create_sakinah_run(
        session_id=session_id,
        user_id=owner.id,
        agent_id=101,
        run_id=202,
        scenario="A persisted run scenario",
        started_at=started_at,
    )
    completed = await db_session.complete_sakinah_run(
        user_id=owner.id,
        session_id=session_id,
        status="completed",
        ended_at=datetime.now(UTC),
        transcript="[now] user: Hello\n[now] assistant: How can I help?\n",
        conversation=[{"role": SAKINAH_ROLE, "text": "How can I help?"}],
        preview_data={"turns": 1, "summary": "saved"},
        recording_url="https://storage.example/recording.wav",
        transcript_url="https://storage.example/transcript.txt",
        recording_file_reference={"mixed": "recording.wav"},
        timings={"duration_ms": 1200},
    )
    assert completed is not None

    async with test_client_factory(owner) as client:
        response = await client.get(f"/api/v1/sakinah/runs/{session_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["agent_id"] == 101
        assert payload["run_id"] == 202
        assert payload["transcript"].startswith("[now] user: Hello")
        assert payload["conversation"][0]["text"] == "How can I help?"
        assert payload["preview_data"]["summary"] == "saved"
        assert payload["recording_url"].endswith("recording.wav")
        assert payload["recording_file_reference"] == {"mixed": "recording.wav"}

    async with test_client_factory(outsider) as client:
        assert (await client.get("/api/v1/sakinah/runs")).json() == {"runs": []}
        assert (
            await client.get(f"/api/v1/sakinah/runs/{session_id}")
        ).status_code == 404


async def test_end_session_artifact_lookup_waits_for_pipeline_upload():
    delayed_lookup = AsyncMock(
        side_effect=[
            {},
            {"recording_url": "recordings/303.wav", "transcript_url": None},
        ]
    )
    with (
        patch(
            "api.routes.sakinah.db_client.get_workflow_run_artifacts_for_user",
            new=delayed_lookup,
        ),
        patch("api.routes.sakinah.asyncio.sleep", new=AsyncMock()),
    ):
        artifacts = await _wait_for_workflow_artifacts(7, 303)

    assert artifacts == {
        "recording_url": "recordings/303.wav",
        "transcript_url": None,
    }
    assert delayed_lookup.await_count == 2
