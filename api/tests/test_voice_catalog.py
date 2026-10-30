from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from api.routes import user as user_routes
from api.services.auth.depends import get_user
from api.services.configuration import voice_catalog

HttpClient = httpx.AsyncClient


@pytest.fixture
def connection(monkeypatch):
    row = SimpleNamespace(
        uuid="elevenlabs-connection",
        organization_id=42,
        provider="elevenlabs",
        is_active=True,
        credentials={"api_key": "private-elevenlabs-key"},
        connection_settings={"base_url": "wss://api.eu.residency.elevenlabs.io/proxy/"},
    )

    async def scoped_lookup(organization_id, uuid):
        if (
            row.organization_id == organization_id
            and row.uuid == uuid
            and row.is_active
        ):
            return row
        return None

    monkeypatch.setattr(
        voice_catalog.db_client,
        "get_provider_connection",
        AsyncMock(side_effect=scoped_lookup),
    )
    return row


@pytest.fixture
def upstream(monkeypatch):
    requests = []

    def install(handler):
        def record(request):
            requests.append(request)
            return handler(request)

        monkeypatch.setattr(
            voice_catalog.httpx,
            "AsyncClient",
            lambda **kwargs: HttpClient(
                transport=httpx.MockTransport(record), **kwargs
            ),
        )
        return requests

    return install


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(user_routes.router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        selected_organization_id=42, provider_id="user-42"
    )
    async with HttpClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_saved_credentials_pagination_and_model_metadata(
    connection, upstream, client
):
    def page(request):
        if request.url.params.get("next_page_token"):
            return httpx.Response(
                200,
                json={
                    "voices": [{"voice_id": "cloned", "name": "Anna clone"}],
                    "has_more": False,
                },
            )
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "voice_id": "ready",
                        "name": "Sofie",
                        "labels": {"gender": "female", "language": "da"},
                        "fine_tuning": {"state": {"eleven_v4_turbo": "fine_tuned"}},
                        "preview_url": "https://audio.example/generic.mp3",
                        "verified_languages": [
                            {
                                "model_id": "eleven_v4_turbo",
                                "language": "da",
                                "accent": "danish",
                                "preview_url": "https://audio.example/danish-v4.mp3",
                            }
                        ],
                    },
                    {
                        "voice_id": "pending",
                        "name": "Pending clone",
                        "fine_tuning": {"state": {"eleven_v4_turbo": "fine_tuning"}},
                    },
                    {
                        "voice_id": "unknown",
                        "name": "Unknown compatibility",
                        "high_quality_base_model_ids": ["eleven_multilingual_v2"],
                    },
                ],
                "has_more": True,
                "next_page_token": "second-page",
            },
        )

    requests = upstream(page)
    response = await client.get(
        "/user/configurations/voices/elevenlabs",
        params={
            "provider_connection_uuid": connection.uuid,
            "model": "eleven_v4_turbo",
        },
    )
    assert response.status_code == 200
    voices = response.json()["voices"]
    assert [voice["voice_id"] for voice in voices] == ["cloned", "ready", "unknown"]
    assert voices[1]["preview_url"] == "https://audio.example/danish-v4.mp3"
    assert voices[1]["language"] == "da"
    assert "private-elevenlabs-key" not in response.text
    assert len(requests) == 2
    for request in requests:
        assert str(request.url).startswith(
            "https://api.eu.residency.elevenlabs.io/proxy/v2/voices?"
        )
        assert request.headers["xi-api-key"] == "private-elevenlabs-key"
        assert request.url.params["page_size"] == "100"
        assert "model_id" not in request.url.params
    assert requests[1].url.params["next_page_token"] == "second-page"
    voice_catalog.db_client.get_provider_connection.assert_awaited_once_with(
        42, connection.uuid
    )


