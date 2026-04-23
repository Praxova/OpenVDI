"""Application-layer encryption for secrets persisted to Postgres.

Fernet (AES-128-CBC + HMAC-SHA256) with a single symmetric key loaded
from the OPENVDI_ENCRYPTION_KEY environment variable. Used today for
clusters.token_secret; any future SecretStr column that needs to land
in the DB should go through encrypt_secret / decrypt_secret as well.

Key rotation, multi-key support, and keyring abstractions are out of
scope for v0 — one key, one purpose. Generate once with
`python -m app.crypto generate-key` and paste into .env.
"""
from __future__ import annotations

import functools
import sys

from cryptography.fernet import Fernet

from app.config import get_settings


@functools.lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """Construct the module's Fernet instance from settings.

    Raises RuntimeError if OPENVDI_ENCRYPTION_KEY is empty. Any malformed
    key surfaces the cryptography library's own error (descriptive enough).
    """
    key = get_settings().openvdi_encryption_key.get_secret_value()
    if not key:
        raise RuntimeError("OPENVDI_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def encrypt_secret(plaintext: str) -> str:
    """Return URL-safe base64 Fernet ciphertext of `plaintext`.

    Empty input is allowed — the M2 seed row stores an empty ciphertext
    that gets overwritten via PUT /clusters/{id} once the broker is up.
    """
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Inverse of encrypt_secret.

    Empty `ciphertext` returns `""` (symmetric with encrypt_secret).
    Tampered ciphertext or wrong key raises
    cryptography.fernet.InvalidToken; callers let it propagate.
    """
    if ciphertext == "":
        return ""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


def generate_key() -> str:
    """Fresh Fernet key as a str. Pure (no I/O, no env reads)."""
    return Fernet.generate_key().decode()


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "generate-key":
        print(generate_key())
    else:
        print("usage: python -m app.crypto generate-key", file=sys.stderr)
        sys.exit(2)
