"""Room-invite token helpers.

Same shape as ``doorae/auth/token.py`` (agent tokens). Split into a
separate module because the token prefix, hint slice, and audience
differ; keeping them together made callers easier to confuse.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Prefix lets routing code recognise the token class at a glance and
# keeps the lookup_hint namespace disjoint from ``agt_``/``mch_``.
_PREFIX = "inv_"
# 8 chars after the prefix is enough to narrow DB candidates down
# to a handful before argon2 runs — matches the AgentToken slice.
_HINT_LEN = 12  # "inv_" + 8 chars

_hasher = PasswordHasher()


def generate_invite_token() -> str:
    """Return a fresh plaintext invite token.

    32-byte urlsafe random ≈ 256 bits of entropy. The token is shown
    to the admin exactly once at creation time; the server stores
    only its hash.
    """
    return f"{_PREFIX}{secrets.token_urlsafe(32)}"


def hash_invite_token(plaintext: str) -> tuple[str, str]:
    """Return ``(argon2_hash, lookup_hint)`` for storage.

    The hint is the first ``_HINT_LEN`` characters — same pattern as
    ``hash_agent_token``. Callers that only need the hint for lookup
    (no storage) can slice ``plaintext[:12]`` themselves.
    """
    hint = plaintext[:_HINT_LEN]
    return _hasher.hash(plaintext), hint


def verify_invite_token(plaintext: str, hashed: str) -> bool:
    """Return ``True`` iff *plaintext* matches the stored *hashed*."""
    try:
        return _hasher.verify(hashed, plaintext)
    except VerifyMismatchError:
        return False
