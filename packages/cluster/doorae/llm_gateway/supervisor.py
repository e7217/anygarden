"""LiteLLM subprocess lifecycle (#197).

The supervisor owns the single ``litellm`` child process that
doorae-server runs when ``DOORAE_LLM_GATEWAY_ENABLED=true``. Its
responsibilities:

1. Spawn the subprocess with ``--config <rendered-yaml>`` and a clean
   env that carries *only* the Fernet-decrypted API keys (masked under
   ``DOORAE_LITELLM_*`` names to avoid colliding with doorae's own
   env), plus an ephemeral master key the reverse proxy reuses.
2. Probe the LiteLLM liveness endpoint until the subprocess answers —
   this is the single edge that transitions ``STARTING → RUNNING``.
3. Watch the process for unexpected exits and respawn with bounded
   exponential backoff (``[1s, 5s, 30s]``). A fourth consecutive crash
   drops the supervisor into ``FAILED`` so an operator sees it in the
   Status panel instead of an infinite restart loop masking the real
   issue.
4. Handle admin-triggered respawns (Apply / Restart) via a graceful
   shutdown (SIGTERM → grace → SIGKILL) followed by a fresh spawn
   with the latest env/config.
5. Tear down cleanly when the FastAPI ``lifespan`` exits.

Subprocess creation (``asyncio.create_subprocess_exec``) and health
probing (``httpx.AsyncClient``) are injected at construction so the
class has no I/O of its own — unit tests drop in ``AsyncMock``
callables and simulate crashes by tripping a ``FakeProc`` event.
Production wiring lives in ``doorae.app`` where the factories are
bound to the real asyncio / httpx machinery.

See ``docs/design/12-llm-gateway.md`` §12.2 for the state machine.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

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


# Crash backoff schedule in seconds. ``len(_BACKOFF_SCHEDULE)`` is also
# the number of consecutive respawn attempts tolerated — a crash after
# the schedule is exhausted lands the supervisor in ``FAILED``.
_BACKOFF_SCHEDULE: tuple[float, ...] = (1.0, 5.0, 30.0)

# How long we poll LiteLLM liveness before declaring the spawn a bust.
_HEALTH_TIMEOUT_SEC = 10.0

# SIGTERM grace period before SIGKILL during graceful shutdown.
_GRACEFUL_SHUTDOWN_SEC = 30.0


# ── Public data surface ───────────────────────────────────────────────


@dataclass
class GatewayStatus:
    """Snapshot surfaced to the admin Status panel.

    A plain dataclass (not Pydantic) so the supervisor has no web-layer
    dependency. The API handler converts this to a response model when
    it reads ``.status()``.
    """

    state: GatewayState
    pid: Optional[int] = None
    port: Optional[int] = None
    started_at: Optional[float] = None  # monotonic seconds
    last_restart_at: Optional[float] = None
    config_hash: Optional[str] = None  # hash of the yaml currently loaded
    crash_count: int = 0
    last_error: Optional[str] = None


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


# ── Injection surface ─────────────────────────────────────────────────
#
# ``SpawnFn``: the callable the supervisor invokes to turn a set of
# spawn params into a running child process. Production binds this
# to ``asyncio.create_subprocess_exec``; tests pass an ``AsyncMock``
# returning a FakeProc.
#
# ``HealthProbe``: async callable that returns ``True`` once LiteLLM
# answers the liveness probe successfully, ``False`` on timeout/failure.
# Production does an HTTP poll loop inside the probe; tests pass a
# trivial ``AsyncMock(return_value=True)``.

SpawnFn = Callable[[_SpawnParams, str], Awaitable[Any]]
HealthProbe = Callable[[int], Awaitable[bool]]
SpawnParamsFactory = Callable[[], Any]  # returns _SpawnParams or awaitable


# ── Supervisor ────────────────────────────────────────────────────────


class LLMGatewaySupervisor:
    """Owns the single ``litellm`` child process.

    Instantiated once by :mod:`doorae.app` during ``lifespan`` when
    ``settings.llm_gateway_enabled`` is true. Not re-entrant — there
    is exactly one supervisor per server process.

    Public API: :meth:`start`, :meth:`restart`, :meth:`stop`,
    :meth:`status`. Everything else is internal; tests inject
    ``spawn_fn`` + ``health_probe`` to drive the state machine
    without a real subprocess.
    """

    def __init__(
        self,
        spawn_params_factory: SpawnParamsFactory,
        *,
        spawn_fn: SpawnFn,
        health_probe: HealthProbe,
        binary: str = "litellm",
        backoff_schedule: tuple[float, ...] = _BACKOFF_SCHEDULE,
        health_timeout: float = _HEALTH_TIMEOUT_SEC,
        graceful_shutdown: float = _GRACEFUL_SHUTDOWN_SEC,
    ) -> None:
        self._spawn_params_factory = spawn_params_factory
        self._spawn_fn = spawn_fn
        self._health_probe = health_probe
        self._binary = binary
        self._backoff_schedule = backoff_schedule
        self._health_timeout = health_timeout
        self._graceful_shutdown = graceful_shutdown

        self._state: GatewayState = GatewayState.INIT
        self._status: GatewayStatus = GatewayStatus(state=GatewayState.INIT)
        # Serialises admin-initiated state transitions so a fast
        # double-click on Apply can't race with itself. The watch loop
        # doesn't take the lock — it reacts to proc exit, and
        # restart()/stop() explicitly cancel it before taking the lock.
        self._lock = asyncio.Lock()
        self._proc: Any = None  # asyncio.subprocess.Process in prod
        self._watch_task: Optional[asyncio.Task] = None
        # Current spawn params — retained after a successful spawn so
        # the reverse proxy can read ``master_key`` / ``port`` without
        # having to poke into the child's env. Cleared on stop() and
        # during restart(); the supervisor writes it, the proxy reads.
        # ``master_key`` is deliberately excluded from ``GatewayStatus``
        # so admin status responses can't accidentally serialize it.
        self._current_params: Optional[_SpawnParams] = None

    # ── Public API ────────────────────────────────────────────────

    async def start(self) -> None:
        """Spawn the child for the first time.

        No-op if already RUNNING or TERMINATED. Failures land in
        ``status().last_error`` with ``state=FAILED`` rather than
        raising — callers drive the lifecycle via :meth:`.status`.
        """
        async with self._lock:
            if self._state == GatewayState.RUNNING:
                return
            if self._state == GatewayState.TERMINATED:
                # A terminated supervisor is inert by design; the
                # owning lifespan should construct a fresh one instead
                # of reviving this instance.
                return
            # Reset crash counter / error on a fresh start.
            self._status = GatewayStatus(state=GatewayState.INIT)
            await self._do_spawn()

    async def restart(self) -> None:
        """Graceful SIGTERM, then spawn with fresh env/config.

        Called by the admin ``POST /api/v1/llm-gateway/apply`` and
        ``/restart`` endpoints. Rolls forward — if the new process
        fails health, ``state=FAILED`` and the old one is already gone.
        """
        async with self._lock:
            self._set_state(GatewayState.RESTARTING)
            await self._cancel_watch()
            await self._graceful_terminate()
            self._proc = None
            self._set_state(GatewayState.STOPPED)
            # Reset crash counter — a successful Apply is a known-good
            # state so backoff history from a previous bad run
            # shouldn't hobble recovery.
            self._status.crash_count = 0
            self._status.last_error = None
            await self._do_spawn()

    async def stop(self) -> None:
        """Tear down for server shutdown. Terminal."""
        async with self._lock:
            if self._state == GatewayState.TERMINATED:
                return
            self._set_state(GatewayState.TERMINATED)
            await self._cancel_watch()
            await self._graceful_terminate()
            self._proc = None
            self._current_params = None

    def status(self) -> GatewayStatus:
        """Immutable snapshot for the Status admin endpoint."""
        return replace(self._status)

    @property
    def state(self) -> GatewayState:
        """Shorthand for ``status().state``."""
        return self._state

    @property
    def master_key(self) -> Optional[str]:
        """Current LiteLLM master key, or ``None`` when not RUNNING.

        Read by the reverse proxy to swap into outgoing ``Authorization``
        headers. Kept off ``GatewayStatus`` to avoid leaking it into
        admin-facing status responses.
        """
        if self._state != GatewayState.RUNNING or self._current_params is None:
            return None
        return self._current_params.master_key

    @property
    def port(self) -> Optional[int]:
        """Current LiteLLM listen port, or ``None`` when not RUNNING."""
        if self._state != GatewayState.RUNNING or self._current_params is None:
            return None
        return self._current_params.port

    # ── Internals ─────────────────────────────────────────────────

    async def _do_spawn(self) -> None:
        """Spawn + health probe + start watch task.

        Called under the lock from start()/restart(), and from the
        watch loop (without the lock) for automatic respawn. No lock
        is taken here — callers are responsible for synchronisation.
        """
        self._set_state(GatewayState.STARTING)

        params = self._spawn_params_factory()
        if inspect.isawaitable(params):
            params = await params

        try:
            proc = await self._spawn_fn(params, self._binary)
        except Exception as exc:
            self._set_state(
                GatewayState.FAILED, error=f"spawn failed: {exc!r}"
            )
            logger.error("llm_gateway.spawn_failed", error=str(exc))
            return

        self._proc = proc
        self._status.pid = getattr(proc, "pid", None)
        self._status.port = params.port

        # Health probe. Treat any exception or False result as a bust;
        # escalate to FAILED after graceful-terminating the child we
        # just started (don't leave zombies behind).
        try:
            ok = await asyncio.wait_for(
                self._health_probe(params.port),
                timeout=self._health_timeout,
            )
        except asyncio.TimeoutError:
            ok = False
            self._status.last_error = "health check timeout"
        except Exception as exc:
            ok = False
            self._status.last_error = f"health probe error: {exc!r}"

        if not ok:
            if self._status.last_error is None:
                self._status.last_error = "health check returned False"
            await self._graceful_terminate()
            self._proc = None
            self._set_state(GatewayState.FAILED)
            return

        # Record params so reverse_proxy can read master_key/port.
        self._current_params = params
        self._set_state(GatewayState.RUNNING)
        self._watch_task = asyncio.create_task(
            self._watch_loop(proc), name="llm_gateway_watch"
        )
        logger.info(
            "llm_gateway.running",
            pid=self._status.pid,
            port=self._status.port,
        )

    async def _watch_loop(self, proc: Any) -> None:
        """Detect unexpected exits → respawn with backoff, or FAIL.

        Bound to a specific ``proc`` so a late respawn can't accidentally
        observe a newer process's exit — each watch task tracks the
        exact proc it was started for.
        """
        try:
            await proc.wait()
        except asyncio.CancelledError:
            return

        # Intentional exits don't trigger respawn. The state was set
        # by restart()/stop() before they cancelled / terminated us.
        if self._state in (
            GatewayState.RESTARTING,
            GatewayState.STOPPED,
            GatewayState.TERMINATED,
        ):
            return

        # Unexpected crash. Increment counter, decide respawn vs fail.
        self._status.crash_count += 1
        crash_idx = self._status.crash_count - 1  # 0-indexed

        if crash_idx >= len(self._backoff_schedule):
            self._set_state(
                GatewayState.FAILED,
                error=(
                    f"backoff exhausted after "
                    f"{self._status.crash_count} consecutive crashes"
                ),
            )
            logger.error(
                "llm_gateway.failed",
                crashes=self._status.crash_count,
            )
            return

        self._set_state(GatewayState.CRASHED)
        logger.warning(
            "llm_gateway.crashed",
            crash_count=self._status.crash_count,
            backoff=self._backoff_schedule[crash_idx],
        )
        await asyncio.sleep(self._backoff_schedule[crash_idx])

        # Respawn — recursion is bounded by len(backoff_schedule).
        await self._do_spawn()

    async def _cancel_watch(self) -> None:
        if self._watch_task is None:
            return
        self._watch_task.cancel()
        try:
            await self._watch_task
        except (asyncio.CancelledError, Exception):
            pass
        self._watch_task = None

    async def _graceful_terminate(self) -> None:
        """SIGTERM → wait ≤ grace → SIGKILL escalation.

        Safe to call when there is no child (no-op). After returning,
        the ``_proc`` is drained — its returncode is set.
        """
        if self._proc is None:
            return
        if getattr(self._proc, "returncode", None) is not None:
            return  # already exited

        try:
            self._proc.terminate()
        except ProcessLookupError:
            return
        except Exception as exc:
            logger.warning("llm_gateway.terminate_failed", error=str(exc))
            return

        try:
            await asyncio.wait_for(
                self._proc.wait(), timeout=self._graceful_shutdown
            )
        except asyncio.TimeoutError:
            logger.warning("llm_gateway.sigkill_escalation")
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            try:
                await self._proc.wait()
            except Exception:
                pass

    def _set_state(self, new_state: GatewayState, error: Optional[str] = None) -> None:
        self._state = new_state
        self._status.state = new_state
        if error is not None:
            self._status.last_error = error
