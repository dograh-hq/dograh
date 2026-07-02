from api.schemas.tool import TransferCallConfig


def test_transfer_call_destination_accepts_initial_context_template():
    config = TransferCallConfig(
        destination="{{initial_context.transfer_destination}}",
    )

    assert config.destination == "{{initial_context.transfer_destination}}"


def test_transfer_call_destination_accepts_provider_specific_literal():
    config = TransferCallConfig(destination="provider-specific-destination")

    assert config.destination == "provider-specific-destination"
