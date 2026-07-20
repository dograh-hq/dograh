import pytest

from api.schemas.tool import TransferCallConfig


def test_transfer_call_destination_accepts_initial_context_template():
    config = TransferCallConfig(
        destination="{{initial_context.transfer_destination}}",
    )

    assert config.destination == "{{initial_context.transfer_destination}}"


def test_transfer_call_destination_accepts_provider_specific_literal():
    config = TransferCallConfig(destination="provider-specific-destination")

    assert config.destination == "provider-specific-destination"


def test_transfer_call_static_allows_empty_draft_destination():
    config = TransferCallConfig(destination_source="static", destination="")

    assert config.destination_source == "static"
    assert config.destination == ""


def test_transfer_call_dynamic_requires_resolver():
    with pytest.raises(ValueError, match="resolver is required"):
        TransferCallConfig(destination_source="dynamic", destination="")


def test_transfer_call_dynamic_accepts_resolver_without_destination():
    config = TransferCallConfig(
        destination_source="dynamic",
        destination="",
        resolver={
            "type": "http",
            "url": "https://crm.example.com/resolve-transfer",
        },
    )

    assert config.destination_source == "dynamic"
    assert config.destination == ""
    assert config.resolver is not None


def test_transfer_call_context_mapping_requires_mapping():
    with pytest.raises(ValueError, match="context_mapping is required"):
        TransferCallConfig(destination_source="context_mapping")


def test_transfer_call_context_mapping_accepts_unique_routes():
    config = TransferCallConfig(
        destination_source="context_mapping",
        context_mapping={
            "context_path": "qualified",
            "routes": [
                {"context_value": "yes", "destination": "sales"},
                {"context_value": "no", "destination": "support"},
            ],
            "fallback_destination": "source",
        },
    )

    assert config.context_mapping is not None
    assert config.context_mapping.routes[0].destination == "sales"


def test_transfer_call_context_mapping_rejects_blank_context_path():
    with pytest.raises(ValueError, match="context path cannot be blank"):
        TransferCallConfig(
            destination_source="context_mapping",
            context_mapping={
                "context_path": "   ",
                "routes": [{"context_value": "yes", "destination": "sales"}],
            },
        )


def test_transfer_call_context_mapping_rejects_duplicate_values_case_insensitively():
    with pytest.raises(ValueError, match="must be unique"):
        TransferCallConfig(
            destination_source="context_mapping",
            context_mapping={
                "context_path": "qualified",
                "routes": [
                    {"context_value": "Yes", "destination": "sales"},
                    {"context_value": "yes", "destination": "support"},
                ],
            },
        )
