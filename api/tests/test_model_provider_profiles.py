from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.model_provider_profiles import router
from api.schemas.model_provider_profiles import (
    ModelProviderProfile,
    ModelProviderProfilesDocument,
    parse_profile_config,
)
from api.services.auth.depends import get_user_with_selected_organization
from api.services.configuration import provider_profiles as profiles
from api.services.configuration.masking import mask_key

ORG_ID = 11
OPENAI_A = {
    "provider": "openai",
    "api_key": "sk-aaaaaaaaaaaa1111",
    "model": "gpt-4.1-mini",
}
OPENAI_B = {
    "provider": "openai",
    "api_key": "sk-bbbbbbbbbbbb2222",
    "model": "gpt-4.1-nano",
}
DEEPGRAM_TTS = {
    "provider": "deepgram",
    "api_key": "dg-cccccccccccc3333",
    "model": "aura-2",
    "voice": "aura-2-thalia-en",
}


class FakeStore:
    """In-memory stand-in for organization_configurations."""

    def __init__(self):
        self.rows: dict[tuple[int, str], dict] = {}

    async def get_configuration(self, organization_id, key):
        value = self.rows.get((organization_id, key))
        return SimpleNamespace(value=value) if value is not None else None

    async def upsert_configuration(self, organization_id, key, value, **_kwargs):
        self.rows[(organization_id, key)] = value

    async def upsert_configuration_with_lock(
        self, organization_id, key, mutate, **_kwargs
    ):
        current = self.rows.get((organization_id, key))
        new_value = mutate(current)
        self.rows[(organization_id, key)] = new_value
        return SimpleNamespace(value=new_value)


class RaceStore(FakeStore):
    """Simulates another writer's row landing between two callers'
    optimistic pre-check and their turn at the row lock -- proving the
    write re-checks the *locked* value rather than trusting a stale read."""

    def __init__(self, injected_write):
        super().__init__()
        self._injected_write = injected_write
        self._injected = False

    async def upsert_configuration_with_lock(
        self, organization_id, key, mutate, **_kwargs
    ):
        if not self._injected:
            self._injected = True
            self.rows[(organization_id, key)] = self._injected_write(
                self.rows.get((organization_id, key))
            )
        return await super().upsert_configuration_with_lock(
            organization_id, key, mutate, **_kwargs
        )


@pytest.fixture
def store():
    fake = FakeStore()
    with (
        patch.object(profiles, "db_client", fake),
        patch.object(
            profiles.UserConfigurationValidator,
            "validate_single_service",
            new=AsyncMock(return_value={"status": []}),
        ) as validator,
    ):
        fake.validator = validator
        yield fake


# ---------------------------------------------------------------- schemas


def test_two_profiles_may_share_a_provider():
    document = ModelProviderProfilesDocument(
        profiles=[
            ModelProviderProfile(name="openai-prod", service="llm", config=OPENAI_A),
            ModelProviderProfile(name="openai-cheap", service="llm", config=OPENAI_B),
        ]
    )
    assert [p.name for p in document.profiles] == ["openai-prod", "openai-cheap"]


def test_duplicate_name_within_a_service_is_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        ModelProviderProfilesDocument(
            profiles=[
                ModelProviderProfile(name="main", service="llm", config=OPENAI_A),
                ModelProviderProfile(name="main", service="llm", config=OPENAI_B),
            ]
        )


def test_same_name_in_different_services_is_allowed():
    ModelProviderProfilesDocument(
        profiles=[
            ModelProviderProfile(name="main", service="llm", config=OPENAI_A),
            ModelProviderProfile(name="main", service="tts", config=DEEPGRAM_TTS),
        ]
    )


@pytest.mark.parametrize(
    "name", ["", "Has Caps", "-leading", "a" * 49, "with space", "abc\n", "abc\x00"]
)
def test_invalid_profile_names_are_rejected(name):
    with pytest.raises(ValueError):
        ModelProviderProfile(name=name, service="llm", config=OPENAI_A)


def test_non_string_provider_is_rejected_not_500():
    # A JSON list/object provider must not reach REGISTRY.get() as a dict key
    # (unhashable -> TypeError -> 500); it must fail as an ordinary 422.
    with pytest.raises(ValueError, match="must be a string"):
        parse_profile_config("llm", {"provider": ["openai"], "api_key": "x"})
    with pytest.raises(ValueError, match="must be a string"):
        parse_profile_config("llm", {"provider": {"nested": True}, "api_key": "x"})


