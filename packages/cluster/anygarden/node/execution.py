"""Local desired-state delivery using the existing reconciler, without daemon WS."""

from __future__ import annotations

import asyncio
import copy
import os
import socket
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import exists, func, select, tuple_, update

from anygarden.db.models import Agent, AgentTurn, AgentTurnAttempt, Machine, User
from anygarden.machine.agent_root_lock import AgentRootLock
from anygarden.machine.daemon import MachineDaemon
from anygarden.node.ownership import NodeOwnershipError
from anygarden.ws.machine_handler import handle_machine_frame

log = structlog.get_logger(__name__)


def _log_best_effort(level: str, event: str, **kwargs) -> None:
    """Log without letting the log write itself end the executor.

    structlog's PrintLogger writes with ``print(..., flush=True)``; on a full
    disk that raises ``OSError(ENOSPC)``. Called from an except handler, such
    a raise used to escape the transient-failure tolerance and permanently
    fail local execution (2026-10-13 benchmark). ``exc_info`` still works
    because this runs inside the caller's except block.
    """
    try:
        getattr(log, level)(event, **kwargs)
    except Exception:  # noqa: BLE001, S110 — losing a log line beats losing the node
        pass


# Operating-system process/bootstrap values only. Application/provider secrets,
# Python import hooks and arbitrary server configuration are never inherited.
AGENT_ENVIRONMENT_KEYS = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)


class LocalDaemon(MachineDaemon):
    """Reuse manifests, stop fences and spawner; replace only delivery."""

    def __init__(self, *args, receive_frame, **kwargs):
        super().__init__(*args, **kwargs)
        self._receive_frame = receive_frame

    async def _send(self, data: dict) -> None:
        await self._receive_frame(data)


