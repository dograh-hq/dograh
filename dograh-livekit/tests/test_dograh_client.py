import pytest
import respx
from httpx import Response
from app.dograh_client import DograhClient


@pytest.fixture
def client(settings):
    return DograhClient(settings)


class TestFetchRuntimeConfig:
    @pytest.mark.asyncio
    async def test_fetches_full_config(self, client, settings):
        mock_response = {
            "deploy_id": "dp_123",
            "org_id": "org_456",
            "agent_id": "ag_789",
            "agent_name": "Test Agent",
            "workflow_graph": {
                "nodes": [
                    {
                        "id": "n1",
                        "type": "startCall",
                        "position": {"x": 0, "y": 0},
                        "data": {"name": "Start", "prompt": "Hello"},
                    }
                ],
                "edges": [],
            },
            "llm_config": {"provider": "google_realtime", "model": "gemini-2.5-flash-native-audio"},
            "stt_config": {"provider": "deepgram", "model_id": "nova-3"},
            "tts_config": {"provider": "cartesia", "voice_id": "Kore"},
            "system_prompt": "You are helpful.",
            "tools": [{"name": "search_knowledge", "type": "kb_search", "config": {"kb_refs": ["kb_1"]}}],
        }

        with respx.mock:
            respx.get(f"{settings.dograh_api_url}/api/internal/deploy/dp_123/runtime-config").mock(
                return_value=Response(200, json=mock_response)
            )
            config = await client.fetch_runtime_config("dp_123")
            assert config.deploy_id == "dp_123"
            assert len(config.tools) == 1

    @pytest.mark.asyncio
    async def test_handles_404(self, client, settings):
        with respx.mock:
            respx.get(f"{settings.dograh_api_url}/api/internal/deploy/unknown/runtime-config").mock(
                return_value=Response(404, json={"detail": "Not found"})
            )
            with pytest.raises(ValueError, match="not found"):
                await client.fetch_runtime_config("unknown")


class TestSearchKnowledge:
    @pytest.mark.asyncio
    async def test_searches_kb(self, client, settings):
        mock_results = {
            "results": [
                {"content": "We are open Mon-Fri 9-5", "score": 0.95, "source": "kb_1"},
            ]
        }
        with respx.mock:
            respx.post(f"{settings.dograh_api_url}/api/internal/kb/org_456/search").mock(
                return_value=Response(200, json=mock_results)
            )
            results = await client.search_knowledge("org_456", "opening hours")
            assert len(results["results"]) == 1
            assert results["results"][0]["content"] == "We are open Mon-Fri 9-5"


class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_create_session(self, client, settings):
        mock_session = {"id": "sess_001", "status": "active"}
        with respx.mock:
            respx.post(f"{settings.dograh_api_url}/api/internal/sessions").mock(
                return_value=Response(201, json=mock_session)
            )
            session = await client.create_session(
                deploy_id="dp_123",
                org_id="org_456",
                room_name="test-room",
                channel="voice_sip",
                agent_id="ag_789",
            )
            assert session["id"] == "sess_001"