def test_dograh_provider_cannot_be_saved_as_a_profile():
    with pytest.raises(ValueError, match="dograh"):
        parse_profile_config(
            "llm", {"provider": "dograh", "api_key": "x", "model": "default"}
        )


def test_unknown_provider_and_service_are_rejected():
    with pytest.raises(ValueError, match="Unknown"):
        parse_profile_config("llm", {"provider": "nope", "api_key": "x", "model": "m"})
    with pytest.raises(ValueError, match="Unsupported"):
        parse_profile_config("embeddings", OPENAI_A)


def test_provider_must_belong_to_the_service():
    # deepgram is a TTS/STT provider, not an LLM provider
    with pytest.raises(ValueError, match="Unknown llm provider"):
        parse_profile_config("llm", DEEPGRAM_TTS)


# ---------------------------------------------------------------- service


@pytest.mark.asyncio
async def test_create_and_list_round_trip(store):
    await profiles.create_profile(
        ORG_ID, name="openai-prod", service="llm", config=OPENAI_A
    )
    await profiles.create_profile(
        ORG_ID, name="openai-cheap", service="llm", config=OPENAI_B
    )

    listed = await profiles.list_profiles(ORG_ID)

    assert {p.name for p in listed} == {"openai-prod", "openai-cheap"}
    assert store.validator.await_count == 2


@pytest.mark.asyncio
async def test_create_duplicate_name_conflicts(store):
    await profiles.create_profile(ORG_ID, name="main", service="llm", config=OPENAI_A)
    with pytest.raises(profiles.ProfileAlreadyExistsError):
        await profiles.create_profile(
            ORG_ID, name="main", service="llm", config=OPENAI_B
        )


@pytest.mark.asyncio
async def test_profiles_are_isolated_per_organization(store):
    await profiles.create_profile(ORG_ID, name="main", service="llm", config=OPENAI_A)

    assert await profiles.list_profiles(ORG_ID + 1) == []
    with pytest.raises(profiles.ProfileNotFoundError):
        await profiles.get_profile(ORG_ID + 1, "llm", "main")


@pytest.mark.asyncio
async def test_create_rejects_masked_secret(store):
    masked = {**OPENAI_A, "api_key": mask_key(OPENAI_A["api_key"])}
    with pytest.raises(profiles.ProfileConfigError, match="masked"):
        await profiles.create_profile(ORG_ID, name="main", service="llm", config=masked)


@pytest.mark.asyncio
async def test_failed_credential_check_saves_nothing(store):
    store.validator.side_effect = ValueError([{"model": "llm", "message": "bad key"}])
    with pytest.raises(profiles.ProfileConfigError):
        await profiles.create_profile(
            ORG_ID, name="main", service="llm", config=OPENAI_A
        )
    assert await profiles.list_profiles(ORG_ID) == []


@pytest.mark.asyncio
async def test_update_keeps_stored_secret_when_client_sends_the_mask(store):
    await profiles.create_profile(ORG_ID, name="main", service="llm", config=OPENAI_A)

    masked_update = {
        **OPENAI_A,
        "api_key": mask_key(OPENAI_A["api_key"]),
        "model": "gpt-4.1",
    }
    await profiles.update_profile(
        ORG_ID, service="llm", name="main", config=masked_update
    )

    stored = await profiles.get_profile(ORG_ID, "llm", "main")
    assert stored.config["api_key"] == OPENAI_A["api_key"]
    assert stored.config["model"] == "gpt-4.1"


@pytest.mark.asyncio
async def test_update_rejects_a_mask_that_does_not_match_the_stored_key(store):
    """A mask that doesn't match the stored key can't be a real key either --
    it must be rejected (422), not silently kept or silently accepted."""
    await profiles.create_profile(ORG_ID, name="main", service="llm", config=OPENAI_A)

    wrong_mask_update = {**OPENAI_A, "api_key": "****wxyz"}
    with pytest.raises(profiles.ProfileConfigError, match="masked"):
        await profiles.update_profile(
            ORG_ID, service="llm", name="main", config=wrong_mask_update
        )

    stored = await profiles.get_profile(ORG_ID, "llm", "main")
    assert stored.config["api_key"] == OPENAI_A["api_key"]


