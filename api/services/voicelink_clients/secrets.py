"""Reversible encryption for the VoiceLink provisioning secret.

The user's platform password is only available in plaintext at signup. To let
a later admin "Create client" reuse the *same* password, we store a Fernet-
encrypted copy on the organization (``voicelink_provision_secret``), wiped on
successful provisioning.

The key comes from ``VOICELINK_PROVISION_KEY`` (a urlsafe-base64 32-byte Fernet
key, e.g. ``Fernet.generate_key()``). When the key is unset the util degrades to
a no-op: nothing is stored and ``decrypt`` yields ``None`` — the admin flow then
falls back to the password prompt instead of crashing.
"""

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

_KEY_ENV = "VOICELINK_PROVISION_KEY"
_warned_unset = False


def _fernet() -> Optional[Fernet]:
    global _warned_unset
    key = os.getenv(_KEY_ENV)
    if not key:
        if not _warned_unset:
            logger.warning(
                f"{_KEY_ENV} is unset — VoiceLink provisioning secrets are not "
                "stored; admin create falls back to the password prompt"
            )
            _warned_unset = True
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError):
        logger.error(f"{_KEY_ENV} is not a valid Fernet key — treating as unset")
        return None


def encrypt_provision_secret(plaintext: str) -> Optional[str]:
    """Encrypt the plaintext password. Returns ``None`` when no key is set."""
    fernet = _fernet()
    if fernet is None:
        return None
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_provision_secret(token: Optional[str]) -> Optional[str]:
    """Decrypt a stored secret. Returns ``None`` for empty input, missing key,
    or an undecryptable/tampered token."""
    if not token:
        return None
    fernet = _fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        logger.warning("VoiceLink provisioning secret failed to decrypt")
        return None
