"""Symmetric encryption for user credentials and console tokens.

The master key lives outside PostgreSQL in a mounted secret file. Ciphertexts
are bound to an associated-data string (cloud and CloudStack user identifiers
for credentials, lease identifier for console tokens), so moving a ciphertext
to another user does not decrypt successfully.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from conductor.errors import ConfigurationError


def _decode_key(raw: str) -> bytes:
    text = raw.strip()
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(text + "=" * (-len(text) % 4))
        except Exception:
            continue
        if len(decoded) == 32:
            return decoded
    try:
        decoded = bytes.fromhex(text)
    except ValueError:
        decoded = b""
    if len(decoded) == 32:
        return decoded
    # Any sufficiently long passphrase is normalised to a 32-byte key. This is
    # for operator convenience with generated random keys; a short passphrase
    # is rejected.
    if len(text) >= 32:
        return hashlib.sha256(text.encode("utf-8")).digest()
    raise ConfigurationError("the vault master key must encode at least 32 bytes")


class Vault:
    """AES-256-GCM with versioned envelopes."""

    prefix = "v1"

    def __init__(self, master_key: str) -> None:
        key = _decode_key(master_key)
        self._cipher = AESGCM(key)
        self.key_id = hashlib.sha256(key).hexdigest()[:12]

    def encrypt(self, plaintext: str, *, aad: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return ".".join(
            (
                self.prefix,
                self.key_id,
                _b64(nonce),
                _b64(ciphertext),
            )
        )

    def decrypt(self, envelope: str, *, aad: str) -> str:
        try:
            version, key_id, nonce, ciphertext = envelope.split(".")
            if version != self.prefix:
                raise ConfigurationError("unsupported vault envelope version")
            if key_id != self.key_id:
                raise ConfigurationError("the vault key does not match this ciphertext")
            plaintext = self._cipher.decrypt(_unb64(nonce), _unb64(ciphertext), aad.encode("utf-8"))
        except (ValueError, InvalidTag) as exc:
            raise ConfigurationError("stored credential could not be decrypted") from exc
        return plaintext.decode("utf-8")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def new_launch_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def credential_aad(cloud_id: str, cloudstack_user_id: str) -> str:
    return f"cloudstack-credential:{cloud_id}:{cloudstack_user_id}"


def console_aad(lease_id: str) -> str:
    return f"console-endpoint:{lease_id}"


def internal_token_aad() -> str:
    return "conductor-internal-token"
