from unittest.mock import patch

import pytest

from api.errors.failure import ErrorOwner, ErrorType
from api.services.telephony.providers.twilio.strategies import TwilioHangupStrategy


@pytest.mark.asyncio
async def test_hangup_missing_credentials_is_user_owned_config_error():
    with patch(
        "api.services.telephony.providers.twilio.strategies.log_failure"
    ) as log_failure:
        result = await TwilioHangupStrategy().execute_hangup(
            {
                "auth_token": "secret",
                "call_sid": "CA123",
            }
        )

    assert result is False
    log_failure.assert_called_once()
    failure = log_failure.call_args.args[0]
    assert log_failure.call_args.kwargs == {"operation": "hang up call"}
    assert failure.type == ErrorType.CONFIG_ERROR
    assert failure.error_owner == ErrorOwner.USER
    assert failure.code == "twilio-missing-hangup-credentials"
    assert failure.external_message == (
        "Check the Twilio credentials configured for this call."
    )


@pytest.mark.asyncio
async def test_hangup_missing_call_sid_is_operator_owned_system_error():
    with patch(
        "api.services.telephony.providers.twilio.strategies.log_failure"
    ) as log_failure:
        result = await TwilioHangupStrategy().execute_hangup(
            {
                "account_sid": "AC123",
                "auth_token": "secret",
                "call_sid": "",
            }
        )

    assert result is False
    log_failure.assert_called_once()
    failure = log_failure.call_args.args[0]
    assert log_failure.call_args.kwargs == {"operation": "hang up call"}
    assert failure.type == ErrorType.SYSTEM_ERROR
    assert failure.error_owner == ErrorOwner.OPERATOR
    assert failure.code == "twilio-missing-call-sid"
    assert failure.external_message == (
        "Dograh could not identify the active Twilio call. Please retry or contact "
        "support if the problem continues."
    )
