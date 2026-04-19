"""Tests for MCP Fernet key persistence across restarts (Issue #138).

The lifespan wiring has three possible sources for the Fernet key,
in priority order:

1. ``DOORAE_MCP_SECRETS_KEY`` env / ``config.mcp_secrets_key`` —
   explicit operator configuration (K8s secret, CI var, etc.).
2. ``~/.doorae/mcp_secrets_key`` file — JWT-secret-style auto
   persistence so local dev survives restarts without any setup.
3. ``config.dev`` flag + ephemeral ``Fernet.generate_key()`` —
   dev-only fallback. With dev disabled, booting without a key
   raises ``MCPSecretsUnavailable`` instead of silently generating
   a throwaway key that orphans previously-attached MCP instances
   on the next restart.

ASGITransport doesn't trigger lifespan, so each test drives
``create_app(...).router.lifespan_context(app)`` directly.
"""

from __future__ import annotations

import secrets as _secrets
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from doorae.app import create_app
from doorae.config import DooraeSettings
from doorae.mcp_templates.encryption import MCPSecretsUnavailable


def _fresh_config(**overrides) -> DooraeSettings:
    """Build a DooraeSettings without ``mcp_secrets_key``.

    The project conftest.py pre-fills ``mcp_secrets_key`` for every
    other test so the MCP layer boots cleanly. That convenience
    masks the behaviours we exercise here, so this helper builds
    a fresh settings instance from scratch.
    """
    base: dict = {
        "db_url": "sqlite+aiosqlite://",
        "jwt_secret": _secrets.token_urlsafe(32),
        "log_level": "WARNING",
    }
    base.update(overrides)
    return DooraeSettings(**base)


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch) -> Path:
    """Redirect ``Path.home()`` so the persistence file lands in tmp."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


async def _boot(config: DooraeSettings):
    """Run the lifespan manually and return the booted app."""
    app = create_app(config)
    async with app.router.lifespan_context(app):
        yield app  # type: ignore[misc]


@pytest.mark.asyncio
async def test_explicit_env_key_wins_over_file(tmp_home: Path) -> None:
    """Source 1 beats source 2 — explicit config is authoritative."""
    # Seed a decoy file so we can prove it was not read.
    doorae_dir = tmp_home / ".doorae"
    doorae_dir.mkdir()
    decoy_key = Fernet.generate_key().decode("ascii")
    (doorae_dir / "mcp_secrets_key").write_text(decoy_key)

    real_key = Fernet.generate_key().decode("ascii")
    config = _fresh_config(mcp_secrets_key=real_key)
    app = create_app(config)
    async with app.router.lifespan_context(app):
        svc = app.state.mcp_template_service
        assert svc is not None
        # Round-trip a payload with the explicit key — decrypt succeeds
        # only if the service is using ``real_key``, not the decoy.
        token = Fernet(real_key.encode()).encrypt(b'{"a":"b"}')
        assert svc._secrets.decrypt_dict(token) == {"a": "b"}


@pytest.mark.asyncio
async def test_file_fallback_persists_across_restarts(tmp_home: Path) -> None:
    """Source 2 — missing config creates a file, restart reads it back."""
    key_path = tmp_home / ".doorae" / "mcp_secrets_key"
    assert not key_path.exists()

    # First boot: no config, no file → file is created.
    config1 = _fresh_config()
    app1 = create_app(config1)
    async with app1.router.lifespan_context(app1):
        pass

    assert key_path.exists(), "file fallback must materialize a key on first boot"
    persisted = key_path.read_text().strip()
    # File mode 0o600 — credentials protection parity with jwt_secret.
    assert (key_path.stat().st_mode & 0o777) == 0o600

    # Second boot: file exists → the same key is reused.
    config2 = _fresh_config()
    app2 = create_app(config2)
    async with app2.router.lifespan_context(app2):
        svc = app2.state.mcp_template_service
        assert svc is not None
        token = Fernet(persisted.encode()).encrypt(b'{"a":"b"}')
        assert svc._secrets.decrypt_dict(token) == {"a": "b"}


def _block_mcp_key_file(tmp_home: Path) -> None:
    """Leave ``~/.doorae`` writable (JWT needs it) but make the
    ``mcp_secrets_key`` path itself a directory so ``write_text``
    raises ``IsADirectoryError``. Exercises the OSError branch in
    the file fallback without breaking JWT's own file handling.
    """
    (tmp_home / ".doorae").mkdir()
    (tmp_home / ".doorae" / "mcp_secrets_key").mkdir()


@pytest.mark.asyncio
async def test_dev_mode_allows_ephemeral_when_file_write_fails(
    tmp_home: Path,
) -> None:
    """Source 3 — ``dev=True`` falls back to an ephemeral key when the
    persistence file can't be written.

    We exercise the OSError branch by pre-creating
    ``mcp_secrets_key`` as a directory so ``write_text`` fails. The
    lifespan catches the error and feeds an empty key to
    ``from_config_key(..., dev_mode=True)``, which generates an
    ephemeral key and logs a warning.
    """
    _block_mcp_key_file(tmp_home)

    config = _fresh_config(dev=True)
    app = create_app(config)
    async with app.router.lifespan_context(app):
        svc = app.state.mcp_template_service
        assert svc is not None
        # Round-trips its own ciphertext (ephemeral key identity).
        token = svc._secrets.encrypt_dict({"k": "v"})
        assert svc._secrets.decrypt_dict(token) == {"k": "v"}


@pytest.mark.asyncio
async def test_production_refuses_boot_without_key_or_file(
    tmp_home: Path,
) -> None:
    """Source 3 gated — ``dev=False`` + no env + no writable file = hard fail.

    Previously the lifespan forced ``dev_mode=True`` unconditionally,
    silently minting a throwaway key on every restart and orphaning
    any MCP credentials attached in the prior process. This test
    pins the fix: a production boot without a configured key must
    raise ``MCPSecretsUnavailable`` so the operator sees the problem
    immediately instead of on the first post-restart MCP call.
    """
    _block_mcp_key_file(tmp_home)  # block file fallback

    config = _fresh_config(dev=False)
    app = create_app(config)
    with pytest.raises(MCPSecretsUnavailable):
        async with app.router.lifespan_context(app):
            pass
