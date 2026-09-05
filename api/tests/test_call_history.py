from types import SimpleNamespace

import pytest

from api.routes import call_history


@pytest.mark.asyncio
async def test_call_replay_returns_short_lived_signed_url_without_storage_details(monkeypatch):
    class _Storage:
        async def aget_signed_url(self, key, expiration, force_inline):
            assert key == "recordings/2026/09/service-user/call/call.wav"
            assert expiration == 300
            assert force_inline is True
            return "https://signed.example/replay?expires=300"

    class _DB:
        async def get_call_replay_for_user(self, call_id, **kwargs):
            assert call_id == "call-1"
            assert kwargs["organization_id"] == 7
            return {
                "call_id": call_id,
                "agent_run_id": 42,
                "storage_backend": None,
                "recording_key": "recordings/2026/09/service-user/call/call.wav",
                "transcript": "user: Hello",
                "utterances": [],
            }

    monkeypatch.setattr(call_history, "db_client", _DB())
    monkeypatch.setattr(call_history, "storage_fs", _Storage())
    response = await call_history.get_call_replay(
        "call-1",
        expires_in=300,
        user=SimpleNamespace(selected_organization_id=7, is_superuser=False),
    )
    payload = response.model_dump()
    assert payload["recording_signed_url"].startswith("https://signed.example/")
    assert "recording_key" not in payload
    assert "bucket" not in payload