@pytest.mark.parametrize("unavailable", ["other_org", "archived", "unknown"])
@pytest.mark.asyncio
async def test_unavailable_connections_never_reach_provider(
    unavailable, connection, upstream, client
):
    uuid = connection.uuid
    if unavailable == "other_org":
        connection.organization_id = 99
    elif unavailable == "archived":
        connection.is_active = False
    else:
        uuid = "unknown"
    requests = upstream(lambda request: pytest.fail("Must not contact ElevenLabs"))
    response = await client.get(
        "/user/configurations/voices/elevenlabs",
        params={"provider_connection_uuid": uuid},
    )
    assert response.status_code == 404
    assert requests == []


@pytest.mark.parametrize("invalid", ["provider", "key", "connection"])
@pytest.mark.asyncio
async def test_missing_or_wrong_connection_is_actionable(
    invalid, connection, upstream, client
):
    if invalid == "provider":
        connection.provider = "openai"
    elif invalid == "key":
        connection.credentials = {}
    requests = upstream(lambda request: pytest.fail("Must not contact ElevenLabs"))
    response = await client.get(
        "/user/configurations/voices/elevenlabs",
        params=(
            {}
            if invalid == "connection"
            else {"provider_connection_uuid": connection.uuid}
        ),
    )
    assert response.status_code == 400
    assert "connection" in response.json()["detail"].lower()
    assert requests == []


@pytest.mark.parametrize(
    "status,expected", [(401, 400), (403, 400), (429, 429), (500, 502), (302, 502)]
)
@pytest.mark.asyncio
async def test_provider_errors_are_safe_and_redirects_are_not_followed(
    status, expected, connection, upstream, client
):
    requests = upstream(
        lambda request: httpx.Response(
            status,
            json={"detail": "private-elevenlabs-key"},
            headers={"Location": "http://127.0.0.1/private"},
        )
    )
    response = await client.get(
        "/user/configurations/voices/elevenlabs",
        params={"provider_connection_uuid": connection.uuid},
    )
    assert response.status_code == expected
    assert "private-elevenlabs-key" not in response.text
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_private_service_url_is_blocked_in_saas(
    monkeypatch, connection, upstream
):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")
    connection.connection_settings["base_url"] = "http://127.0.0.1"
    requests = upstream(lambda request: pytest.fail("Must not contact private server"))
    with pytest.raises(HTTPException) as exc:
        await voice_catalog.get_connection_voices("elevenlabs", 42, connection.uuid)
    assert exc.value.status_code == 400
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_key", ["first-key", "second-key"])
async def test_key_pool_uses_one_key_for_every_page(
    selected_key, monkeypatch, connection, upstream
):
    connection.credentials["api_key"] = ["first-key", "second-key"]
    select_key = Mock(return_value=selected_key)
    monkeypatch.setattr(voice_catalog, "choice", select_key)

    def page(request):
        second = request.url.params.get("next_page_token") == "second-page"
        return httpx.Response(
            200,
            json={
                "voices": [
                    {"voice_id": "second" if second else "first", "name": "Sofie"}
                ],
                "has_more": not second,
                "next_page_token": None if second else "second-page",
            },
        )

    requests = upstream(page)
    response = await voice_catalog.get_connection_voices(
        "elevenlabs", 42, connection.uuid, q="Sofie", language="da"
    )
    assert {voice.voice_id for voice in response.voices} == {"first", "second"}
    select_key.assert_called_once_with(["first-key", "second-key"])
    assert len(requests) == 2
    assert "next_page_token" not in requests[0].url.params
    assert requests[1].url.params["next_page_token"] == "second-page"
    for request in requests:
        assert request.headers["xi-api-key"] == selected_key
        assert request.url.params["search"] == "Sofie"
        assert request.url.params["language"] == "da"


