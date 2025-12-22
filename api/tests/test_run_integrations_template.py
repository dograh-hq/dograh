"""Tests for webhook execution in run_integrations.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.tasks.run_integrations import (
    _build_auth_header,
    _build_render_context,
    _execute_webhook_node,
)


@pytest.fixture(autouse=True)
def mock_logger():
    """Mock the logger for all tests."""
    with patch("api.tasks.run_integrations.logger") as mock_log:
        mock_log.bind.return_value = mock_log
        yield mock_log


class TestBuildAuthHeader:
    """Tests for _build_auth_header function."""

    def test_bearer_token(self):
        """Test bearer token auth header."""
        credential = MagicMock()
        credential.credential_type = "bearer_token"
        credential.credential_data = {"token": "my-secret-token"}

        result = _build_auth_header(credential)
        assert result == {"Authorization": "Bearer my-secret-token"}

    def test_api_key(self):
        """Test API key auth header."""
        credential = MagicMock()
        credential.credential_type = "api_key"
        credential.credential_data = {"header_name": "X-API-Key", "api_key": "key123"}

        result = _build_auth_header(credential)
        assert result == {"X-API-Key": "key123"}

    def test_api_key_default_header(self):
        """Test API key with default header name."""
        credential = MagicMock()
        credential.credential_type = "api_key"
        credential.credential_data = {"api_key": "key123"}

        result = _build_auth_header(credential)
        assert result == {"X-API-Key": "key123"}

    def test_basic_auth(self):
        """Test basic auth header."""
        credential = MagicMock()
        credential.credential_type = "basic_auth"
        credential.credential_data = {"username": "user", "password": "pass"}

        result = _build_auth_header(credential)
        # base64 of "user:pass" is "dXNlcjpwYXNz"
        assert result == {"Authorization": "Basic dXNlcjpwYXNz"}

    def test_custom_header(self):
        """Test custom header auth."""
        credential = MagicMock()
        credential.credential_type = "custom_header"
        credential.credential_data = {
            "header_name": "X-Custom-Auth",
            "header_value": "custom-value",
        }

        result = _build_auth_header(credential)
        assert result == {"X-Custom-Auth": "custom-value"}

    def test_unknown_type(self):
        """Test unknown credential type returns empty dict."""
        credential = MagicMock()
        credential.credential_type = "unknown"
        credential.credential_data = {}

        result = _build_auth_header(credential)
        assert result == {}


class TestBuildRenderContext:
    """Tests for _build_render_context function."""

    def test_basic_context(self):
        """Test building render context from workflow run."""
        workflow_run = MagicMock()
        workflow_run.id = 123
        workflow_run.name = "WR-TEST-001"
        workflow_run.workflow_id = 456
        workflow_run.workflow.name = "Test Workflow"
        workflow_run.initial_context = {"phone_number": "+1234567890"}
        workflow_run.gathered_context = {
            "customer_name": "John",
            "mapped_call_disposition": "QUALIFIED",
        }
        workflow_run.usage_info = {"call_duration_seconds": 120}
        workflow_run.completed_at = None

        result = _build_render_context(workflow_run)

        assert result["workflow_run_id"] == 123
        assert result["workflow_run_name"] == "WR-TEST-001"
        assert result["workflow_id"] == 456
        assert result["workflow_name"] == "Test Workflow"
        assert result["initial_context"]["phone_number"] == "+1234567890"
        assert result["gathered_context"]["customer_name"] == "John"
        assert result["cost_info"]["call_duration_seconds"] == 120
        assert result["disposition_code"] == "QUALIFIED"

    def test_empty_contexts(self):
        """Test with empty/None contexts."""
        workflow_run = MagicMock()
        workflow_run.id = 1
        workflow_run.name = "Test"
        workflow_run.workflow_id = 1
        workflow_run.workflow.name = "Workflow"
        workflow_run.initial_context = None
        workflow_run.gathered_context = None
        workflow_run.usage_info = None
        workflow_run.completed_at = None

        result = _build_render_context(workflow_run)

        assert result["initial_context"] == {}
        assert result["gathered_context"] == {}
        assert result["cost_info"] == {}
        assert result["disposition_code"] is None


class TestExecuteWebhookNode:
    """Tests for _execute_webhook_node function."""

    @pytest.mark.asyncio
    async def test_disabled_webhook_skipped(self):
        """Test that disabled webhooks are skipped."""
        webhook_data = {"name": "Test Webhook", "enabled": False}

        result = await _execute_webhook_node(
            webhook_data=webhook_data,
            render_context={},
            organization_id=1,
        )

        assert result is True  # Returns True for skipped webhooks

    @pytest.mark.asyncio
    async def test_missing_url_returns_false(self):
        """Test that missing endpoint URL returns False."""
        webhook_data = {"name": "Test Webhook", "enabled": True, "endpoint_url": None}

        result = await _execute_webhook_node(
            webhook_data=webhook_data,
            render_context={},
            organization_id=1,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_successful_post_request(self):
        """Test successful POST webhook execution."""
        webhook_data = {
            "name": "CRM Sync",
            "enabled": True,
            "http_method": "POST",
            "endpoint_url": "https://api.example.com/webhook",
            "payload_template": {
                "call_id": "{{workflow_run_id}}",
                "phone": "{{initial_context.phone_number}}",
            },
        }

        render_context = {
            "workflow_run_id": 123,
            "initial_context": {"phone_number": "+1234567890"},
        }

        with patch("api.tasks.run_integrations.db_client") as mock_db:
            mock_db.get_credential_by_uuid = AsyncMock(return_value=None)

            with patch("api.tasks.run_integrations.httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.raise_for_status = MagicMock()

                mock_client_instance = AsyncMock()
                mock_client_instance.request = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                result = await _execute_webhook_node(
                    webhook_data=webhook_data,
                    render_context=render_context,
                    organization_id=1,
                )

                assert result is True

                # Verify the request was made correctly
                mock_client_instance.request.assert_called_once()
                call_kwargs = mock_client_instance.request.call_args[1]
                assert call_kwargs["method"] == "POST"
                assert call_kwargs["url"] == "https://api.example.com/webhook"
                assert call_kwargs["json"] == {
                    "call_id": "123",
                    "phone": "+1234567890",
                }

    @pytest.mark.asyncio
    async def test_webhook_with_credential(self):
        """Test webhook execution with credential auth."""
        webhook_data = {
            "name": "Authenticated Webhook",
            "enabled": True,
            "http_method": "POST",
            "endpoint_url": "https://api.example.com/webhook",
            "credential_uuid": "cred-123",
            "payload_template": {},
        }

        mock_credential = MagicMock()
        mock_credential.name = "API Key"
        mock_credential.credential_type = "bearer_token"
        mock_credential.credential_data = {"token": "secret-token"}

        with patch("api.tasks.run_integrations.db_client") as mock_db:
            mock_db.get_credential_by_uuid = AsyncMock(return_value=mock_credential)

            with patch("api.tasks.run_integrations.httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.raise_for_status = MagicMock()

                mock_client_instance = AsyncMock()
                mock_client_instance.request = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                result = await _execute_webhook_node(
                    webhook_data=webhook_data,
                    render_context={},
                    organization_id=1,
                )

                assert result is True

                # Verify auth header was included
                call_kwargs = mock_client_instance.request.call_args[1]
                assert call_kwargs["headers"]["Authorization"] == "Bearer secret-token"

    @pytest.mark.asyncio
    async def test_webhook_with_custom_headers(self):
        """Test webhook execution with custom headers."""
        webhook_data = {
            "name": "Custom Headers Webhook",
            "enabled": True,
            "http_method": "POST",
            "endpoint_url": "https://api.example.com/webhook",
            "custom_headers": [
                {"key": "X-Source", "value": "dograh"},
                {"key": "X-Workflow", "value": "test"},
            ],
            "payload_template": {},
        }

        with patch("api.tasks.run_integrations.db_client") as mock_db:
            mock_db.get_credential_by_uuid = AsyncMock(return_value=None)

            with patch("api.tasks.run_integrations.httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.raise_for_status = MagicMock()

                mock_client_instance = AsyncMock()
                mock_client_instance.request = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                result = await _execute_webhook_node(
                    webhook_data=webhook_data,
                    render_context={},
                    organization_id=1,
                )

                assert result is True

                # Verify custom headers were included
                call_kwargs = mock_client_instance.request.call_args[1]
                assert call_kwargs["headers"]["X-Source"] == "dograh"
                assert call_kwargs["headers"]["X-Workflow"] == "test"

    @pytest.mark.asyncio
    async def test_webhook_http_error(self):
        """Test webhook execution with HTTP error."""
        import httpx

        webhook_data = {
            "name": "Failing Webhook",
            "enabled": True,
            "http_method": "POST",
            "endpoint_url": "https://api.example.com/webhook",
            "payload_template": {},
        }

        with patch("api.tasks.run_integrations.db_client") as mock_db:
            mock_db.get_credential_by_uuid = AsyncMock(return_value=None)

            with patch("api.tasks.run_integrations.httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 500
                mock_response.text = "Internal Server Error"
                mock_response.raise_for_status = MagicMock(
                    side_effect=httpx.HTTPStatusError(
                        "Server Error",
                        request=MagicMock(),
                        response=mock_response,
                    )
                )

                mock_client_instance = AsyncMock()
                mock_client_instance.request = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                result = await _execute_webhook_node(
                    webhook_data=webhook_data,
                    render_context={},
                    organization_id=1,
                )

                assert result is False
