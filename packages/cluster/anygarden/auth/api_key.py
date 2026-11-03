"""Personal API key generation, hashing and scopes (#988).

A personal API key lets a script or service outside the browser act as
the person who issued it. It follows the ``agt_``/``mch_`` token shape:
the plaintext is shown once, only the argon2 hash and a 12-char
``lookup_hint`` are stored.

Scopes are cumulative: ``write`` includes ``read`` and ``admin``
includes ``write``. Only an ``admin`` key carries the person's admin
rights; any other key acts as a non-admin even for an admin account.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

API_KEY_PREFIX = "agu_"
_hasher = PasswordHasher()

SCOPE_READ = "read"
SCOPE_WRITE = "write"
SCOPE_ADMIN = "admin"
SCOPES = (SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN)
_RANK = {scope: rank for rank, scope in enumerate(SCOPES)}


def generate_api_key() -> str:
    """Return a new plaintext personal API key with the ``agu_`` prefix."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_api_key(plaintext: str) -> tuple[str, str]:
    """Return ``(hash, lookup_hint)``; the hint is ``agu_`` + 8 chars."""
    return _hasher.hash(plaintext), plaintext[:12]


def verify_api_key_hash(plaintext: str, hashed: str) -> bool:
    """Return ``True`` if *plaintext* matches *hashed*, ``False`` otherwise."""
    try:
        return _hasher.verify(hashed, plaintext)
    except VerifyMismatchError:
        return False


def scope_allows(granted: str, required: str) -> bool:
    """Return whether a key with *granted* scope may do *required* work."""
    return _RANK.get(granted, -1) >= _RANK[required]