@pytest.mark.parametrize(
    "model,language,expected",
    [
        (None, None, ("en", "british", "english-v3")),
        (None, "da", ("da", "danish", "danish-v4")),
        ("eleven_v4_turbo", "da", ("da", "danish", "danish-v4")),
        ("eleven_flash_v2_5", None, ("en", "american", "generic")),
    ],
)
@pytest.mark.asyncio
async def test_elevenlabs_verified_previews_respect_optional_model_and_language(
    model, language, expected, connection, upstream
):
    upstream(
        lambda request: httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "voice_id": "sofie",
                        "name": "Sofie",
                        "labels": {"language": "en", "accent": "american"},
                        "preview_url": "https://audio.example/generic.mp3",
                        "verified_languages": [
                            {
                                "model_id": "eleven_v3",
                                "language": "en",
                                "accent": "british",
                                "preview_url": "https://audio.example/english-v3.mp3",
                            },
                            {
                                "model_id": "eleven_v4_turbo",
                                "language": "da",
                                "accent": "danish",
                                "preview_url": "https://audio.example/danish-v4.mp3",
                            },
                        ],
                    }
                ]
            },
        )
    )
    response = await voice_catalog.get_connection_voices(
        "elevenlabs", 42, connection.uuid, model=model, language=language
    )
    voice = response.voices[0]
    expected_language, expected_accent, preview = expected
    assert (voice.language, voice.accent, voice.preview_url) == (
        expected_language,
        expected_accent,
        f"https://audio.example/{preview}.mp3",
    )


@pytest.mark.asyncio
async def test_repeated_pagination_token_fails_without_returning_partial_list(
    connection, upstream
):
    requests = upstream(
        lambda request: httpx.Response(
            200, json={"voices": [], "has_more": True, "next_page_token": "same"}
        )
    )
    with pytest.raises(HTTPException) as exc:
        await voice_catalog.get_connection_voices("elevenlabs", 42, connection.uuid)
    assert exc.value.status_code == 502
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_timeout_is_actionable(connection, upstream):
    def timeout(request):
        raise httpx.ReadTimeout("private-elevenlabs-key", request=request)

    upstream(timeout)
    with pytest.raises(HTTPException) as exc:
        await voice_catalog.get_connection_voices("elevenlabs", 42, connection.uuid)
    assert exc.value.status_code == 504
    assert "private-elevenlabs-key" not in exc.value.detail


@pytest.fixture(autouse=True)
def mps_not_used(monkeypatch):
    mock = AsyncMock(side_effect=AssertionError("External catalogs must never use MPS"))
    monkeypatch.setattr(user_routes.mps_service_key_client, "get_voices", mock)
    return mock


@pytest.mark.parametrize(
    "provider", ["elevenlabs", "cartesia", "deepgram", "rime", "sarvam"]
)
@pytest.mark.parametrize(
    "invalid", ["missing", "wrong_provider", "other_org", "archived"]
)
@pytest.mark.asyncio
async def test_every_external_catalog_requires_its_own_active_connection(
    provider, invalid, connection, upstream, client, mps_not_used
):
    connection.provider = provider
    if invalid == "wrong_provider":
        connection.provider = "openai"
    elif invalid == "other_org":
        connection.organization_id = 99
    elif invalid == "archived":
        connection.is_active = False
    requests = upstream(
        lambda request: pytest.fail("Invalid connection must not make requests")
    )
    response = await client.get(
        f"/user/configurations/voices/{provider}",
        params=(
            {}
            if invalid == "missing"
            else {"provider_connection_uuid": connection.uuid}
        ),
    )
    assert response.status_code == (
        404 if invalid in {"other_org", "archived"} else 400
    )
    assert requests == []
    mps_not_used.assert_not_awaited()


