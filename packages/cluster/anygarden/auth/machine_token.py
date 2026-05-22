"""Machine API token generation and argon2 hashing."""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_PREFIX = "mch_"
_hasher = PasswordHasher()


def generate_machine_token() -> str:
    """Generate a new machine token.  Returns plaintext (shown once)."""
    raw = secrets.token_urlsafe(32)
    return f"{_PREFIX}{raw}"


def hash_machine_token(plaintext: str) -> tuple[str, str]:
    """Return ``(hash, lookup_hint)``.

    The hint is the first 12 characters (``mch_`` + 8 chars) so the DB
    can narrow down candidates before running argon2 verification.
    """
    hint = plaintext[:12]  # "mch_" + 8 chars
    hashed = _hasher.hash(plaintext)
    return hashed, hint


def verify_machine_token_hash(plaintext: str, hashed: str) -> bool:
    """Return ``True`` if *plaintext* matches *hashed*, ``False`` otherwise."""
    try:
        return _hasher.verify(hashed, plaintext)
    except VerifyMismatchError:
        return False
