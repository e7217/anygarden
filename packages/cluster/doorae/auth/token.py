"""Agent API token generation and argon2 hashing."""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_PREFIX = "agt_"
_hasher = PasswordHasher()


def generate_token() -> str:
    """Return a new plaintext agent token with the ``agt_`` prefix."""
    return f"{_PREFIX}{secrets.token_urlsafe(48)}"


def hash_token(plaintext: str) -> str:
    """Hash *plaintext* with argon2id and return the encoded hash."""
    return _hasher.hash(plaintext)


def hash_agent_token(plaintext: str) -> tuple[str, str]:
    """Return ``(hash, lookup_hint)``.

    The hint is the first 12 characters (``agt_`` + 8 chars) so the DB
    can narrow down candidates before running argon2 verification.
    """
    hint = plaintext[:12]  # "agt_" + 8 chars
    hashed = _hasher.hash(plaintext)
    return hashed, hint


def verify_token_hash(plaintext: str, hashed: str) -> bool:
    """Return ``True`` if *plaintext* matches *hashed*, ``False`` otherwise."""
    try:
        return _hasher.verify(hashed, plaintext)
    except VerifyMismatchError:
        return False