@pytest.mark.asyncio
async def test_cartesia_pages_include_private_voices_and_previews(
    connection, upstream, client
):
    connection.provider = "cartesia"
    connection.credentials = {"api_key": "cartesia-key"}
    connection.connection_settings = {}

    def page(request):
        second = "starting_after" in request.url.params
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "clone" if second else "public",
                        "name": "My clone" if second else "Skylar",
                        "is_owner": second,
                        "gender": "feminine",
                        "language": "en",
                        "accents": [{"accent": "general-american", "is_native": True}],
                        "preview_file_url": "https://audio.example/preview.wav",
                    }
                ],
                "has_more": not second,
                "next_page": None if second else "public",
            },
        )

    requests = upstream(page)
    response = await client.get(
        "/user/configurations/voices/cartesia",
        params={
            "provider_connection_uuid": connection.uuid,
            "model": "sonic-3.6",
            "language": "en",
            "gender": "female",
        },
    )
    assert response.status_code == 200
    assert [voice["voice_id"] for voice in response.json()["voices"]] == [
        "clone",
        "public",
    ]
    voice = response.json()["voices"][0]
    assert (voice["gender"], voice["accent"], voice["preview_url"]) == (
        "female",
        "general-american",
        "https://audio.example/preview.wav",
    )
    assert len(requests) == 2
    assert requests[1].url.params["starting_after"] == "public"
    for request in requests:
        assert request.url.host == "api.cartesia.ai"
        assert request.headers["Authorization"] == "Bearer cartesia-key"
        assert request.headers["Cartesia-Version"] == "2026-08-14"
        assert request.url.params["expand[]"] == "preview_file_url"
        assert request.url.params["gender"] == "feminine"
        assert "is_owner" not in request.url.params
        assert "model" not in request.url.params


@pytest.mark.asyncio
async def test_cartesia_repeated_cursor_fails(connection, upstream):
    connection.provider = "cartesia"
    connection.connection_settings = {}
    requests = upstream(
        lambda request: httpx.Response(
            200, json={"data": [], "has_more": True, "next_page": "same"}
        )
    )
    with pytest.raises(HTTPException) as exc:
        await voice_catalog.get_connection_voices("cartesia", 42, connection.uuid)
    assert exc.value.status_code == 502
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_deepgram_uses_regional_connection_and_filters_tts_models(
    connection, upstream
):
    connection.provider = "deepgram"
    connection.credentials = {"api_key": "deepgram-key"}
    connection.connection_settings = {"base_url": "https://api.eu.deepgram.com"}
    requests = upstream(
        lambda request: httpx.Response(
            200,
            json={
                "stt": [{"name": "nova-3"}],
                "tts": [
                    {
                        "canonical_name": "aura-2-helena-en",
                        "name": "helena",
                        "architecture": "aura-2",
                        "languages": ["en"],
                        "metadata": {
                            "tags": ["feminine", "friendly"],
                            "accent": "American",
                            "sample": "https://audio.example/helena.wav",
                        },
                    },
                    {
                        "canonical_name": "aura-asteria-en",
                        "name": "asteria",
                        "architecture": "aura",
                        "languages": ["en"],
                    },
                    {
                        "canonical_name": "aura-2-celeste-es",
                        "name": "celeste",
                        "architecture": "aura-2",
                        "languages": ["es"],
                    },
                ],
            },
        )
    )
    voices = await voice_catalog.get_connection_voices(
        "deepgram", 42, connection.uuid, model="aura-2", language="en"
    )
    assert [voice.voice_id for voice in voices.voices] == ["aura-2-helena-en"]
    assert voices.voices[0].gender == "female"
    assert voices.voices[0].preview_url == "https://audio.example/helena.wav"
    assert str(requests[0].url) == "https://api.eu.deepgram.com/v1/models"
    assert requests[0].headers["Authorization"] == "Token deepgram-key"
    legacy = await voice_catalog.get_connection_voices(
        "deepgram", 42, connection.uuid, model="aura-1"
    )
    assert [voice.voice_id for voice in legacy.voices] == ["aura-asteria-en"]


