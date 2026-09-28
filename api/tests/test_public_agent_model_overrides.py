from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes.public_agent import (
    ResolvedAgentTarget,
    _validate_model_overrides,
    router,
)
from api.schemas.model_provider_profiles import CallModelOverrides
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration import provider_profiles as profiles
from api.services.workflow.initial_context import (
    RUN_MODEL_OVERRIDES_CONTEXT_KEY,
    merge_external_initial_context,
)

ORG_ID = 11
BASE = {
    "llm": {"provider": "openai", "api_key": "sk-default-0000", "model": "gpt-4.1"},
    "tts": {
        "provider": "deepgram",
        "api_key": "dg-default-0000",
        "voice": "aura-2-thalia-en",
    },
    "stt": {"provider": "deepgram", "api_key": "dg-default-0000", "model": "nova-3"},
}
CHEAP = {"provider": "openai", "api_key": "sk-cheap-1111", "model": "gpt-4.1-nano"}
ELEVEN = {"provider": "elevenlabs", "api_key": "el-eu-2222", "voice": "Rachel"}

ROUTES = [
    "/public/agent/trigger-uuid-123",
    "/public/agent/test/trigger-uuid-123",
    "/public/agent/workflow/workflow-uuid-123",
    "/public/agent/test/workflow/workflow-uuid-123",
]


class FakeStore:
    def __init__(self):
        self.rows = {}

    async def get_configuration(self, organization_id, key):
        value = self.rows.get((organization_id, key))
        return SimpleNamespace(value=value) if value is not None else None

    async def upsert_configuration(self, organization_id, key, value, **_kwargs):
        self.rows[(organization_id, key)] = value


def _workflow():
    return SimpleNamespace(
        id=33,
        user_id=99,
        organization_id=ORG_ID,
        status="active",
        workflow_uuid="workflow-uuid-123",
        workflow_configurations={},
        released_definition=SimpleNamespace(
            id=77,
            workflow_json={
                "nodes": [
                    {"type": "trigger", "data": {"trigger_path": "trigger-uuid-123"}}
                ],
                "edges": [],
            },
            template_context_variables={},
            workflow_configurations={},
        ),
        current_definition=None,
        template_context_variables={},
    )


def _provider():
    return SimpleNamespace(
        PROVIDER_NAME="twilio",
        WEBHOOK_ENDPOINT="outbound",
        validate_config=Mock(return_value=True),
        initiate_call=AsyncMock(
            return_value=SimpleNamespace(
                call_id="CA123",
                status="queued",
                caller_number="+1555",
                provider_metadata={},
            )
        ),
    )


@pytest.fixture
async def env():
    """Fake org profile store + everything a trigger call touches."""
    store = FakeStore()
    workflow = _workflow()
    provider = _provider()
    base_config = EffectiveAIModelConfiguration.model_validate(BASE)

    with (
        patch.object(profiles, "db_client", store),
        patch.object(
            profiles.UserConfigurationValidator,
            "validate_single_service",
            new=AsyncMock(return_value={"status": []}),
        ),
        patch("api.routes.public_agent.db_client") as mock_db,
        patch("api.routes.public_agent.call_concurrency") as concurrency,
        patch(
            "api.routes.public_agent.resolve_outbound_configuration_id",
            new=AsyncMock(return_value=55),
        ),
        patch(
            "api.routes.public_agent.authorize_workflow_run_start",
            new=AsyncMock(
                return_value=SimpleNamespace(has_quota=True, error_message="")
            ),
        ),
        patch(
            "api.routes.public_agent.get_telephony_provider_by_id",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "api.routes.public_agent.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://x")),
        ),
        patch(
            "api.routes.public_agent.get_effective_ai_model_configuration_for_workflow",
            new=AsyncMock(return_value=base_config),
        ) as get_effective_config,
    ):
        concurrency.acquire_org_slot = AsyncMock(return_value=object())
        concurrency.bind_workflow_run = AsyncMock()
        concurrency.release_workflow_run_slot = AsyncMock()
        concurrency.release_slot = AsyncMock()
        mock_db.validate_api_key = AsyncMock(
            return_value=SimpleNamespace(id=7, organization_id=ORG_ID, created_by=22)
        )
        mock_db.get_agent_trigger_by_path = AsyncMock(
            return_value=SimpleNamespace(
                workflow_id=33, organization_id=ORG_ID, state="active"
            )
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.get_workflow_by_uuid = AsyncMock(return_value=workflow)
        mock_db.get_draft_version = AsyncMock(return_value=None)
        mock_db.create_workflow_run = AsyncMock(return_value=SimpleNamespace(id=501))
        mock_db.update_workflow_run = AsyncMock()

        await profiles.create_profile(
            ORG_ID, name="openai-cheap", service="llm", config=CHEAP
        )
        await profiles.create_profile(
            ORG_ID, name="eleven-eu", service="tts", config=ELEVEN
        )

        app = FastAPI()
        app.include_router(router)
        yield SimpleNamespace(
            client=TestClient(app),
            db=mock_db,
            provider=provider,
            concurrency=concurrency,
            workflow=workflow,
            get_effective_config=get_effective_config,
        )


def _post(env, url, body):
    return env.client.post(
        url, headers={"X-API-Key": "k"}, json={"phone_number": "+15551234567", **body}
    )


@pytest.mark.parametrize("url", ROUTES)
def test_all_four_routes_accept_and_store_model_overrides(env, url):
    overrides = {
        "llm": {"profile": "openai-cheap"},
        "tts": {"profile": "eleven-eu", "voice": "Bella"},
    }

    response = _post(env, url, {"model_overrides": overrides})

    assert response.status_code == 200, response.text
    context = env.db.create_workflow_run.await_args.kwargs["initial_context"]
    assert context[RUN_MODEL_OVERRIDES_CONTEXT_KEY] == overrides
    # Only profile names and tweaks are stored: no credentials anywhere.
    assert "sk-cheap-1111" not in str(context)
    assert "el-eu-2222" not in str(context)


def test_request_without_overrides_stores_nothing(env):
    assert _post(env, ROUTES[0], {}).status_code == 200
    context = env.db.create_workflow_run.await_args.kwargs["initial_context"]
    assert RUN_MODEL_OVERRIDES_CONTEXT_KEY not in context


def test_empty_overrides_object_stores_nothing(env):
    assert _post(env, ROUTES[0], {"model_overrides": {}}).status_code == 200
    context = env.db.create_workflow_run.await_args.kwargs["initial_context"]
    assert RUN_MODEL_OVERRIDES_CONTEXT_KEY not in context


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"llm": {"profile": "missing"}}, "No saved llm profile named 'missing'"),
        (
            {"llm": {"profile": "openai-cheap", "api_key": "sk-x"}},
            "cannot be overridden",
        ),
        ({"tts": {"profile": "eleven-eu", "voise": "x"}}, "unknown field"),
        ({"realtime": {"profile": "nope"}}, "No saved realtime profile"),
    ],
)
def test_invalid_overrides_return_422_before_any_side_effect(env, overrides, message):
    response = _post(env, ROUTES[0], {"model_overrides": overrides})

    assert response.status_code == 422
    assert message in response.json()["detail"]
    env.concurrency.acquire_org_slot.assert_not_awaited()
    env.db.create_workflow_run.assert_not_awaited()
    env.provider.initiate_call.assert_not_awaited()


