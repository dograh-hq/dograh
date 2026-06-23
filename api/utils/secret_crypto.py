"""At-rest encryption for org-provided secrets (WhatsApp/Langfuse keys, webhook creds).

A single Fernet primitive used two ways:
  - `encrypt_secret`/`decrypt_secret` for individual string fields living inside a
    shared JSON blob (e.g. WhatsApp api_key, Langfuse secret_key) — we cannot
    encrypt the whole blob because non-secret fields must stay readable/queryable.
  - `EncryptedJSON` TypeDecorator for a column whose ENTIRE value is secret
    (webhook ExternalCredentialModel.credential_data) — transparent to all callers.

Keys come from APP_SECRET_KEY (primary) then VOICELINK_PROVISION_KEY (fallback),
each a urlsafe-base64 32-byte Fernet key (`Fernet.generate_key()`). Using
MultiFernet means: new writes use the primary key, but values written under EITHER
key still decrypt — so prod (which already has VOICELINK_PROVISION_KEY set) gets
encryption immediately, and you can later add a dedicated APP_SECRET_KEY and rotate
without a re-encrypt pass.

Backward/forward compatible:
  - When NO key is set, both functions are pass-throughs (no crash) — a transitional
    state until a key is configured.
  - Encrypted values carry an "enc::v1::" envelope, so legacy PLAINTEXT values
    (written before this shipped) are detected and returned as-is on read.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from loguru import logger
from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

_PREFIX = "enc::v1::"
_KEY_ENVS = ("APP_SECRET_KEY", "VOICELINK_PROVISION_KEY")
_warned_unset = False


def _multifernet() -> Optional[MultiFernet]:
    """Build a MultiFernet from whichever keys are set (primary first). None if none."""
    global _warned_unset
    fernets = []
    for env in _KEY_ENVS:
        key = os.getenv(env)
        if not key:
            continue
        try:
            fernets.append(Fernet(key.encode() if isinstance(key, str) else key))
        except (ValueError, TypeError):
            logger.error(f"{env} is not a valid Fernet key — ignoring it")
    if not fernets:
        if not _warned_unset:
            logger.warning(
                "No APP_SECRET_KEY/VOICELINK_PROVISION_KEY set — org secrets are "
                "stored UNENCRYPTED at rest. Set a Fernet key to enable encryption."
            )
            _warned_unset = True
        return None
    return MultiFernet(fernets)


def is_encryption_enabled() -> bool:
    return _multifernet() is not None


def encrypt_secret(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a string field. Empty/None and already-encrypted values pass through.
    No key configured -> returns the plaintext unchanged (transitional no-op)."""
    if not plaintext:
        return plaintext
    if plaintext.startswith(_PREFIX):
        return plaintext  # already encrypted — idempotent
    mf = _multifernet()
    if mf is None:
        return plaintext
    return _PREFIX + mf.encrypt(plaintext.encode()).decode()


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    """Decrypt a string field. Legacy plaintext (no envelope) is returned as-is, so
    rows written before encryption shipped keep working."""
    if not value or not isinstance(value, str) or not value.startswith(_PREFIX):
        return value  # empty, non-string, or legacy plaintext
    mf = _multifernet()
    if mf is None:
        logger.error("Encrypted secret found but no key available to decrypt it")
        return value
    try:
        return mf.decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        logger.error("Secret failed to decrypt (key mismatch or tampered value)")
        return value


class EncryptedJSON(TypeDecorator):
    """A JSON column whose entire value is encrypted at rest.

    DDL-identical to ``JSON`` (impl=JSON) so swapping a column to this type needs
    NO migration. New writes are stored as an encrypted string; legacy rows written
    as a plain JSON object are detected on read and returned unchanged.
    """

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt_secret(json.dumps(value, separators=(",", ":")))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            decrypted = decrypt_secret(value)
            if decrypted is None:
                return None
            try:
                return json.loads(decrypted)
            except (ValueError, TypeError):
                logger.error("EncryptedJSON value did not parse after decrypt")
                return None
        # Legacy row stored as a JSON object (pre-encryption) — already a dict.
        return value