@pytest.mark.asyncio
async def test_deepgram_lists_flux_voices_from_the_v2_catalog(connection, upstream):
    # Flux voices are absent from /v1/models and Aura voices from /v2/models, so
    # a configuration whose voice is a Flux voice would otherwise see an empty
    # dropdown.
    connection.provider = "deepgram"
    connection.credentials = {"api_key": "deepgram-key"}
    connection.connection_settings = {"base_url": "https://api.eu.deepgram.com"}
    listings = {
        "/v1/models": {
            "stt": [],
            "tts": [
                {
                    "canonical_name": "aura-2-helena-en",
                    "name": "helena",
                    "architecture": "aura-2",
                    "languages": ["en"],
                }
            ],
        },
        "/v2/models": {
            "stt": [{"canonical_name": "flux-general-en", "architecture": "flux"}],
            "tts": [
                {
                    "canonical_name": "flux-maeve-en",
                    "name": "maeve",
                    "architecture": "flux-tts",
                    "languages": ["en", "en-US"],
                    "metadata": {
                        "display_name": "Maeve",
                        "accent": "Irish",
                        "tags": ["feminine", "warm"],
                        "sample": "https://audio.example/maeve.wav",
                    },
                }
            ],
        },
    }
    requests = upstream(
        lambda request: httpx.Response(200, json=listings[request.url.path])
    )

    flux = await voice_catalog.get_connection_voices(
        "deepgram", 42, connection.uuid, model="flux"
    )
    assert [voice.voice_id for voice in flux.voices] == ["flux-maeve-en"]
    assert flux.voices[0].name == "Maeve"
    assert flux.voices[0].accent == "Irish"
    assert flux.voices[0].gender == "female"
    assert flux.voices[0].preview_url == "https://audio.example/maeve.wav"
    assert [str(r.url) for r in requests] == ["https://api.eu.deepgram.com/v2/models"]
    assert requests[0].headers["Authorization"] == "Token deepgram-key"

    everything = await voice_catalog.get_connection_voices(
        "deepgram", 42, connection.uuid
    )
    assert [voice.voice_id for voice in everything.voices] == [
        "aura-2-helena-en",
        "flux-maeve-en",
    ]
    assert [r.url.path for r in requests[1:]] == ["/v1/models", "/v2/models"]


@pytest.mark.parametrize(
    "model,language,expected",
    [
        ("arcana", "en", ["celeste"]),
        ("mistv3", "de", ["german"]),
        ("arcana", "da", []),
        ("unknown", "en", []),
    ],
)
@pytest.mark.asyncio
async def test_rime_model_and_language_filtering_without_sending_secrets(
    model, language, expected, connection, upstream
):
    connection.provider = "rime"
    connection.connection_settings = {}
    requests = upstream(
        lambda request: httpx.Response(
            200,
            json={
                "arcana": {"eng": ["celeste"], "spa": ["alba"]},
                "mistv3": {"ger": ["german"], "eng": ["other"]},
            },
        )
    )
    response = await voice_catalog.get_connection_voices(
        "rime", 42, connection.uuid, model=model, language=language
    )
    assert [voice.voice_id for voice in response.voices] == expected
    assert str(requests[0].url) == "https://users.rime.ai/data/voices/all-v2.json"
    assert "Authorization" not in requests[0].headers
    assert "private-elevenlabs-key" not in str(requests[0].headers)


@pytest.mark.asyncio
async def test_sarvam_reuses_model_options_without_external_requests(
    connection, upstream
):
    from api.services.configuration.options.sarvam import (
        SARVAM_V3_VOICES,
        SARVAM_V4_VOICES,
    )

    connection.provider = "sarvam"
    connection.connection_settings = {}
    requests = upstream(lambda request: pytest.fail("Bulbul voice options are static"))
    for model, expected in [
        ("bulbul:v4-flash", SARVAM_V4_VOICES),
        ("bulbul:v3", SARVAM_V3_VOICES),
    ]:
        response = await voice_catalog.get_connection_voices(
            "sarvam", 42, connection.uuid, model=model
        )
        assert {voice.voice_id for voice in response.voices} == set(expected)
        assert all(voice.name for voice in response.voices)
        assert all(voice.description for voice in response.voices)
        assert all(voice.language for voice in response.voices)
        if model == "bulbul:v4-flash":
            shubh_voice = next(
                v for v in response.voices if v.voice_id == "shubh_enhi_ads"
            )
            assert shubh_voice.name == "Shubh"
            assert shubh_voice.description == "English-Hindi • Ads • shubh_enhi_ads"
            assert shubh_voice.accent is None
            assert shubh_voice.language == "en-hi"
            kyc_voice = next(
                v for v in response.voices if v.voice_id == "aparna_hi_kyc"
            )
            assert kyc_voice.name == "Aparna"
            assert kyc_voice.description == "Hindi • KYC • aparna_hi_kyc"
            assert kyc_voice.accent is None
            assert kyc_voice.language == "hi"
        elif model == "bulbul:v3":
            shubh_voice = next(v for v in response.voices if v.voice_id == "shubh")
            assert shubh_voice.name == "Shubh"
            assert shubh_voice.description == "Multilingual"
    assert requests == []


