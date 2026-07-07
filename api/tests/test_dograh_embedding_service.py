"""Tests for the Dograh-managed embedding service and its correlation resolver."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.gen_ai.embedding.dograh_service import DograhEmbeddingService
from api.services.gen_ai.embedding.factory import resolve_embedding_correlation_id


def _service_with_fake_client(correlation_id):
    service = DograhEmbeddingService(
        db_client=None,
        api_key="sk-test",
        model_id="text-embedding-3-small",
        base_url=None,
        correlation_id=correlation_id,
    )
    create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])])
    )
    service.client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    return service, create


@pytest.mark.asyncio
async def test_dograh_embedding_forwards_v2_protocol_when_correlation_present():
    service, create = _service_with_fake_client("corr-123")

    await service.embed_texts(["hello"])

    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["input"] == ["hello"]
    assert kwargs["model"] == "text-embedding-3-small"
    assert kwargs["extra_body"] == {
        "metadata": {
            "correlation_id": "corr-123",
            "mps_billing_version": "2",
        }
    }


@pytest.mark.asyncio
async def test_dograh_embedding_sends_plain_without_correlation():
    service, create = _service_with_fake_client(None)

    await service.embed_texts(["hello"])

    create.assert_awaited_once()
    # No correlation id → no MPS metadata; MPS accepts plain calls.
    assert "extra_body" not in create.await_args.kwargs


def _fake_mps_client(*, minted="minted"):
    return SimpleNamespace(
        create_correlation_id=AsyncMock(return_value={"correlation_id": minted}),
    )


@pytest.mark.asyncio
async def test_resolve_correlation_mints_via_service_key(monkeypatch):
    fake = _fake_mps_client()
    monkeypatch.setattr(
        "api.services.mps_service_key_client.mps_service_key_client", fake
    )

    result = await resolve_embedding_correlation_id(service_key="sk-mps")

    assert result == "minted"
    fake.create_correlation_id.assert_awaited_once_with(service_key="sk-mps")


@pytest.mark.asyncio
async def test_resolve_correlation_no_service_key_returns_none(monkeypatch):
    fake = _fake_mps_client()
    monkeypatch.setattr(
        "api.services.mps_service_key_client.mps_service_key_client", fake
    )

    result = await resolve_embedding_correlation_id(service_key=None)

    assert result is None
    fake.create_correlation_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_correlation_swallows_errors(monkeypatch):
    fake = SimpleNamespace(
        create_correlation_id=AsyncMock(side_effect=RuntimeError("mps down")),
    )
    monkeypatch.setattr(
        "api.services.mps_service_key_client.mps_service_key_client", fake
    )

    # A transient MPS failure must not break embeddings — send without it.
    result = await resolve_embedding_correlation_id(service_key="sk-mps")

    assert result is None
