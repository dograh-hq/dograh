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
