"""At-rest secret encryption: round-trip, legacy passthrough, no-key no-op, rotation."""

import json

import pytest
from cryptography.fernet import Fernet

from api.services.configuration.masking import mask_key
from api.utils import secret_crypto
from api.utils.secret_crypto import (
    EncryptedJSON,
    decrypt_secret,
    encrypt_secret,
    is_encryption_enabled,
)

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", KEY_A)
    monkeypatch.delenv("VOICELINK_PROVISION_KEY", raising=False)


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    monkeypatch.delenv("VOICELINK_PROVISION_KEY", raising=False)


def test_round_trip_and_envelope(with_key):
    enc = encrypt_secret("super-secret-key")
    assert enc.startswith("enc::v1::")
    assert "super-secret-key" not in enc  # actually ciphertext, not plaintext
    assert decrypt_secret(enc) == "super-secret-key"
    assert is_encryption_enabled() is True


def test_encrypt_is_idempotent(with_key):
    once = encrypt_secret("k")
    twice = encrypt_secret(once)  # already encrypted -> unchanged
    assert once == twice
    assert decrypt_secret(twice) == "k"


def test_empty_and_none_passthrough(with_key):
    assert encrypt_secret("") == ""
    assert encrypt_secret(None) is None
    assert decrypt_secret("") == ""
    assert decrypt_secret(None) is None


def test_legacy_plaintext_decrypt_passthrough(with_key):
    # A value written before encryption shipped has no envelope -> returned as-is.
    assert decrypt_secret("legacy-plaintext-key") == "legacy-plaintext-key"


def test_no_key_is_noop(no_key):
    assert is_encryption_enabled() is False
    enc = encrypt_secret("k")
    assert enc == "k"  # passthrough, no envelope
    assert decrypt_secret(enc) == "k"


def test_masking_interplay(with_key):
    # GET path: decrypt then mask must equal masking the original plaintext.
    plaintext = "abcd1234efgh5678"
    stored = encrypt_secret(plaintext)
    assert mask_key(decrypt_secret(stored)) == mask_key(plaintext)


def test_multifernet_rotation(monkeypatch):
    # Write under KEY_B only (as VOICELINK_PROVISION_KEY)...
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    monkeypatch.setenv("VOICELINK_PROVISION_KEY", KEY_B)
    stored = encrypt_secret("rotate-me")
    # ...then add KEY_A as the new primary. Old value must still decrypt.
    monkeypatch.setenv("APP_SECRET_KEY", KEY_A)
    assert decrypt_secret(stored) == "rotate-me"


def test_encrypted_json_round_trip(with_key):
    col = EncryptedJSON()
    data = {"token": "bearer-xyz", "header_name": "X-API-Key"}
    bound = col.process_bind_param(data, None)
    assert isinstance(bound, str) and bound.startswith("enc::v1::")
    # Simulate the JSON column round-trip: bound string is stored/returned verbatim.
    assert col.process_result_value(bound, None) == data


def test_encrypted_json_legacy_dict_passthrough(with_key):
    col = EncryptedJSON()
    legacy = {"password": "p"}  # pre-encryption row came back as a dict
    assert col.process_result_value(legacy, None) == legacy


def test_encrypted_json_none(with_key):
    col = EncryptedJSON()
    assert col.process_bind_param(None, None) is None
    assert col.process_result_value(None, None) is None


def test_encrypted_json_noop_without_key(no_key):
    col = EncryptedJSON()
    data = {"a": 1}
    bound = col.process_bind_param(data, None)
    # No key -> stored as plain JSON string, still round-trips back to the dict.
    assert json.loads(bound) == data
    assert col.process_result_value(bound, None) == data
