"""Tests for PapiVoip configuration schemas."""

import pytest
from pydantic import ValidationError

from api.services.telephony.providers.papi_voip.config import (
    PapiVoipConfigurationRequest,
    PapiVoipConfigurationResponse,
)


def test_valid_papi_voip_configuration():
    config = PapiVoipConfigurationRequest(
        api_key="secret-key",
        instance_id="inst-12345",
        base_url="https://api.papi.api.br",
        webhook_secret="whsec_123",
        from_numbers=["5511999999999"],
    )
    assert config.api_key == "secret-key"
    assert config.instance_id == "inst-12345"
    assert config.base_url == "https://api.papi.api.br"
    assert config.webhook_secret == "whsec_123"
    assert config.from_numbers == ["5511999999999"]


def test_invalid_base_url_scheme():
    with pytest.raises(ValidationError):
        PapiVoipConfigurationRequest(
            api_key="secret-key",
            instance_id="inst-12345",
            base_url="http://api.papi.api.br",  # HTTP rejected, must be HTTPS
        )


def test_invalid_base_url_no_hostname():
    with pytest.raises(ValidationError):
        PapiVoipConfigurationRequest(
            api_key="secret-key",
            instance_id="inst-12345",
            base_url="https://",
        )


def test_response_schema():
    resp = PapiVoipConfigurationResponse(
        provider="papi_voip",
        base_url="https://api.papi.api.br",
        api_key="secr...masked",
        instance_id="inst-12345",
        webhook_secret="whse...masked",
        from_numbers=["5511999999999"],
    )
    assert resp.provider == "papi_voip"
    assert resp.instance_id == "inst-12345"
