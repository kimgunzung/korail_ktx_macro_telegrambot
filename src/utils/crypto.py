"""Encryption at rest for card details stored in Redis (AES-256-GCM, pycryptodome).

The key comes from ``CARD_ENCRYPTION_KEY``; when it is not set, a key is derived
from the bot token so the data is never written in clear text, but operators are
expected to configure a dedicated secret (see README).
"""
import base64
import hashlib
import json
import os
from typing import Any

from Crypto.Cipher import AES

from config.settings import settings

_VERSION = b"v1"
_NONCE_BYTES = 12


class DecryptionError(ValueError):
    """Stored payload could not be decrypted (wrong key or corrupted data)."""


def _key() -> bytes:
    secret = settings.CARD_ENCRYPTION_KEY or f"derived:{settings.TELEGRAM_BOT_TOKEN}"
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt_json(data: Any) -> str:
    """Serialize ``data`` as JSON and return an opaque base64 token."""
    plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
    nonce = os.urandom(_NONCE_BYTES)
    cipher = AES.new(_key(), AES.MODE_GCM, nonce=nonce)
    cipher.update(_VERSION)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return base64.urlsafe_b64encode(_VERSION + nonce + tag + ciphertext).decode("ascii")


def decrypt_json(token: str) -> Any:
    """Inverse of :func:`encrypt_json`.

    Raises:
        DecryptionError: wrong key, tampered or malformed token
    """
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        version, raw = raw[: len(_VERSION)], raw[len(_VERSION):]
        if version != _VERSION:
            raise DecryptionError(f"unsupported payload version {version!r}")
        nonce, tag, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:_NONCE_BYTES + 16], raw[_NONCE_BYTES + 16:]
        cipher = AES.new(_key(), AES.MODE_GCM, nonce=nonce)
        cipher.update(_VERSION)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
        return json.loads(plaintext.decode("utf-8"))
    except DecryptionError:
        raise
    except Exception as e:
        raise DecryptionError(f"{type(e).__name__}: {e}") from e
