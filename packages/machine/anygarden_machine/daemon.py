"""WebSocket daemon: main loop, frame dispatch, and declarative reconciliation."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import structlog
import websockets
from websockets.asyncio.client import connect

from anygarden_machine import __version__
from anygarden_machine.config import save_token
from anygarden_machine.crash_budget import CrashBudget
from anygarden_machine.detector import detect_engines
from anygarden_machine.manifest_store import ManifestStore
from anygarden_machine.sysinfo import collect_system_info
from anygarden_machine.protocol.frames import (
    AgentActual,
    AgentMemorySharedFileDeleteFrame,
    AgentMemorySharedFileWriteFrame,
    RegisterFrame,
    ReportActualStateFrame,
    RequestReplacementFrame,
    SyncDesiredStateFrame,
    TokenRequestFrame,
    parse_server_frame,
)
from anygarden_machine.spawner import Spawner, SpawnManifest

log = structlog.get_logger()

REPORT_INTERVAL = 30  # seconds between report_actual_state sends
RECONNECT_BASE = 1  # initial backoff in seconds
RECONNECT_MAX = 60  # max backoff in seconds
TOKEN_REQUEST_TIMEOUT = 30  # seconds to wait for a token_grant
# Safety-net for leaked ``_transitional_states`` entries: if a
# starting/stopping annotation sticks around longer than this without
# either a running-process match or a stop callback, drop it on the
# next report so the server isn't stuck seeing a phantom transition
# forever. The window is generous because normal spawns can legitimately
# take tens of seconds (engine boot, first tool load).
TRANSITIONAL_LEAK_GRACE = 60.0

# Issue #290 — outbox artifact constraints. The cap leaves headroom
# under the 1 MiB WebSocket frame limit after base64 inflation
# (768 KiB raw → ~1024 KiB encoded plus envelope). MIME whitelist
# matches the server-side check; daemon enforcement is purely an
# optimisation (don't burn a frame on something the server will
# reject). text/* is broad on purpose — anything the existing
# RoomSharedFile flow accepts as text fits here too.
ARTIFACT_MAX_BYTES = 768 * 1024
ARTIFACT_ALLOWED_MIMES: frozenset[str] = frozenset({
    # Images — the headline use case (codex screenshot story).
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    # Text — symmetrical with the existing user→agent shared file
    # whitelist. Lets agents drop diagnostic logs / data dumps too.
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/csv",
    "text/yaml",
    "text/x-yaml",
    "application/json",
    "application/yaml",
    "application/x-yaml",
    "text/x-python",
    "application/xml",
    "text/xml",
    "text/html",
})


def _base_url_from_machine_url(machine_ws_url: str) -> str:
    """Trim the ``/ws/machines/<id>`` endpoint suffix off the daemon URL.

    The daemon is already connected to a server it can reach. Agents spawned
    on this host should target that same prefix — regardless of what the
    server thinks its own hostname/port is (which may be wrong under
    0.0.0.0 binds, reverse proxies, or container networking).

    Only the daemon endpoint suffix is stripped; any leading path segments
    (reverse-proxy mount like ``/anygarden``, API version like ``/api/v1``)
    are preserved so the SDK's ``{base}/ws/rooms/<id>`` composition still
    traverses the same proxy the daemon authenticated against.
    """
    if not machine_ws_url:
        return ""
    parsed = urlparse(machine_ws_url)
    if not parsed.scheme or not parsed.netloc:
        return ""

    path = parsed.path
    marker = "/ws/machines"
    idx = path.rfind(marker)
    if idx != -1:
        path = path[:idx]
    # ``urlunparse`` normalizes an empty path to "", which is what the SDK
    # expects so that it can safely append ``/ws/rooms/<id>``.
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


class MachineDaemon:
    """Machine daemon that connects to anygarden-server via WebSocket.

    Implements a **declarative desired-state protocol**: the server sends
    SyncDesiredStateFrame / SyncBatchFrame describing which agents should
    be running, and the daemon reconciles its actual state toward the
    desired state.  The daemon requests agent tokens on demand via
    TokenRequestFrame and handles local crash-restart with CrashBudget.
    """

    def __init__(
        self,
        server_url: str,
        machine_id: str,
        machine_token: str,
        labels: dict | None = None,
        token_path: Any = None,
        agent_dirs_root: Path | None = None,
    ) -> None:
        self.server_url = server_url
        self.machine_id = machine_id
        self.machine_token = machine_token
        self.labels = labels or {}
        self._token_path = token_path
        self._draining = False
        self._ws: Any = None

        self._manifest_store = ManifestStore(agents_root=agent_dirs_root)
        self._spawner = Spawner(
            on_stopped=self._on_agent_stopped,
            on_crashed=self._on_agent_crashed,
            agent_server_url=_base_url_from_machine_url(server_url),
            agent_dirs_root=agent_dirs_root,
            # #451 — share the daemon's ManifestStore so runtime.json
            # reads (re-adopt) and writes (spawn/cleanup) stay consistent
            # within one process.
            manifest_store=self._manifest_store,
        )
        self._crash_budgets: dict[str, CrashBudget] = {}
        self._token_futures: dict[str, asyncio.Future[str]] = {}
        self._running_generations: dict[str, int] = {}
        # Per-agent serialization. Every mutation of
        # ``_running_generations`` or dispatch of a spawn task for a
        # given agent_id must happen while holding the matching lock,
        # so two concurrent ``_reconcile_agent`` calls cannot both
        # clear the generation check and dispatch duplicate spawns
        # (#183). Different agents get different locks — reconciliation
        # across agents stays parallel.
        self._agent_locks: dict[str, asyncio.Lock] = {}
        # Short-lived "starting"/"stopping" annotations (#219). Set at
        # spawn/kill dispatch, cleared by the ``_on_agent_stopped`` /
        # ``_on_agent_crashed`` callbacks or by the spawn coroutine's
        # cleanup step. ``_report_actual_state`` merges these with the
        # spawner's running list so admins see the transition instead
        # of a 30s-wide "running → stopped" jump. The value is the
        # unix-epoch seconds the annotation was set; stale entries
        # older than ``_TRANSITIONAL_LEAK_GRACE`` seconds are pruned on
        # each report as a safety net against missed callbacks.
        self._transitional_states: dict[str, str] = {}
        self._transitional_set_at: dict[str, float] = {}

        # Issue #237 — per-agent last-seen hash of ``memory/notes.md``.
        # Used by the report loop to cheaply detect mutation since the
        # last sync. Populated lazily on first read. Wiped when the
        # agent stops so a re-spawn performs a clean first-read.
        self._memory_last_hash: dict[str, str] = {}

        # Issue #290 — per-(agent_id, filename) sha256 cache for the
        # ``memory/outbox/`` watcher. Same idea as the notes hash but
        # keyed by filename because one agent can have many outbox
        # files in parallel. Cleared when the agent stops so re-spawn
        # forces re-emission (which the server dedups via
        # ``UniqueConstraint(room_id, sha256)``).
        self._artifact_last_hash: dict[tuple[str, str], str] = {}

    # ── Main loop ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main WebSocket reconnection loop with exponential backoff."""
        backoff = RECONNECT_BASE
        while True:
            try:
                await self._connect_and_serve()
                backoff = RECONNECT_BASE
            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                OSError,
            ) as exc:
                log.warning(
                    "ws_disconnected",
                    error=str(exc),
                    reconnect_in=backoff,
                )
            except asyncio.CancelledError:
                log.info("daemon_cancelled")
                await self._spawner.drain()
                return

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _connect_and_serve(self) -> None:
        """Establish WS connection and run message + report loops."""
        subprotocols = [
            websockets.Subprotocol("anygarden.v1"),
            websockets.Subprotocol(f"bearer.{self.machine_token}"),
        ]

        async with connect(
            self.server_url,
            subprotocols=subprotocols,
        ) as ws:
            self._ws = ws
            log.info("ws_connected", url=self.server_url)

            await self._register()

            # #451 — re-adopt agent processes that outlived a daemon
            # restart. Must run AFTER register (we're connected) and
            # BEFORE the first report/reconcile: re-adopt restores
            # ``_running_generations`` and ``Spawner._agents`` so the
            # reconcile generation gate suppresses duplicate spawns and
            # ``kill`` can reach the existing process group. Idempotent
            # across reconnects (already-adopted agents are skipped).
            await self._readopt_running_agents()

            # Send initial report so server knows which agents survived a reconnect
            await self._report_actual_state()

            # Run report loop and message handler concurrently
            report_task = asyncio.create_task(self._report_loop())
            try:
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        await self._handle(data)
                    except json.JSONDecodeError:
                        log.warning("invalid_json", raw=str(raw_msg)[:200])
                    except Exception as exc:
                        log.error("handle_error", error=str(exc))
            finally:
                report_task.cancel()
                try:
                    await report_task
                except asyncio.CancelledError:
                    pass
                self._ws = None

    async def _register(self) -> None:
        """Send register frame with detected capabilities + system info."""
        detection = await detect_engines()
        capabilities = [
            {"engine": e.engine, "version": e.version, "path": e.path}
            for e in detection.engines
        ]
        # Static system info (issue #523) — best-effort, never blocks register.
        system_info = collect_system_info()
        frame = RegisterFrame(
            machine_id=self.machine_id,
            capabilities=capabilities,
            labels=self.labels,
            system_info=system_info,
            daemon_version=__version__,  # #546
        )
        await self._send(frame.model_dump())
        log.info(
            "registered",
            machine_id=self.machine_id,
            capabilities=len(capabilities),
            hostname=system_info.hostname,
            lan_ip=system_info.lan_ip,
            cpu_cores=system_info.cpu_cores,
            memory_gb=system_info.memory_gb,
        )

    async def _readopt_running_agents(self) -> None:
        """Re-adopt agent processes that survived a daemon restart (#451).

        Reads every persisted ``runtime.json`` (via ``ManifestStore``)
        and, for each one whose recorded process group is still alive and
        passes the PID-recycle guard, registers it with the spawner and
        restores ``_running_generations[agent_id]`` to the generation
        stamped at spawn time. Dead / recycled runtimes are cleared by
        ``Spawner.adopt``.

        Restoring ``_running_generations`` BEFORE the first reconcile is
        what makes the generation gate (``current_gen >= generation``)
        short-circuit instead of spawning a duplicate. Agents already
        tracked in memory (e.g. a WS reconnect without a process restart)
        are skipped so adopt stays idempotent.
        """
        adopted = 0
        for agent_id, runtime in self._manifest_store.list_runtimes():
            # Skip agents we already track (reconnect, not a cold start).
            if self._spawner.get_running(agent_id) is not None:
                continue

            generation = runtime.get("generation", 0)
            if self._spawner.adopt(
                agent_id,
                runtime,
                handle_stopped=self._on_agent_stopped,
                handle_crashed=self._on_agent_crashed,
            ):
                # Restore the generation reservation so the reconcile
                # gate treats this agent as already-at-generation.
                if isinstance(generation, int):
                    self._running_generations[agent_id] = generation
                else:
                    self._running_generations[agent_id] = 0
                adopted += 1

        if adopted:
            log.info("agents_readopted", count=adopted)

    async def _report_loop(self) -> None:
        """Send report_actual_state every REPORT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(REPORT_INTERVAL)
            await self._report_actual_state()

    # ── Frame dispatch ─────────────────────────────────────────────────

    async def _handle(self, data: dict) -> None:
        """Dispatch incoming server frame to the appropriate handler."""
        frame = parse_server_frame(data)

        match frame.type:
            case "sync_desired_state":
                await self._handle_sync_desired_state(frame)
            case "sync_batch":
                await self._handle_sync_batch(frame)
            case "token_grant":
                self._handle_token_grant(frame)
            case "drain":
                await self._handle_drain()
            case "ping":
                await self._report_actual_state()
            case "rotate_token":
                await self._handle_rotate_token(frame)
            case "agent_memory_shared_file_write":
                await self._handle_agent_memory_shared_file_write(frame)
            case "agent_memory_shared_file_delete":
                await self._handle_agent_memory_shared_file_delete(frame)

    # ── Desired-state handlers ─────────────────────────────────────────

    async def _handle_sync_desired_state(self, frame: Any) -> None:
        """Handle a single agent's desired state declaration."""
        self._manifest_store.save(frame)
        await self._reconcile_agent(frame.agent_id)

    async def _handle_sync_batch(self, frame: Any) -> None:
        """Handle a batch of agent desired states.

        ``frame.is_full_snapshot`` distinguishes two semantics (#185):

        * True (default): the batch is the full desired set for this
          machine. Agents running locally but missing from the batch
          are orphans and get stopped.
        * False: the batch is a targeted update. Only the listed
          agents are reconciled; anything absent stays untouched. A
          server-side bug that produces an empty partial batch must
          not mass-kill local agents.
        """
        # Save all manifests
        desired_ids: set[str] = set()
        for agent_frame in frame.agents:
            self._manifest_store.save(agent_frame)
            desired_ids.add(agent_frame.agent_id)

        if frame.is_full_snapshot:
            # Kill orphans: agents running locally but not in the batch.
            # Hold the per-agent lock around each kill so the orphan
            # pop doesn't clobber a concurrent reservation left by a
            # newer ``_reconcile_agent`` (#183).
            running_list = self._spawner.list_running()
            for info in running_list:
                agent_id = info["agent_id"]
                if agent_id not in desired_ids:
                    log.info("killing_orphan", agent_id=agent_id)
                    async with self._lock_for(agent_id):
                        await self._spawner.kill(agent_id)
                        self._running_generations.pop(agent_id, None)

        # Reconcile all desired agents
        for agent_frame in frame.agents:
            await self._reconcile_agent(agent_frame.agent_id)

    def _lock_for(self, agent_id: str) -> asyncio.Lock:
        """Return (creating if absent) the per-agent serialization lock."""
        lock = self._agent_locks.get(agent_id)
        if lock is None:
            lock = asyncio.Lock()
            self._agent_locks[agent_id] = lock
        return lock

    async def _reconcile_agent(self, agent_id: str) -> None:
        """Reconcile a single agent's actual state toward its desired state."""
        manifest = self._manifest_store.load(agent_id)
        if manifest is None:
            return

        # Serialize per-agent reconcile decisions (#183). Without this
        # lock, two sync_desired_state frames landing back to back for
        # the same agent could both clear the generation check before
        # either one completed, dispatching duplicate spawn tasks.
        # Different agents use different locks so cross-agent reconcile
        # stays parallel.
        should_dispatch_spawn = False
        async with self._lock_for(agent_id):
            running = self._spawner.get_running(agent_id)

            if manifest.desired_state == "stopped":
                if running is not None:
                    log.info("stopping_agent", agent_id=agent_id)
                    # #219 — annotate BEFORE kill so the report we push
                    # out next carries the stopping badge; the periodic
                    # 30s loop would otherwise leave admins staring at
                    # a stale ``running`` until the kill completes.
                    self._mark_transitional(agent_id, "stopping")
                    await self._report_actual_state()
                    await self._spawner.kill(agent_id)
                self._running_generations.pop(agent_id, None)
                return

            # desired_state == "running". ``_running_generations`` holds
            # either the completed spawn's gen OR a pre-reservation for
            # an in-flight spawn; in both cases, a request at the same
            # or lower gen is already-covered.
            current_gen = self._running_generations.get(agent_id, -1)
            if current_gen >= manifest.generation:
                return

            if running is not None:
                log.info(
                    "killing_old_generation",
                    agent_id=agent_id,
                    current=current_gen,
                    desired=manifest.generation,
                )
                await self._spawner.kill(agent_id)

            # Reset crash budget since this is a server-driven reconcile
            # (not a crash-driven restart).
            budget = self._crash_budgets.get(agent_id)
            if budget is not None:
                budget.reset()

            # Pre-reserve the generation BEFORE dispatching the spawn
            # task. Any concurrent reconcile at this gen or lower will
            # now short-circuit on the ``current_gen >= ...`` check above
            # instead of racing to dispatch a duplicate spawn. On spawn
            # failure, ``_request_token_and_spawn`` rolls this back.
            self._running_generations[agent_id] = manifest.generation
            # #219 — mark starting so the immediate report (and any
            # periodic report before the spawn completes) surfaces the
            # transition to admins.
            self._mark_transitional(agent_id, "starting")
            should_dispatch_spawn = True

        # Dispatch OUTSIDE the lock: ``_request_token_and_spawn`` awaits
        # a Future resolved by the WS message loop delivering a
        # token_grant frame. Awaiting inline would block the loop and
        # deadlock; the original code handled this with ``create_task``
        # but without the lock+reservation above, the dispatch itself
        # raced.
        if should_dispatch_spawn:
            # Push the ``starting`` annotation out before the spawn task
            # yields — otherwise admins wait one 30s report cycle to see
            # anything. Done outside the lock so ``_send`` doesn't
            # serialize reconciles across agents.
            await self._report_actual_state()
            asyncio.create_task(
                self._request_token_and_spawn(agent_id, manifest)
            )

    async def _request_token_and_spawn(
        self,
        agent_id: str,
        manifest: SyncDesiredStateFrame,
    ) -> None:
        """Request an agent token from the server, then spawn the agent."""
        # Create a future for the token grant
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._token_futures[agent_id] = future

        # Send token request
        token_req = TokenRequestFrame(agent_ids=[agent_id])
        await self._send(token_req.model_dump())

        try:
            agent_token = await asyncio.wait_for(
                future, timeout=TOKEN_REQUEST_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.error("token_request_timeout", agent_id=agent_id)
            self._token_futures.pop(agent_id, None)
            # Roll back the pre-reservation so a retry can proceed.
            await self._rollback_reservation(agent_id, manifest.generation)
            return
        finally:
            self._token_futures.pop(agent_id, None)

        # Build SpawnManifest from manifest + token
        spawn_manifest = SpawnManifest(
            agent_id=agent_id,
            engine=manifest.engine,
            agent_token=agent_token,
            profile_yaml=manifest.profile_yaml,
            rooms=list(manifest.rooms),
            server_url="",  # spawner uses its own agent_server_url
            name=manifest.name,
            agents_md=manifest.agents_md,
            files=dict(manifest.files),
            # ``manifest`` came from ``ManifestStore.load`` which returns
            # engine_secrets={} by design (disk storage strips secrets).
            # The freshest frame's secrets live in the in-memory cache.
            engine_secrets=self._manifest_store.get_secrets(agent_id),
            # Issue #237 — pass DB snapshot through so the spawner can
            # materialize ``memory/notes.md`` on cold start. ``getattr``
            # keeps compatibility with pre-#237 frames that omit the field.
            memory_md=getattr(manifest, "memory_md", None),
            reasoning_effort=manifest.reasoning_effort,
            model=manifest.model,
            # Issue #309 — semantic permission tier propagated from
            # the cluster's sync frame. ``getattr`` keeps in-memory
            # manifests from pre-#309 schema revisions compatible.
            permission_level=getattr(manifest, "permission_level", None),
            # Issue #493 — per-agent turn timeout propagated from the sync
            # frame. ``getattr`` keeps pre-#493 in-memory manifests compatible.
            turn_timeout_sec=getattr(manifest, "turn_timeout_sec", None),
            sub_rooms=list(manifest.sub_rooms),
            # Issue #73 — runtime passes through from the server frame.
            # ``getattr`` keeps compatibility with in-memory manifest
            # objects from earlier schema revisions in tests.
            runtime=getattr(manifest, "runtime", "python") or "python",
            # Issue #277 — anygarden self-MCP bearer token from the
            # in-memory cache (manifest on disk strips it for the same
            # reasons engine_secrets are stripped).
            anygarden_mcp_token=self._manifest_store.get_anygarden_mcp_token(agent_id),
            # Issue #451 — stamp the generation into runtime.json so a
            # restarted daemon can restore ``_running_generations`` on
            # re-adopt and the reconcile gate suppresses a duplicate spawn.
            generation=manifest.generation,
        )

        result = await self._spawner.spawn(spawn_manifest)
        if result.success:
            # ``_running_generations[agent_id]`` was already pre-reserved
            # by ``_reconcile_agent`` at the requested generation, so
            # success leaves it in place. This log line is the single
            # point of truth for "spawn happened".
            log.info(
                "agent_spawned",
                agent_id=agent_id,
                pid=result.pid,
                generation=manifest.generation,
            )
        else:
            log.error(
                "spawn_failed",
                agent_id=agent_id,
                error=result.error,
            )
            await self._rollback_reservation(agent_id, manifest.generation)
        # #219 — the spawn is done (success or fail). Drop the
        # ``starting`` annotation so the final report reflects the true
        # state: running (if spawner.list_running picks it up) or
        # absent (server converges to stopped via absent-from-report).
        self._clear_transitional(agent_id)
        await self._report_actual_state()

    async def _rollback_reservation(
        self, agent_id: str, generation: int
    ) -> None:
        """Undo the pre-reservation in ``_running_generations`` for
        *agent_id* at *generation*, holding the per-agent lock so we
        don't clobber a higher generation reserved by a newer reconcile
        that arrived while this spawn was in flight (#183).
        """
        async with self._lock_for(agent_id):
            if self._running_generations.get(agent_id) == generation:
                self._running_generations.pop(agent_id, None)

    # ── Token grant ────────────────────────────────────────────────────

    def _handle_token_grant(self, frame: Any) -> None:
        """Resolve the pending Future for a token request."""
        future = self._token_futures.get(frame.agent_id)
        if future is not None and not future.done():
            future.set_result(frame.agent_token)
        else:
            log.warning(
                "unexpected_token_grant",
                agent_id=frame.agent_id,
            )

    # ── Crash / stop callbacks ─────────────────────────────────────────

    async def _on_agent_stopped(self, agent_id: str, exit_code: int) -> None:
        """Callback when an agent exits normally (exit code 0)."""
        async with self._lock_for(agent_id):
            self._running_generations.pop(agent_id, None)
        # #219 — process actually gone, release the transitional marker
        # so the next report is absent-from-report (→ server converges
        # to ``stopped``) instead of phantom-stopping.
        self._clear_transitional(agent_id)
        log.info("agent_stopped", agent_id=agent_id, exit_code=exit_code)
        await self._report_actual_state()

    async def _on_agent_crashed(
        self, agent_id: str, exit_code: int, stderr_tail: str
    ) -> None:
        """Callback when an agent crashes. Attempt local restart if budget allows."""
        async with self._lock_for(agent_id):
            self._running_generations.pop(agent_id, None)
        # #219 — crash implies the spawn lifecycle ended, whatever
        # transitional marker was there (typically ``starting`` from a
        # still-booting spawn) must go before the crash-restart dispatch
        # below otherwise the restart's fresh ``starting`` marker gets
        # overwritten on the leak-grace timer rather than set explicitly.
        self._clear_transitional(agent_id)
        log.warning(
            "agent_crashed",
            agent_id=agent_id,
            exit_code=exit_code,
            stderr_tail=stderr_tail[:200],
        )

        manifest = self._manifest_store.load(agent_id)
        if manifest is None or manifest.desired_state != "running":
            await self._report_actual_state()
            return

        if manifest.restart_policy == "stop":
            await self._report_actual_state()
            return

        # Get or create crash budget for this agent
        budget = self._crash_budgets.get(agent_id)
        if budget is None:
            budget = CrashBudget(
                max_restarts=manifest.max_restarts,
                window_seconds=manifest.restart_window_seconds,
            )
            self._crash_budgets[agent_id] = budget

        if budget.record_crash():
            # Budget allows restart. Pre-reserve the generation under
            # the per-agent lock so a concurrent server reconcile for
            # the same agent_id short-circuits on its
            # ``current_gen >= manifest.generation`` check instead of
            # dispatching a second, duplicate spawn (#183). If the
            # server's reconcile reserved first (newer gen), we honour
            # it and skip the crash restart.
            should_spawn = False
            async with self._lock_for(agent_id):
                current = self._running_generations.get(agent_id, -1)
                if current < manifest.generation:
                    self._running_generations[agent_id] = manifest.generation
                    should_spawn = True

            log.info(
                "crash_restart",
                agent_id=agent_id,
                crash_count=budget.crash_count,
                max_restarts=manifest.max_restarts,
                dispatched=should_spawn,
            )
            if should_spawn:
                await self._request_token_and_spawn(agent_id, manifest)
        else:
            # Budget exhausted
            log.warning(
                "crash_budget_exhausted",
                agent_id=agent_id,
                crash_count=budget.crash_count,
            )
            if manifest.restart_policy == "restart_anywhere":
                # Ask server to reschedule on another machine
                replacement = RequestReplacementFrame(
                    agent_id=agent_id,
                    reason=f"Crash budget exhausted ({budget.crash_count} "
                    f"crashes in {manifest.restart_window_seconds}s)",
                )
                await self._send(replacement.model_dump())
                # Mirror the restart_on_same_machine branch below: relinquish
                # local responsibility by marking the manifest stopped. Without
                # this, a daemon restart would ``load_all_running()`` the same
                # manifest and re-spawn an agent the server has already placed
                # elsewhere — split-brain ghost (#182).
                try:
                    self._manifest_store.update_desired_state(
                        agent_id, "stopped"
                    )
                except FileNotFoundError:
                    pass
            else:
                # restart_on_same_machine but budget exhausted — stop
                try:
                    self._manifest_store.update_desired_state(
                        agent_id, "stopped"
                    )
                except FileNotFoundError:
                    pass
            await self._report_actual_state()

    # ── Report actual state ────────────────────────────────────────────

    async def _report_actual_state(self) -> None:
        """Build and send a ReportActualStateFrame from current state.

        Merges two sources (#219):

        - ``_spawner.list_running()`` — concrete processes this daemon
          currently owns. The default state is ``running``.
        - ``_transitional_states`` — short-lived ``starting`` /
          ``stopping`` annotations set by ``_reconcile_agent`` on
          spawn/kill dispatch. When an entry exists, it takes
          precedence over the spawner's ``running`` (for ``stopping``
          where the process is still alive) or stands alone (for
          ``starting`` where no process exists yet).

        Entries that have been in ``_transitional_states`` longer than
        ``TRANSITIONAL_LEAK_GRACE`` seconds without a corresponding
        running process are pruned here as a safety net — if the
        normal callback path missed clearing the annotation (for
        example the daemon was restarted mid-spawn) the entry
        shouldn't linger forever.
        """
        now = time.time()
        self._prune_stale_transitional(now)

        agents: list[AgentActual] = []
        seen: set[str] = set()

        for info in self._spawner.list_running():
            agent_id = info["agent_id"]
            seen.add(agent_id)
            state = self._transitional_states.get(agent_id) or "running"
            agents.append(
                AgentActual(
                    agent_id=agent_id,
                    actual_state=state,
                    pid=info.get("pid"),
                    engine=info.get("engine", ""),
                    generation=self._running_generations.get(agent_id, 0),
                    uptime_seconds=info.get("uptime_seconds", 0),
                )
            )

        # Starting agents have no process yet — emit them separately so
        # the server sees the transition immediately rather than
        # interpreting "not in the report" as stopped.
        for agent_id, state in self._transitional_states.items():
            if agent_id in seen:
                continue
            if state != "starting":
                continue
            agents.append(
                AgentActual(
                    agent_id=agent_id,
                    actual_state="starting",
                    generation=self._running_generations.get(agent_id, 0),
                )
            )

        frame = ReportActualStateFrame(agents=agents)
        await self._send(frame.model_dump())

        # Issue #237 — piggy-back the memory sync on the same report
        # tick. Cheap (sha256 of a markdown file per running agent),
        # avoids spinning up a parallel scheduler. Failures are logged
        # but never abort the report path — sync-back is best-effort.
        try:
            await self._flush_memory_updates()
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("memory_flush_failed", error=str(exc))

        # Issue #290 — same cadence for the room-artifact outbox.
        try:
            await self._flush_outbox_artifacts()
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("artifact_flush_failed", error=str(exc))

    async def _flush_memory_updates(self) -> None:
        """Detect ``memory/notes.md`` changes for each running agent and
        emit ``agent_memory_update`` frames.

        Direction (#237 plan §3.2 decision 4): file is the runtime
        truth; the cluster's ``agents.memory_md`` is the snapshot. We
        hash the file body and only send when the hash differs from
        the last one we sent. Empty / missing files are reported as
        empty strings on first observation so the server can clear
        stale snapshots — but only once (the cached "" hash suppresses
        repeats).
        """
        import hashlib

        for info in self._spawner.list_running():
            agent_id = info["agent_id"]
            try:
                agent_root = self._spawner.get_agent_root(agent_id)
            except AttributeError:
                # Tests may stub the spawner with MagicMock; in that case
                # the accessor is missing entirely and there's nothing
                # to sync. Skipping silently keeps existing tests happy.
                continue
            notes_path = agent_root / "memory" / "notes.md"
            # File won't exist yet when the agent's spawn path was
            # skipped (e.g. unit tests with stubbed spawner). Skip the
            # frame entirely in that case — the first real sync will
            # fire as soon as materialize lays the empty file.
            if not notes_path.is_file():
                continue
            try:
                body = notes_path.read_text()
            except OSError as exc:
                log.warning(
                    "memory_read_failed", agent_id=agent_id, error=str(exc)
                )
                continue
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if self._memory_last_hash.get(agent_id) == digest:
                continue
            self._memory_last_hash[agent_id] = digest
            from anygarden_machine.protocol.frames import AgentMemoryUpdateFrame

            frame = AgentMemoryUpdateFrame(agent_id=agent_id, memory_md=body)
            await self._send(frame.model_dump())
            log.info(
                "memory_synced",
                agent_id=agent_id,
                bytes=len(body),
            )

    async def _flush_outbox_artifacts(self) -> None:
        """Detect new / changed files under each running agent's
        ``memory/outbox/`` and emit ``room_artifact_produced`` frames.

        Mirrors :meth:`_flush_memory_updates` but the body is binary
        (base64'd on the wire) and the cache is keyed by filename
        because one agent can drop many artifacts in parallel.

        Skipped silently:
          - non-regular entries (subdirs, symlinks)
          - filenames that look path-traversal-y (``/`` or
            ``..``-anchored — shouldn't happen since we only
            ``listdir`` the outbox, but defence in depth)
          - files exceeding ``ARTIFACT_MAX_BYTES`` (just logged so
            the operator can spot oversize uploads)
          - MIME types outside ``ARTIFACT_ALLOWED_MIMES``

        Re-delivery is intentional after a daemon restart (the cache
        starts empty); the cluster's ``UniqueConstraint(room_id,
        sha256)`` makes that a server-side no-op.
        """
        import base64
        import hashlib
        import mimetypes

        for info in self._spawner.list_running():
            agent_id = info["agent_id"]
            try:
                agent_root = self._spawner.get_agent_root(agent_id)
            except AttributeError:
                # Stubbed spawner in tests — same skip rationale as
                # ``_flush_memory_updates``.
                continue
            outbox = agent_root / "memory" / "outbox"
            if not outbox.is_dir():
                continue

            for entry in sorted(outbox.iterdir()):
                if not entry.is_file() or entry.is_symlink():
                    continue
                name = entry.name
                if "/" in name or name in ("", ".", ".."):
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    continue
                if size > ARTIFACT_MAX_BYTES:
                    log.warning(
                        "outbox_too_large",
                        agent_id=agent_id,
                        filename=name,
                        size_bytes=size,
                        cap=ARTIFACT_MAX_BYTES,
                    )
                    continue
                mime, _ = mimetypes.guess_type(name)
                if mime not in ARTIFACT_ALLOWED_MIMES:
                    # Unknown / disallowed MIME — log once per file
                    # change. Use a sentinel hash entry so we don't
                    # spam the log on every report tick.
                    sentinel = f"unsupported:{mime}"
                    if (
                        self._artifact_last_hash.get((agent_id, name))
                        != sentinel
                    ):
                        log.info(
                            "outbox_skipped_mime",
                            agent_id=agent_id,
                            filename=name,
                            mime=mime,
                        )
                        self._artifact_last_hash[(agent_id, name)] = sentinel
                    continue
                try:
                    raw = entry.read_bytes()
                except OSError as exc:
                    log.warning(
                        "outbox_read_failed",
                        agent_id=agent_id,
                        filename=name,
                        error=str(exc),
                    )
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                if self._artifact_last_hash.get((agent_id, name)) == digest:
                    continue
                self._artifact_last_hash[(agent_id, name)] = digest

                from anygarden_machine.protocol.frames import RoomArtifactProducedFrame

                frame = RoomArtifactProducedFrame(
                    agent_id=agent_id,
                    filename=name,
                    mime=mime,
                    content_b64=base64.b64encode(raw).decode("ascii"),
                    sha256=digest,
                    size_bytes=size,
                )
                await self._send(frame.model_dump())
                log.info(
                    "outbox_artifact_synced",
                    agent_id=agent_id,
                    filename=name,
                    mime=mime,
                    size_bytes=size,
                )

    # ── Transitional state helpers (#219) ──────────────────────────────

    def _mark_transitional(self, agent_id: str, state: str) -> None:
        """Annotate *agent_id* with a transitional ``state`` and remember
        when the annotation was set so ``_prune_stale_transitional`` can
        reclaim it if the matching callback never arrives."""
        self._transitional_states[agent_id] = state
        self._transitional_set_at[agent_id] = time.time()

    def _clear_transitional(self, agent_id: str) -> None:
        """Drop both the state and the timestamp; safe if absent."""
        self._transitional_states.pop(agent_id, None)
        self._transitional_set_at.pop(agent_id, None)

    def _prune_stale_transitional(self, now: float) -> None:
        """Reclaim annotations stuck past ``TRANSITIONAL_LEAK_GRACE`` with
        no running process — belt-and-suspenders for missed callbacks."""
        running_ids = {
            info["agent_id"] for info in self._spawner.list_running()
        }
        stale = [
            aid
            for aid, ts in self._transitional_set_at.items()
            if aid not in running_ids
            and now - ts > TRANSITIONAL_LEAK_GRACE
        ]
        for aid in stale:
            log.warning(
                "transitional_leak_reclaimed",
                agent_id=aid,
                state=self._transitional_states.get(aid),
            )
            self._clear_transitional(aid)

    # ── Drain & rotate ─────────────────────────────────────────────────

    async def _handle_drain(self) -> None:
        """Handle drain command: stop accepting new agents and kill existing ones."""
        self._draining = True
        log.info("drain_started")
        await self._spawner.drain()

    async def _handle_rotate_token(self, frame: Any) -> None:
        """Handle rotate_token command: persist the new token and update memory."""
        new_token = frame.new_token
        try:
            save_token(new_token, path=self._token_path)
        except Exception as exc:
            log.error("rotate_token_save_failed", error=str(exc))
            return
        self.machine_token = new_token
        log.info("rotate_token_applied")

    # ── Room shared file handlers (#246) ───────────────────────────────

    async def _handle_agent_memory_shared_file_write(
        self, frame: AgentMemorySharedFileWriteFrame
    ) -> None:
        """Materialize a room-shared file under
        ``<agent_root>/memory/shared/<storage_name>``.

        Idempotent by design — if the on-disk file already matches
        ``frame.content_sha256`` the write is skipped so backfill /
        reconnect storms don't churn the filesystem.
        """
        import hashlib

        try:
            agent_root = self._spawner.get_agent_root(frame.agent_id)
        except (AttributeError, KeyError):
            # Unknown agent (stopped, not yet spawned, or a test stub
            # without the accessor) — drop silently; the server will
            # re-deliver once the agent is back.
            return

        # Defensive: refuse path components in ``storage_name``. The
        # server already sanitises, but the daemon runs unprivileged
        # filesystem writes that shouldn't implicitly trust upstream
        # strings.
        if "/" in frame.storage_name or frame.storage_name in ("", ".", ".."):
            log.warning(
                "shared_file_write_rejected",
                agent_id=frame.agent_id,
                storage_name=frame.storage_name,
            )
            return

        shared_dir = agent_root / "memory" / "shared"
        shared_dir.mkdir(parents=True, exist_ok=True)
        target = shared_dir / frame.storage_name

        if target.exists():
            existing = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing == frame.content_sha256:
                return

        target.write_text(frame.content, encoding="utf-8")
        log.info(
            "shared_file_written",
            agent_id=frame.agent_id,
            storage_name=frame.storage_name,
        )

    async def _handle_agent_memory_shared_file_delete(
        self, frame: AgentMemorySharedFileDeleteFrame
    ) -> None:
        """Remove a room-shared file if present. No-op when absent so
        redundant delete frames are safe to re-issue."""
        try:
            agent_root = self._spawner.get_agent_root(frame.agent_id)
        except (AttributeError, KeyError):
            return

        if "/" in frame.storage_name or frame.storage_name in ("", ".", ".."):
            return

        target = agent_root / "memory" / "shared" / frame.storage_name
        target.unlink(missing_ok=True)

    # ── WebSocket send ─────────────────────────────────────────────────

    async def _send(self, data: dict) -> None:
        """Send a JSON frame over the WebSocket."""
        if self._ws is None:
            log.warning("send_no_ws", frame_type=data.get("type"))
            return
        try:
            await self._ws.send(json.dumps(data))
        except Exception as exc:
            log.error("send_error", error=str(exc), frame_type=data.get("type"))
