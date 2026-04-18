"""Fernet-backed at-rest encryption for MCP instance credentials (#124).

Tiny wrapper around :class:`cryptography.fernet.Fernet` that:

1. validates the configured key at init time (loud fail if the key is
   malformed — we'd rather refuse to boot than discover the problem
   mid-request);
2. serialises a plain dict as JSON before encrypting and parses it
   back on decrypt, so the caller only deals in Python dicts;
3. exposes a ``from_config`` classmethod that handles the dev-mode
   ephemeral fallback in a single place — prod boot stays strict.

Why a class and not module-level functions: wiring the Fernet
instance onto ``app.state`` keeps tests from having to monkey-patch
environment variables just to rotate the key, and the DI style
matches :class:`SkillLibraryService`.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


logger = logging.getLogger(__name__)


class MCPSecretsUnavailable(RuntimeError):
    """Raised when a key is required but the deployment provided none.

    Distinct from :class:`ValueError` (malformed key) so callers can
    tell "you forgot to configure a key" from "the key you set is
    garbage" and point operators at the right line in the docs.
    """


class MCPSecrets:
    """Fernet wrapper with dict convenience + loud init failure."""

    def __init__(self, key: str | bytes) -> None:
        # Accept str or bytes — .env files typically store the key as a
        # urlsafe-base64 ASCII string, but rotating via a script may
        # pass raw bytes. Fernet itself accepts either.
        if isinstance(key, str):
            key = key.encode("ascii")
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            # Fernet raises either ValueError (bad base64 / wrong length)
            # or binascii.Error (a TypeError subclass). Normalise to
            # ValueError so callers can catch one type.
            raise ValueError(
                "DOORAE_MCP_SECRETS_KEY is not a valid Fernet key "
                "(must be urlsafe-base64 of 32 random bytes, i.e. "
                "Fernet.generate_key() output)."
            ) from exc

    # ── Factories ──────────────────────────────────────────────────

    @classmethod
    def from_config_key(
        cls,
        key: str,
        *,
        dev_mode: bool = False,
    ) -> "MCPSecrets":
        """Build an :class:`MCPSecrets` from the configured key string.

        Production (``dev_mode=False``) refuses to proceed without an
        explicit key — an empty string raises
        :class:`MCPSecretsUnavailable` so boot fails loudly rather
        than silently encrypting with a predictable value.

        Development mode generates an ephemeral key and warns; restart
        will regenerate, so any existing encrypted rows become
        undecryptable. This matches the behaviour of other "dev
        secrets" in the codebase (JWT fallback, etc.).
        """
        if not key:
            if not dev_mode:
                raise MCPSecretsUnavailable(
                    "DOORAE_MCP_SECRETS_KEY is unset. Set it to the "
                    "output of ``Fernet.generate_key()`` (urlsafe-"
                    "base64 of 32 random bytes) before starting the "
                    "cluster in production. MCP credentials are "
                    "stored encrypted and refuse to load without a "
                    "configured key."
                )
            ephemeral = Fernet.generate_key()
            logger.warning(
                "mcp_secrets.ephemeral_key_generated "
                "dev_mode=True — MCP credentials encrypted with a "
                "process-local key. Attached instances will become "
                "undecryptable on next restart. Set "
                "DOORAE_MCP_SECRETS_KEY to persist."
            )
            return cls(ephemeral)
        return cls(key)

    # ── Core API ──────────────────────────────────────────────────

    def encrypt_dict(self, values: dict[str, str]) -> bytes:
        """Return Fernet ciphertext for ``json.dumps(values)``."""
        payload = json.dumps(values, sort_keys=True, ensure_ascii=False)
        return self._fernet.encrypt(payload.encode("utf-8"))

    def decrypt_dict(self, token: Optional[bytes]) -> dict[str, str]:
        """Inverse of :meth:`encrypt_dict`.

        Accepts ``None`` / empty token as "no credentials stored"
        and returns an empty dict — simpler than forcing the caller
        to branch on NULL every time.
        """
        if token is None or len(token) == 0:
            return {}
        try:
            plain = self._fernet.decrypt(token)
        except InvalidToken as exc:
            # Most likely cause: key rotated without re-encrypting
            # existing rows. Surface a named exception so the service
            # layer can give the admin a targeted error message.
            raise ValueError(
                "Failed to decrypt MCP credentials — the Fernet key "
                "may have been rotated since this instance was "
                "attached. Re-enter the credentials via the admin "
                "UI to refresh."
            ) from exc
        return json.loads(plain.decode("utf-8"))