@pytest.mark.asyncio
async def test_update_with_new_key_replaces_it(store):
    await profiles.create_profile(ORG_ID, name="main", service="llm", config=OPENAI_A)
    await profiles.update_profile(
        ORG_ID,
        service="llm",
        name="main",
        config={**OPENAI_A, "api_key": "sk-new-key-9999"},
    )
    assert (await profiles.get_profile(ORG_ID, "llm", "main")).config[
        "api_key"
    ] == "sk-new-key-9999"


@pytest.mark.asyncio
async def test_update_and_delete_missing_profile_raise_not_found(store):
    with pytest.raises(profiles.ProfileNotFoundError):
        await profiles.update_profile(
            ORG_ID, service="llm", name="nope", config=OPENAI_A
        )
    with pytest.raises(profiles.ProfileNotFoundError):
        await profiles.delete_profile(ORG_ID, service="llm", name="nope")


@pytest.mark.asyncio
async def test_delete_removes_only_the_named_profile(store):
    await profiles.create_profile(ORG_ID, name="a", service="llm", config=OPENAI_A)
    await profiles.create_profile(ORG_ID, name="b", service="llm", config=OPENAI_B)

    await profiles.delete_profile(ORG_ID, service="llm", name="a")

    assert [p.name for p in await profiles.list_profiles(ORG_ID)] == ["b"]


# ---------------------------------------------------------- concurrent writes


@pytest.mark.asyncio
async def test_create_detects_a_conflict_that_appears_after_the_precheck():
    """Two callers can pass the (fast, pre-lock) duplicate-name pre-check for
    the same name before either has written. The second one's actual write
    must detect the now-real conflict under the row lock and raise -- not
    silently clobber the first writer's profile with a stale read."""
    concurrent_write = ModelProviderProfilesDocument(
        profiles=[ModelProviderProfile(name="main", service="llm", config=OPENAI_A)]
    ).model_dump(mode="json", exclude_none=True)
    store = RaceStore(injected_write=lambda _current: concurrent_write)

    with (
        patch.object(profiles, "db_client", store),
        patch.object(
            profiles.UserConfigurationValidator,
            "validate_single_service",
            new=AsyncMock(return_value={"status": []}),
        ),
    ):
        with pytest.raises(profiles.ProfileAlreadyExistsError):
            await profiles.create_profile(
                ORG_ID, name="main", service="llm", config=OPENAI_B
            )

    stored = ModelProviderProfilesDocument.model_validate(
        store.rows[(ORG_ID, profiles._KEY)]
    )
    # The concurrent writer's profile survived untouched; our conflicting
    # create was rejected, not appended on top of it.
    assert [p.name for p in stored.profiles] == ["main"]
    assert stored.profiles[0].config["api_key"] == OPENAI_A["api_key"]


@pytest.mark.asyncio
async def test_update_detects_a_deletion_that_happened_after_the_read():
    """The profile existed when `update_profile` read it (and validated the
    new credentials against it), but was deleted by another writer before
    this caller's turn at the lock. The update must fail loudly -- not
    silently resurrect a profile an administrator just deleted."""
    store = RaceStore(
        injected_write=lambda _current: ModelProviderProfilesDocument().model_dump(
            mode="json", exclude_none=True
        )
    )
    store.rows[(ORG_ID, profiles._KEY)] = ModelProviderProfilesDocument(
        profiles=[ModelProviderProfile(name="main", service="llm", config=OPENAI_A)]
    ).model_dump(mode="json", exclude_none=True)

    with (
        patch.object(profiles, "db_client", store),
        patch.object(
            profiles.UserConfigurationValidator,
            "validate_single_service",
            new=AsyncMock(return_value={"status": []}),
        ),
    ):
        with pytest.raises(profiles.ProfileNotFoundError):
            await profiles.update_profile(
                ORG_ID, service="llm", name="main", config=OPENAI_B
            )

    stored = ModelProviderProfilesDocument.model_validate(
        store.rows[(ORG_ID, profiles._KEY)]
    )
    assert stored.profiles == []


