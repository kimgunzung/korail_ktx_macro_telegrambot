"""Encryption at rest for secrets stored in Redis (AES-256-GCM, pycryptodome).

The key comes from ``CARD_ENCRYPTION_KEY``; when it is not set, a random key is
generated once and kept in ``<DATA_DIR>/secret.key`` (mode 0600) so the data is
never written in clear text. Losing that file (or changing the env key) makes the
saved card and credentials unreadable; they must then be registered again.
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


def _key_file_path() -> str:
    return os.path.join(settings.DATA_DIR, "secret.key")


def _local_key_material() -> str:
    """Read or create the random key file used when CARD_ENCRYPTION_KEY is unset."""
    path = _key_file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            material = f.read().strip()
        if material:
            return material
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    material = os.urandom(32).hex()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(material)
    except FileExistsError:  # pragma: no cover - race with another process
        with open(path, "r", encoding="utf-8") as f:
            material = f.read().strip()
    return material


def _key() -> bytes:
    secret = settings.CARD_ENCRYPTION_KEY or _local_key_material()
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
