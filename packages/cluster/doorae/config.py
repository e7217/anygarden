"""Application configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_DB_DIR = Path.home() / ".doorae"
_DEFAULT_DB_URL = f"sqlite+aiosqlite:///{_DEFAULT_DB_DIR / 'doorae.db'}"
_DEFAULT_ROOM_FILES_DIR = _DEFAULT_DB_DIR / "room_files"
_DEFAULT_ARTIFACT_FILES_DIR = _DEFAULT_DB_DIR / "artifact_files"

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
    # #124 — Fernet symmetric key for encrypting MCP server credentials
    # at rest (``mcp_server_instances.env_values_encrypted``). Loaded
    # from ``DOORAE_MCP_SECRETS_KEY``. Empty string falls back to a
    # process-local ephemeral key when ``dev=True`` (with a loud
    # warning); production deployments MUST set this explicitly or the
    # MCPSecrets initializer will refuse to boot.
    mcp_secrets_key: str = ""
    # #197 — Embedded LiteLLM gateway. When enabled, doorae-server
    # supervises a ``litellm`` subprocess listening on
    # ``127.0.0.1:<llm_gateway_port>`` and exposes ``/api/v1/llm/*``
    # as a reverse proxy. See docs/design/12-llm-gateway.md.
    #
    # Off by default — the existing "agent calls upstream directly"
    # path remains canonical until Phase 5 flips per-agent wiring.
    llm_gateway_enabled: bool = False
    # Port the LiteLLM subprocess listens on (loopback only). Picked
    # above LiteLLM's default 4000 so local dev setups that already
    # have LiteLLM running don't collide with the embedded one.
    llm_gateway_port: int = 4001
    # Rendered config.yaml location. Empty string defaults to
    # ``~/.doorae/litellm.yaml`` — resolved lazily in the gateway
    # code so tests can point at a temp dir without poisoning home.
    llm_gateway_config_path: str = ""
    # #362 — Seconds the supervisor waits for the litellm subprocess
    # to report healthy at ``/health/liveliness`` before declaring
    # spawn a bust. Default 30s covers cold-start variance observed
    # on litellm 1.83.x (~12s on a warm dev box; can spike higher
    # on a cold disk or with a large ``model_list``). Operators on
    # constrained hardware can override via
    # ``DOORAE_LLM_GATEWAY_HEALTH_TIMEOUT_SEC``.
    llm_gateway_health_timeout_sec: float = 30.0
    # #364 — Path of the ``litellm`` binary the supervisor spawns.
    # Defaults to bare ``"litellm"`` (PATH lookup) which works when
    # the operator's ``uv tool install 'litellm[proxy]'`` is the
    # only litellm reachable via PATH.
    #
    # Why this knob exists: #355 added ``openhands-sdk`` to the
    # agent package, which transitively pulls a *bare* ``litellm``
    # (no ``[proxy]`` extras) into the monorepo venv. ``.venv/bin``
    # precedes ``~/.local/bin`` in PATH for ``uv run`` processes, so
    # ``which litellm`` resolves to the bare install — which dies
    # on ``import backoff`` because backoff is a ``[proxy]``-only
    # dep. The cluster venv can't carry ``litellm[proxy]`` itself
    # because the proxy extras pin ``fastapi==0.124.4`` while
    # cluster pins ``fastapi<0.120`` (incompatible).
    #
    # The clean fix is to let operators point the supervisor at a
    # *separate* litellm install with proxy extras (typically
    # ``$HOME/.local/bin/litellm`` from
    # ``uv tool install 'litellm[proxy]'``). Override via
    # ``DOORAE_LLM_GATEWAY_BINARY=/abs/path/to/litellm``.
    llm_gateway_binary: str = "litellm"
    # #246 — Disk-backed storage for room shared files. The DB only
    # keeps metadata + sha256; the original bytes live under
    # ``<room_files_dir>/<room_id>/<file_id>``. Kept outside the
    # DB so the default SQLite ``doorae.db`` stays compact as rooms
    # accumulate attachments. Resolved lazily so tests can redirect
    # it without touching ``$HOME``.
    room_files_dir: Path = _DEFAULT_ROOM_FILES_DIR
    # #290 — Sibling directory for agent-produced artifacts. Kept
    # separate from ``room_files_dir`` so the two flows can carry
    # divergent retention / quota policies later without mass-moving
    # files. Same on-disk layout: ``<artifact_files_dir>/<room_id>/<id>``.
    artifact_files_dir: Path = _DEFAULT_ARTIFACT_FILES_DIR
    # #277 — Public URL agents use to reach this cluster's MCP / REST
    # endpoints. Empty string falls back to
    # ``http://{reachable_host()}:{port}`` so loopback / single-host
    # dev setups need no extra config. Production deployments behind a
    # reverse proxy MUST set this explicitly
    # (e.g. ``https://chat.example.com``).
    cluster_external_url: str = ""

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

    def cluster_external_url_or_default(self) -> str:
        """URL agents target for cluster-side MCP and REST calls.

        When ``cluster_external_url`` is set (production / reverse
        proxy), it wins after a single trailing-slash trim — the
        ``/mcp/rpc`` suffix is appended at the consumer side and a
        residual slash would yield ``//mcp/rpc``. When empty, fall
        back to ``http://{reachable_host()}:{port}`` which works for
        loopback / single-host dev (#277).
        """
        if self.cluster_external_url:
            return self.cluster_external_url.rstrip("/")
        return f"http://{self.reachable_host()}:{self.port}"