class LocalExecutionBackend:
    # Consecutive failed maintenance ticks tolerated before the node gives
    # up (#772). Ticks are 0.25 s apart plus the SQLite lock wait, so this
    # spans far longer than any single writer holds the lock.
    MAX_MAINTENANCE_FAILURES = 40
    # Consecutive frames that failed to apply before the node gives up. A
    # single failure loses only that frame (#772); a run of failures with no
    # success in between means execution is not working at all (e.g. the
    # disk stays full), and a half-dead node must not keep serving.
    MAX_FRAME_FAILURES = 40

    def __init__(self, app, owner):
        self.app = app
        self.owner = owner
        self.machine_id = owner.identity["machine_id"]
        self.data_dir: Path = owner.data_dir
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=256)
        self.tasks: list[asyncio.Task] = []
        self.ready = False
        self.closing = False
        self.failed = False
        self._registration_lock = asyncio.Lock()
        self._receive_lock = asyncio.Lock()
        self.agent_root_lock = AgentRootLock(self.data_dir / "agents")
        self.daemon = LocalDaemon(
            server_url=app.state.config.cluster_external_url_or_default(),
            machine_id=self.machine_id,
            machine_token="",  # No network identity/token is created for this row.
            agent_dirs_root=self.data_dir / "agents",
            workspace_registry_path=self.data_dir / "workspace-registry.json",
            workspace_signing_key_path=self.data_dir / "workspace-signing.key",
            receive_frame=self._receive,
            agent_environment={
                key: os.environ[key]
                for key in AGENT_ENVIRONMENT_KEYS
                if key in os.environ
            },
        )

    async def start(self) -> None:
        # #848 — a standalone machine daemon pointed at this directory would
        # re-adopt and then stop this node's agents. Fail loudly instead.
        self.agent_root_lock.acquire()
        try:
            await self._start()
        except BaseException:
            self.agent_root_lock.release()
            raise

    async def _start(self) -> None:
        # A clean shutdown must remove every runtime receipt. Missing/corrupt
        # evidence on an unclean shutdown is rejected by NodeOwner even earlier.
        if any((self.data_dir / "agents").glob("*/runtime.json")):
            raise NodeOwnershipError(
                "Residual runtime records require recovery before local execution"
            )
        self.app.state.local_machine_id = self.machine_id
        await self.app.state.machine_bus.register_local(self.machine_id, self)
        self.tasks.append(
            asyncio.create_task(self._consume(), name="local-execution-delivery")
        )
        await self.ensure_registered()
        self.tasks.append(
            asyncio.create_task(self._maintain(), name="local-execution-maintenance")
        )

    async def ensure_registered(self) -> None:
        """Bind the internal row to the first real admin, never invent an account."""
        if self.ready or self.closing:
            return
        async with self._registration_lock:
            if self.ready or self.closing:
                return
            async with self.app.state.session_factory() as db:
                machine = await db.get(Machine, self.machine_id)
                if machine is None:
                    admin = (
                        await db.execute(
                            select(User)
                            .where(User.is_admin.is_(True))
                            .order_by(User.created_at, User.id)
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if admin is None:
                        return  # Normal empty-install state until the first signup.
                    machine = Machine(
                        id=self.machine_id,
                        name="Local node",
                        hostname=socket.gethostname(),
                        owner_user_id=admin.id,
                        labels={
                            "anygarden.local_node_id": self.owner.identity["node_id"]
                        },
                    )
                    db.add(machine)
                elif (machine.labels or {}).get(
                    "anygarden.local_node_id"
                ) != self.owner.identity["node_id"]:
                    raise NodeOwnershipError(
                        "Local node/Machine mapping does not match this database"
                    )
                await db.commit()
            await self.daemon._register()
            self.ready = True
            await self.daemon._report_actual_state()

    async def send(self, frame: dict) -> bool:
        if self.closing or self.failed:
            return False
        # Package/token rotation belongs to the remote daemon lifecycle. A
        # local component cannot self-update beneath its live server process.
        if frame.get("type") in {"self_update", "rotate_token"}:
            return False
        try:
            self.queue.put_nowait(copy.deepcopy(frame))
        except asyncio.QueueFull:
            return False
        return True

    async def _receive(self, frame: dict) -> None:
        # Preserve the serial receive ordering of the remote WebSocket handler.
        async with self._receive_lock:
            # This function is reachable only from the owned local reconciler.
            # During shutdown do not let reports dispatch new reconciliation work.
            if self.closing:
                if frame.get("type") == "report_actual_state":
                    await self.app.state.agent_lifecycle.handle_report_actual_state(
                        self.machine_id, frame.get("agents", [])
                    )
                return
            await handle_machine_frame(self.app, self.machine_id, frame)

    async def _handle_frame(self, frame: dict) -> Exception | None:
        """Apply one frame; return the error it failed with, if any.

        #772 — match the remote daemon, which logs a frame it cannot handle
        and keeps reading. A transient error (SQLite "database is locked",
        ``OSError``/ENOSPC, "database or disk is full") loses one frame; the
        periodic state report and lifecycle reconciliation converge the rest.
        Ownership errors still stop the node.
        """
        try:
            await self.daemon._handle(frame)
        except NodeOwnershipError:
            raise
        except Exception as exc:  # noqa: BLE001 — one lost frame, see above
            _log_best_effort(
                "exception",
                "local_execution.frame_failed",
                frame_type=frame.get("type"),
            )
            return exc
        return None

    def _request_shutdown(self) -> None:
        callback = getattr(self.app.state, "node_shutdown_callback", None)
        if callback is not None:
            callback()

    def _failed(self, exc: Exception) -> None:
        # Mark and request shutdown before logging: a failing log write
        # (full disk) must never leave the node half-dead with execution
        # gone but the API still serving.
        self.failed = True
        try:
            self._request_shutdown()
        finally:
            _log_best_effort(
                "error", "local_execution.failed", error=type(exc).__name__
            )

    async def _consume(self) -> None:
        try:
            failures = 0
            while True:
                frame = await self.queue.get()
                try:
                    if frame is None:
                        return
                    error = await self._handle_frame(frame)
                finally:
                    self.queue.task_done()
                if error is None:
                    failures = 0
                    continue
                failures += 1
                if failures >= self.MAX_FRAME_FAILURES:
                    raise error
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed(exc)
            raise

    async def _maintain(self) -> None:
        try:
            tick = 0
            failures = 0
            while True:
                try:
                    # Inside the tolerance: the stop-request check reads a
                    # file and can hit a transient OSError like any write.
                    if self.owner.stop_requested():
                        self._request_shutdown()
                        return
                    await self.ensure_registered()
                    if self.ready and tick % 20 == 0:
                        await self.daemon._report_actual_state()
                    failures = 0
                except NodeOwnershipError:
                    raise
                except Exception:
                    # A busy database must not end the node; the next tick
                    # retries. Only a persistent failure is fatal.
                    failures += 1
                    if failures >= self.MAX_MAINTENANCE_FAILURES:
                        raise
                    _log_best_effort(
                        "warning",
                        "local_execution.maintenance_failed",
                        failures=failures,
                        exc_info=True,
                    )
                tick += 1
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed(exc)
            raise

    async def close(self) -> None:
        self.closing = True
        self.daemon._draining = True
        # Discard accepted but unapplied delivery. The persisted desired state
        # remains authoritative and is reconciled on a clean subsequent start.
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        for task in self.tasks:
            if task.get_name() == "local-execution-maintenance":
                task.cancel()
        self.queue.put_nowait(None)
        await asyncio.gather(*self.tasks, return_exceptions=True)
        # Remember the generations this executor owns before stopping them.
        # A concurrent placement/generation change must not let this shutdown
        # fence a replacement process on another executor.
        async with self.app.state.session_factory() as db:
            owned_generations = list(
                (
                    await db.execute(
                        select(Agent.id, Agent.generation).where(
                            Agent.placed_on_machine_id == self.machine_id,
                        )
                    )
                ).all()
            )
        await self.daemon.close_local_execution()
        # close_local_execution confirms every process group is gone and raises
        # on uncertain cleanup. Never make a retry eligible before that proof.
        await self._recover_closed_turns(owned_generations)
        await self.app.state.machine_bus.unregister_local(self.machine_id, self)
        async with self.app.state.session_factory() as db:
            machine = await db.get(Machine, self.machine_id)
            if machine is not None:
                machine.status = "offline"
                await db.commit()
        # Only after every agent process is confirmed gone.
        self.agent_root_lock.release()

    async def _recover_closed_turns(
        self, owned_generations: list[tuple[str, int]]
    ) -> None:
        """Persist bounded retries after confirmed local process cleanup."""
        if not owned_generations:
            return
        from anygarden.turns.service import (
            ACTIVE_ATTEMPT_STATES,
            OPEN_TURN_STATES,
            recover_stalled_turns,
        )

        now = datetime.now(UTC)
        async with self.app.state.session_factory() as db:
            fenced = await db.execute(
                update(AgentTurnAttempt)
                .where(
                    tuple_(AgentTurnAttempt.agent_id, AgentTurnAttempt.generation).in_(
                        owned_generations
                    ),
                    AgentTurnAttempt.state.in_(ACTIVE_ATTEMPT_STATES),
                    exists(
                        select(AgentTurn.request_id).where(
                            AgentTurn.request_id == AgentTurnAttempt.turn_id,
                            AgentTurn.state.in_(OPEN_TURN_STATES),
                        )
                    ),
                    exists(
                        select(Agent.id).where(
                            Agent.id == AgentTurnAttempt.agent_id,
                            Agent.generation == AgentTurnAttempt.generation,
                            Agent.placed_on_machine_id == self.machine_id,
                        )
                    ),
                )
                .values(
                    lease_expires_at=now,
                    reason=func.coalesce(AgentTurnAttempt.reason, "node_shutdown"),
                )
                .returning(AgentTurnAttempt.id)
            )
            attempt_ids = set(fenced.scalars().all())
            await db.commit()
        if not attempt_ids:
            return
        # No transport delivery on shutdown. The normal next-start dispatcher
        # consumes the newly persisted attempts/outbox, retaining retry limits.
        recovered = await recover_stalled_turns(
            self.app.state.session_factory,
            None,
            now=now,
            attempt_ids=attempt_ids,
        )
        # Shutdown path: a failed log write must not undo confirmed cleanup.
        _log_best_effort(
            "info",
            "local_execution.turns_recovered_after_cleanup",
            attempt_count=len(attempt_ids),
            redispatched=recovered.redispatched,
            cancelled=recovered.cancelled,
            failed=recovered.failed,
        )
