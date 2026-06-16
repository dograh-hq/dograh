"""Tests for the reversible VoiceLink provisioning-secret util.

The signup password is stored Fernet-encrypted (key from
``VOICELINK_PROVISION_KEY``) so a later admin "Create client" can reuse the
*same* platform password. With no key configured the util degrades to a
no-op so the feature falls back to the password prompt instead of crashing.
"""

import pytest
from cryptography.fernet import Fernet

from api.services.voicelink_clients.secrets import (
    decrypt_provision_secret,
    encrypt_provision_secret,
)

KEY = Fernet.generate_key().decode()


def test_encrypt_then_decrypt_round_trips(monkeypatch):
    monkeypatch.setenv("VOICELINK_PROVISION_KEY", KEY)

    token = encrypt_provision_secret("platform-pass-123")

    assert token is not None
    assert token != "platform-pass-123"  # actually encrypted at rest
    assert decrypt_provision_secret(token) == "platform-pass-123"


def test_decrypt_none_returns_none(monkeypatch):
    monkeypatch.setenv("VOICELINK_PROVISION_KEY", KEY)
    assert decrypt_provision_secret(None) is None


def test_no_key_encrypt_is_noop(monkeypatch):
    monkeypatch.delenv("VOICELINK_PROVISION_KEY", raising=False)
    assert encrypt_provision_secret("platform-pass-123") is None


def test_no_key_decrypt_returns_none(monkeypatch):
    monkeypatch.delenv("VOICELINK_PROVISION_KEY", raising=False)
    # A token that was valid under some key is undecryptable without the key.
    assert decrypt_provision_secret("gAAAA-not-decryptable") is None


def test_decrypt_invalid_token_returns_none(monkeypatch):
    monkeypatch.setenv("VOICELINK_PROVISION_KEY", KEY)
    assert decrypt_provision_secret("not-a-valid-fernet-token") is None
