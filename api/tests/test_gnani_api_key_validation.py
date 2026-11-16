import json
from types import SimpleNamespace

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus

from api.services.configuration.check_validity import UserConfigurationValidator

SECRET = "gnani-live-secret-value"


class _Socket:
    def __init__(self, message):
        self._message = message

    def recv(self, timeout=None):
        if isinstance(self._message, Exception):
            raise self._message
        return self._message

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def _status(code):
    return InvalidStatus(SimpleNamespace(status_code=code))


def _check(monkeypatch, message=None, error=None):
    def connect(*args, **kwargs):
        if error is not None:
            raise error
        return _Socket(message)

    monkeypatch.setattr(
        "api.services.configuration.check_validity.websocket_connect", connect
    )
    return UserConfigurationValidator()._check_gnani_api_key("prisma-v2.5", SECRET)


def test_connected_handshake_accepts_the_key(monkeypatch):
    seen = {}

    def connect(*args, **kwargs):
        seen.update(kwargs)
        return _Socket(json.dumps({"type": "connected"}))

    monkeypatch.setattr(
        "api.services.configuration.check_validity.websocket_connect", connect
    )
    assert (
        UserConfigurationValidator()._check_gnani_api_key("prisma-v2.5", SECRET)
        is True
    )
    assert seen["additional_headers"]["lang_code"] == "hi-IN"
    assert SECRET not in json.dumps(seen["additional_headers"]["lang_code"])


@pytest.mark.parametrize(
    ("error", "snippet"),
    [
        (_status(401), "Invalid Gnani API key"),
        (_status(403), "does not have permission"),
        (_status(429), "rate limited"),
        (_status(500), "could not complete"),
        (_status(404), "credential check failed"),
        (TimeoutError(), "timed out"),
        (ConnectionClosed(None, None), "Could not connect"),
        (OSError("down"), "Could not connect"),
    ],
)
def test_handshake_failures_do_not_leak_the_key(monkeypatch, error, snippet):
    with pytest.raises(ValueError) as caught:
        _check(monkeypatch, error=error)
    message = str(caught.value)
    assert snippet in message
    assert SECRET not in message
    assert "Invalid Gnani API key" not in message or snippet == "Invalid Gnani API key"


def test_malformed_handshake_fails_without_the_body(monkeypatch):
    with pytest.raises(ValueError) as caught:
        _check(monkeypatch, message="not-json")
    assert SECRET not in str(caught.value)
    assert "not-json" not in str(caught.value)


def test_missing_connected_type_fails(monkeypatch):
    with pytest.raises(ValueError, match="credential check failed"):
        _check(monkeypatch, message=json.dumps({"type": "error"}))


def test_blank_key_is_rejected_before_the_network(monkeypatch):
    def connect(*args, **kwargs):
        raise AssertionError("blank keys must not open a socket")

    monkeypatch.setattr(
        "api.services.configuration.check_validity.websocket_connect", connect
    )
    with pytest.raises(ValueError, match="required"):
        UserConfigurationValidator()._check_gnani_api_key("prisma-v2.5", "  ")