def test_overrides_that_leave_no_runnable_config_are_rejected(env):
    incomplete = EffectiveAIModelConfiguration.model_validate({"llm": BASE["llm"]})
    with patch(
        "api.routes.public_agent.get_effective_ai_model_configuration_for_workflow",
        new=AsyncMock(return_value=incomplete),
    ):
        response = _post(
            env, ROUTES[0], {"model_overrides": {"llm": {"profile": "openai-cheap"}}}
        )

    assert response.status_code == 422
    assert "tts, stt" in response.json()["detail"]
    env.db.create_workflow_run.assert_not_awaited()


def test_caller_cannot_inject_the_reserved_key_through_initial_context(env):
    response = _post(
        env,
        ROUTES[0],
        {
            "initial_context": {
                RUN_MODEL_OVERRIDES_CONTEXT_KEY: {"llm": {"profile": "openai-cheap"}}
            }
        },
    )

    assert response.status_code == 200
    context = env.db.create_workflow_run.await_args.kwargs["initial_context"]
    assert RUN_MODEL_OVERRIDES_CONTEXT_KEY not in context


def test_reserved_key_is_stripped_when_merging_external_context():
    merged = merge_external_initial_context(
        {"a": 1}, {RUN_MODEL_OVERRIDES_CONTEXT_KEY: {"llm": {}}, "b": 2}
    )
    assert merged == {"a": 1, "b": 2}


def test_validation_uses_the_pinned_definitions_config_not_a_stale_workflow_one(env):
    """An intentionally empty definition config (`{}`) must not fall back to
    the mutable top-level workflow config -- `{}` is falsy, so a naive `or`
    would silently pick the wrong (possibly stale) source."""
    env.workflow.released_definition.workflow_configurations = {}
    env.workflow.workflow_configurations = {"stale": "marker-should-not-be-used"}

    response = _post(
        env, ROUTES[0], {"model_overrides": {"llm": {"profile": "openai-cheap"}}}
    )

    assert response.status_code == 200
    used_configs = env.get_effective_config.await_args_list[0].kwargs[
        "workflow_configurations"
    ]
    assert used_configs == {}


@pytest.mark.asyncio
async def test_validate_model_overrides_catches_a_plain_value_error_as_422():
    """A ValueError/ValidationError from the second (apply-time) profile read
    -- e.g. a profile edited between the two reads in this one request --
    must surface as the documented 422, not an unhandled 500."""
    workflow = SimpleNamespace(
        organization_id=ORG_ID,
        workflow_configurations={},
        released_definition=None,
        current_definition=SimpleNamespace(workflow_configurations={}),
    )
    target = ResolvedAgentTarget(
        workflow=workflow,
        organization_id=ORG_ID,
        identifier_type="trigger_path",
        identifier_value="trigger-uuid-123",
    )
    overrides = CallModelOverrides.model_validate({"llm": {"profile": "openai-cheap"}})

    with (
        patch(
            "api.routes.public_agent.validate_call_overrides",
            new=AsyncMock(return_value={"llm": {"profile": "openai-cheap"}}),
        ),
        patch(
            "api.routes.public_agent.get_effective_ai_model_configuration_for_workflow",
            new=AsyncMock(
                return_value=EffectiveAIModelConfiguration.model_validate(BASE)
            ),
        ),
        patch(
            "api.routes.public_agent.apply_call_model_overrides",
            new=AsyncMock(side_effect=ValueError("profile changed mid-request")),
        ),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await _validate_model_overrides(target, overrides, use_draft=False)

    assert excinfo.value.status_code == 422
    assert "profile changed mid-request" in excinfo.value.detail
