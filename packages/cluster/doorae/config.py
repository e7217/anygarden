"""Application configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_DB_DIR = Path.home() / ".doorae"
_DEFAULT_DB_URL = f"sqlite+aiosqlite:///{_DEFAULT_DB_DIR / 'doorae.db'}"

# Bind addresses that listen on every interface aren't valid dial targets.
# When we build a URL for an agent to connect back to us, map these to a
# loopback address so a local agent can still reach the server.
_UNDIALABLE_HOSTS = frozenset({"0.0.0.0", "::", ""})


class DooraeSettings(BaseSettings):
    """Configuration loaded from ``DOORAE_*`` environment variables."""

    host: str = "127.0.0.1"
    port: int = 8000
    db_url: str = _DEFAULT_DB_URL
    jwt_secret: str = ""
    jwt_expire_hours: int = 24
    log_level: str = "INFO"
    dev: bool = False  # DOORAE_DEV=1 for development mode

    model_config = SettingsConfigDict(env_prefix="DOORAE_")

    def reachable_host(self) -> str:
        """A host string that a client can actually dial.

        Running with ``--host 0.0.0.0`` tells uvicorn to listen on every
        interface, but ``0.0.0.0`` is not a usable destination address.
        Falling back to ``127.0.0.1`` lets agents colocated with the
        server connect over loopback; remote machines should set
        ``DOORAE_HOST`` to the server's real hostname explicitly.
        """
        if self.host in _UNDIALABLE_HOSTS:
            return "127.0.0.1"
        return self.host