@pytest.mark.asyncio
async def test_sarvam_v4_voices_allow_cross_lingual_selection(connection, upstream):
    from api.services.configuration.options.sarvam import SARVAM_V4_VOICES

    connection.provider = "sarvam"
    connection.connection_settings = {}
    upstream(lambda request: pytest.fail("Bulbul voice options are static"))
    response = await voice_catalog.get_connection_voices(
        "sarvam", 42, connection.uuid, model="bulbul:v4-flash", language="pa"
    )
    # Sarvam Bulbul voices are cross-lingual; passing language must not restrict voice choice
    assert {v.voice_id for v in response.voices} == set(SARVAM_V4_VOICES)
    pa_voice = next(v for v in response.voices if v.voice_id == "anand_pa_conversation")
    assert pa_voice.language == "pa"
    hi_voice = next(v for v in response.voices if v.voice_id == "shubh_enhi_ads")
    assert hi_voice.language == "en-hi"


@pytest.mark.asyncio
async def test_only_dograh_uses_mps_without_a_byok_connection(client, mps_not_used):
    mps_not_used.side_effect = None
    mps_not_used.return_value = {
        "provider": "dograh",
        "voices": [{"voice_id": "dg:voice", "name": "Dograh voice"}],
        "facets": {"languages": ["en"]},
    }
    response = await client.get(
        "/user/configurations/voices/dograh", params={"language": "en"}
    )
    assert response.status_code == 200
    assert response.json()["voices"][0]["voice_id"] == "dg:voice"
    assert response.json()["facets"]["languages"] == ["en"]
    mps_not_used.assert_awaited_once_with(
        provider="dograh",
        model=None,
        language="en",
        q=None,
        gender=None,
        accent=None,
        organization_id=42,
        created_by="user-42",
    )


def test_dynamic_voice_fields_are_backed_by_catalog_adapters():
    from api.services.configuration.registry import REGISTRY, ServiceType
    from api.services.configuration.voice_catalog_adapters import VOICE_CATALOGS

    dynamic_providers = set()
    for provider, config in REGISTRY[ServiceType.TTS].items():
        voice = config.model_json_schema()["properties"].get("voice", {})
        if voice.get("voice_catalog"):
            dynamic_providers.add(provider.value)
    assert dynamic_providers == {
        "dograh",
        "elevenlabs",
        "cartesia",
        "deepgram",
        "rime",
        "sarvam",
    }
    assert dynamic_providers - {"dograh"} <= VOICE_CATALOGS.keys()


@pytest.mark.asyncio
async def test_provider_search_results_are_not_refiltered_by_display_fields(
    connection, upstream
):
    # Search may match labels and other metadata not included in VoiceInfo.
    upstream(
        lambda request: httpx.Response(
            200, json={"voices": [{"voice_id": "ready", "name": "Sofie"}]}
        )
    )
    response = await voice_catalog.get_connection_voices(
        "elevenlabs", 42, connection.uuid, q="conversational", accent="danish"
    )
    assert [voice.voice_id for voice in response.voices] == ["ready"]
