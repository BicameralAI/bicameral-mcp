"""Fernet encryption for OAuth tokens at rest.

Key sourced from `BICAMERAL_TEAM_SERVER_SECRET_KEY` env var (urlsafe-base64
Fernet key). Operator generates via `python -c "from cryptography.fernet
import Fernet; print(Fernet.generate_key().decode())"` at install time.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet

ENV_KEY = "BICAMERAL_TEAM_SERVER_SECRET_KEY"


def encrypt_token(plaintext: str, key: bytes) -> bytes:
    return Fernet(key).encrypt(plaintext.encode("utf-8"))


def decrypt_token(ciphertext: bytes, key: bytes) -> str:
    return Fernet(key).decrypt(ciphertext).decode("utf-8")


def load_key_from_env() -> bytes:
    value = os.environ.get(ENV_KEY, "").strip()
    if not value:
        raise RuntimeError(f"{ENV_KEY} env var is required (Fernet urlsafe-base64 key)")
    return value.encode("utf-8")
