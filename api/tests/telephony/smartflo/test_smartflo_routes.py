"""API route tests for Smartflo integration."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient

from api.app import app


@pytest.mark.asyncio
async def test_smartflo_call_endpoint_validation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing agent_id
        resp = await client.post("/smartflo/call", json={"recipient_phone_number": "919999999999"})
        assert resp.status_code == 400
        assert "agent_id is required" in resp.text

        # Missing recipient_phone_number
        resp = await client.post("/smartflo/call", json={"agent_id": "agent_123"})
        assert resp.status_code == 400
        assert "recipient_phone_number is required" in resp.text


@pytest.mark.asyncio
async def test_smartflo_connect_endpoint_resolution():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Resolution via Query Parameters
        resp = await client.get(
            "/smartflo_connect?workflow_id=10&organization_id=1&workflow_run_id=20&callId=call_abc"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "/stream?token=20" in data["url"]
        assert data["ws_url"] == data["url"]

        # 2. Resolution via POST Body
        resp_post = await client.post(
            "/smartflo_connect",
            json={
                "workflow_id": 10,
                "organization_id": 1,
                "workflow_run_id": 20,
                "callId": "call_abc",
            },
        )
        assert resp_post.status_code == 200
        data_post = resp_post.json()
        assert "/stream?token=20" in data_post["url"]

        # 3. Resolution via Redis Cache lookup
        cached_state = {
            "workflow_id": 55,
            "organization_id": 2,
            "workflow_run_id": 88,
            "agent_id": "agent_99",
        }
        with patch(
            "api.services.telephony.providers.smartflo.routes.get_smartflo_call_state",
            new_callable=AsyncMock,
            return_value=cached_state,
        ):
            resp_redis = await client.get("/smartflo_connect?callId=call_cached_123")
            assert resp_redis.status_code == 200
            data_redis = resp_redis.json()
            assert "/stream?token=88" in data_redis["url"]


@pytest.mark.asyncio
async def test_smartflo_events_webhook():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "api.services.telephony.providers.smartflo.routes._process_status_update",
            new_callable=AsyncMock,
        ) as mock_status:
            resp = await client.post(
                "/smartflo/events",
                json={
                    "call_id": "c123",
                    "workflow_run_id": 101,
                    "status": "answered",
                    "caller_id": "918888888888",
                    "customer_number": "919999999999",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "received"
            mock_status.assert_awaited_once()
            callback_req = mock_status.await_args.args[1]
            assert callback_req.status == "answered"
            assert callback_req.call_id == "c123"
