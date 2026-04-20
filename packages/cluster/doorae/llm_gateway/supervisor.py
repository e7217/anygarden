"""LiteLLM subprocess lifecycle (#197).

The supervisor owns the single ``litellm`` child process that
doorae-server runs when ``DOORAE_LLM_GATEWAY_ENABLED=true``. Its
responsibilities:

1. Spawn the subprocess with ``--config <rendered-yaml>`` and a clean
   env that carries *only* the Fernet-decrypted API keys (masked under
   ``DOORAE_LITELLM_*`` names to avoid colliding with doorae's own
   env), plus an ephemeral master key the reverse proxy reuses.
2. Probe ``GET /health`` until the subprocess answers — this is the
   single edge that transitions ``STARTING → RUNNING``.
3. Watch the process for unexpected exits and respawn with bounded
   exponential backoff (``[1s, 5s, 30s]``). Four consecutive crashes
   drop the supervisor into ``FAILED`` so an operator sees it in the
   Status panel instead of an infinite restart loop masking the real
   issue.
4. Handle admin-triggered respawns (Apply / Restart) via a graceful
   shutdown (SIGTERM → 30 s grace → SIGKILL) followed by a fresh spawn
   with the latest env/config.
5. Tear down cleanly when the FastAPI ``lifespan`` exits.

The public API is intentionally small — callers use only
:meth:`LLMGatewaySupervisor.start`, :meth:`.restart`, :meth:`.stop`,
and :meth:`.state`. Everything else (health polling, backoff,
process watch) is internal and covered by unit tests that mock
``asyncio.create_subprocess_exec``.

See ``docs/design/12-llm-gateway.md`` §12.2 for the full state
machine diagram.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


# ── State machine ─────────────────────────────────────────────────────


class GatewayState(str, Enum):
    """Supervisor state. See design doc §12.2 for transitions.

    String-valued so the Status admin endpoint can return it directly
    without an extra serialization layer.
    """

    INIT = "init"
    STARTING = "starting"
    RUNNING = "running"
    CRASHED = "crashed"
    RESTARTING = "restarting"
    STOPPED = "stopped"
    FAILED = "failed"
    TERMINATED = "terminated"


# Crash backoff schedule in seconds. The 4th consecutive failure
# drops the supervisor into ``FAILED`` — ``len(_BACKOFF_SCHEDULE) == 3``
# attempts are made with these delays, the next crash gives up.
_BACKOFF_SCHEDULE: tuple[float, ...] = (1.0, 5.0, 30.0)

# How long we poll ``GET /health`` before declaring the spawn a bust.
_HEALTH_TIMEOUT_SEC = 10.0

# SIGTERM grace period before SIGKILL during graceful shutdown.
_GRACEFUL_SHUTDOWN_SEC = 30.0

# Once the supervisor has been RUNNING for this long, reset the
# crash counter so genuinely transient issues don't accumulate.
_CRASH_COUNTER_RESET_SEC = 300.0


# ── Public data surface ───────────────────────────────────────────────


@dataclass
class GatewayStatus:
    """Snapshot surfaced to the admin Status panel.

    A plain dataclass (not Pydantic) so the supervisor has no web-layer
    dependency. The API handler converts this to a response model when
    it reads ``.state()``.
    """

    state: GatewayState
    pid: Optional[int] = None
    port: Optional[int] = None
    started_at: Optional[float] = None  # monotonic seconds
    last_restart_at: Optional[float] = None
    config_hash: Optional[str] = None  # hash of the yaml currently loaded
    crash_count: int = 0
    last_error: Optional[str] = None


# ── Supervisor ────────────────────────────────────────────────────────


@dataclass
class _SpawnParams:
    """Inputs the supervisor needs to spawn ``litellm``.

    Captured as a dataclass so callers can build them once per Apply
    cycle (config writer + secret decryption) and hand a single value
    to :meth:`LLMGatewaySupervisor.restart`. Keeping this out of the
    supervisor lets tests substitute it without mocking Fernet.
    """

    config_path: Path
    # Env subset injected into the child. Merged with the server's own
    # env at spawn time — keep this dict small and unambiguous
    # (``DOORAE_LITELLM_*`` only) so a stray inherited var can't shadow.
    child_env: dict[str, str] = field(default_factory=dict)
    # Random master key shared with the reverse proxy for
    # Authorization replacement. Regenerated on each start; never
    # persisted.
    master_key: str = ""
    # LiteLLM's listen port. Always bound to 127.0.0.1; surface exposed
    # to callers only through the reverse proxy.
    port: int = 4001


class LLMGatewaySupervisor:
    """Owns the single ``litellm`` child process.

    Instantiated once by :mod:`doorae.app` during ``lifespan`` when
    ``settings.llm_gateway_enabled`` is true. Not re-entrant — there
    is exactly one supervisor per server process.

    The constructor takes dependencies as arguments so tests can
    substitute them. In production the caller wires it up to
    ``asyncio.create_subprocess_exec`` + ``httpx.AsyncClient``.
    """

    def __init__(
        self,
        spawn_params_factory,  # Callable[[], Awaitable[_SpawnParams]]
        *,
        binary: str = "litellm",
        backoff_schedule: tuple[float, ...] = _BACKOFF_SCHEDULE,
        health_timeout: float = _HEALTH_TIMEOUT_SEC,
        graceful_shutdown: float = _GRACEFUL_SHUTDOWN_SEC,
    ) -> None:
        self._spawn_params_factory = spawn_params_factory
        self._binary = binary
        self._backoff_schedule = backoff_schedule
        self._health_timeout = health_timeout
        self._graceful_shutdown = graceful_shutdown

        self._state: GatewayState = GatewayState.INIT
        self._status: GatewayStatus = GatewayStatus(state=GatewayState.INIT)
        # Protects state transitions — start/restart/stop must be
        # serialised against each other even if admin clicks fast.
        self._lock = asyncio.Lock()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._watch_task: Optional[asyncio.Task] = None

    # ── Public API ────────────────────────────────────────────────

    async def start(self) -> None:
        """Spawn the child for the first time.

        Safe to call on any state; a no-op if already ``RUNNING``.
        Raises no exceptions — failures land in ``status.last_error``
        and ``state`` becomes ``FAILED``. Callers should probe
        :meth:`.state` rather than relying on return value.
        """
        raise NotImplementedError  # Phase 2 — TDD implementation

    async def restart(self) -> None:
        """Graceful SIGTERM, then spawn with fresh env/config.

        Called by the admin ``POST /api/v1/llm-gateway/apply`` endpoint
        and the manual ``/restart`` endpoint. Rolls forward — if the
        new process fails health, state stays ``FAILED`` and the old
        one is already gone (no attempt to rollback to the previous
        yaml).
        """
        raise NotImplementedError

    async def stop(self) -> None:
        """Tear down for server shutdown. Terminal."""
        raise NotImplementedError

    def status(self) -> GatewayStatus:
        """Snapshot for the Status admin endpoint. Thread-safe read."""
        raise NotImplementedError

    @property
    def state(self) -> GatewayState:
        """Shorthand for ``status().state``."""
        return self._state

    # ── Internals (exposed for test override) ─────────────────────

    async def _spawn(self, params: _SpawnParams) -> None:
        """Run ``asyncio.create_subprocess_exec`` and await health."""
        raise NotImplementedError

    async def _health_check(self, port: int) -> bool:
        """Poll ``http://127.0.0.1:<port>/health`` until 2xx or timeout."""
        raise NotImplementedError

    async def _watch_loop(self) -> None:
        """Detect crashes and trigger respawn / FAILED."""
        raise NotImplementedError

    async def _graceful_terminate(self) -> None:
        """SIGTERM → wait → SIGKILL escalation."""
        raise NotImplementedError
