from api.schemas.telephony_config import (
    PapiVoipConfigurationRequest,
    TelephonyConfigurationCreateRequest,
    TelephonyConfigurationResponse,
)


def test_telephony_config_create_accepts_papi_voip_provider():
    request = TelephonyConfigurationCreateRequest.model_validate(
        {
            "name": "Papi Voip",
            "config": {
                "provider": "papi_voip",
                "api_key": "instance-key",
                "instance_id": "instance-123",
                "base_url": "https://api.papi.api.br",
                "from_numbers": ["5511999999999"],
            },
        }
    )

    assert isinstance(request.config, PapiVoipConfigurationRequest)
    assert request.config.provider == "papi_voip"
    assert request.config.instance_id == "instance-123"


def test_telephony_configuration_response_supports_papi_voip_payload():
    response = TelephonyConfigurationResponse.model_validate(
        {
            "papi_voip": {
                "provider": "papi_voip",
                "api_key": "********",
                "instance_id": "instance-123",
                "base_url": "https://api.papi.api.br",
                "from_numbers": ["5511999999999"],
            }
        }
    )

    assert response.papi_voip is not None
    assert response.papi_voip.provider == "papi_voip"