@pytest.mark.asyncio
async def test_update_does_not_restore_a_key_rotated_by_another_writer():
    """Admin A rotates a profile's key (a full update). Admin B's own
    concurrent update -- sending the *old*, now-stale masked key, read
    before A's rotation landed -- must not silently overwrite A's new key
    with the old one once B's turn at the lock arrives."""
    rotated_key = "sk-bbbbbbbbbbbb1111"  # same length + last-4 as OPENAI_A, so B's stale mask of the old key still matches mask_key(rotated_key)
    store = RaceStore(
        injected_write=lambda _current: ModelProviderProfilesDocument(
            profiles=[
                ModelProviderProfile(
                    name="main",
                    service="llm",
                    config={**OPENAI_A, "api_key": rotated_key},
                )
            ]
        ).model_dump(mode="json", exclude_none=True)
    )
    store.rows[(ORG_ID, profiles._KEY)] = ModelProviderProfilesDocument(
        profiles=[ModelProviderProfile(name="main", service="llm", config=OPENAI_A)]
    ).model_dump(mode="json", exclude_none=True)

    with (
        patch.object(profiles, "db_client", store),
        patch.object(
            profiles.UserConfigurationValidator,
            "validate_single_service",
            new=AsyncMock(return_value={"status": []}),
        ),
    ):
        # B's request: masked key (unchanged from B's point of view) + a
        # harmless model tweak. B read OPENAI_A's key before A's rotation.
        await profiles.update_profile(
            ORG_ID,
            service="llm",
            name="main",
            config={
                **OPENAI_A,
                "api_key": mask_key(OPENAI_A["api_key"]),
                "model": "gpt-4.1",
            },
        )

        stored = await profiles.get_profile(ORG_ID, "llm", "main")

    # A's rotated key survives; B's masked "unchanged" field resolved
    # against the *current* key at write time, not B's stale read.
    assert stored.config["api_key"] == rotated_key
    assert stored.config["model"] == "gpt-4.1"


def test_mask_profile_hides_the_api_key():
    profile = ModelProviderProfile(name="main", service="llm", config=OPENAI_A)
    masked = profiles.mask_profile(profile)
    assert masked.config["api_key"] == mask_key(OPENAI_A["api_key"])
    assert OPENAI_A["api_key"] not in masked.model_dump_json()


# ----------------------------------------------------------------- routes


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: (
        SimpleNamespace(id=1, selected_organization_id=ORG_ID, provider_id="user-1")
    )
    return TestClient(app)


BASE = "/organizations/model-configurations/v2/profiles"


def test_routes_full_lifecycle_never_leak_keys(client):
    created = client.post(
        BASE, json={"name": "openai-prod", "service": "llm", "config": OPENAI_A}
    )
    assert created.status_code == 201
    assert OPENAI_A["api_key"] not in created.text

    listing = client.get(BASE)
    assert listing.status_code == 200
    assert [p["name"] for p in listing.json()["profiles"]] == ["openai-prod"]
    assert OPENAI_A["api_key"] not in listing.text

    masked_config = listing.json()["profiles"][0]["config"]
    updated = client.put(
        f"{BASE}/llm/openai-prod",
        json={"config": {**masked_config, "model": "gpt-4.1"}},
    )
    assert updated.status_code == 200
    assert updated.json()["config"]["model"] == "gpt-4.1"
    assert OPENAI_A["api_key"] not in updated.text

    assert client.delete(f"{BASE}/llm/openai-prod").status_code == 204
    assert client.get(BASE).json()["profiles"] == []


def test_routes_status_codes(client):
    body = {"name": "main", "service": "llm", "config": OPENAI_A}
    assert client.post(BASE, json=body).status_code == 201
    assert client.post(BASE, json=body).status_code == 409
    assert client.post(BASE, json={**body, "name": "Bad Name"}).status_code == 422
    assert (
        client.post(
            BASE,
            json={
                "name": "x",
                "service": "llm",
                "config": {"provider": "dograh", "api_key": "k", "model": "m"},
            },
        ).status_code
        == 422
    )
    assert (
        client.put(f"{BASE}/llm/missing", json={"config": OPENAI_A}).status_code == 404
    )
    assert client.delete(f"{BASE}/llm/missing").status_code == 404
    assert (
        client.put(f"{BASE}/embeddings/main", json={"config": OPENAI_A}).status_code
        == 422
    )
    # A non-string provider (JSON list/object) must 422, not crash the
    # registry lookup with an unhashable-key TypeError.
    assert (
        client.post(
            BASE,
            json={"name": "y", "service": "llm", "config": {"provider": ["openai"]}},
        ).status_code
        == 422
    )
    # A name with a trailing newline must be rejected, not silently accepted
    # by a `$`-anchored regex.
    assert (
        client.post(
            BASE, json={"name": "abc\n", "service": "llm", "config": OPENAI_A}
        ).status_code
        == 422
    )
