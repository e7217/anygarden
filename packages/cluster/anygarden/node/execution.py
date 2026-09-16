"""Local desired-state delivery using the existing reconciler, without daemon WS."""

from __future__ import annotations

import asyncio
import copy
import socket
from pathlib import Path

import structlog
from sqlalchemy import select

from anygarden.db.models import Machine, User
from anygarden.node.ownership import NodeOwnershipError
from anygarden.ws.machine_handler import handle_machine_frame
from anygarden_machine.daemon import MachineDaemon

log = structlog.get_logger(__name__)


class LocalDaemon(MachineDaemon):
    """Reuse manifests, stop fences and spawner; replace only delivery."""

    def __init__(self, *args, receive_frame, **kwargs):
        super().__init__(*args, **kwargs)
        self._receive_frame = receive_frame

    async def _send(self, data: dict) -> None:
        await self._receive_frame(data)


class LocalExecutionBackend:
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
        self.daemon = LocalDaemon(
            server_url=app.state.config.cluster_external_url_or_default(),
            machine_id=self.machine_id,
            machine_token="",  # No network identity/token is created for this row.
            agent_dirs_root=self.data_dir / "agents",
            workspace_registry_path=self.data_dir / "workspace-registry.json",
            workspace_signing_key_path=self.data_dir / "workspace-signing.key",
            receive_frame=self._receive,
        )

    async def start(self) -> None:
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

    def _failed(self, exc: Exception) -> None:
        self.failed = True
        log.error("local_execution.failed", error=type(exc).__name__)
        callback = getattr(self.app.state, "node_shutdown_callback", None)
        if callback is not None:
            callback()

    async def _consume(self) -> None:
        try:
            while True:
                frame = await self.queue.get()
                try:
                    if frame is None:
                        return
                    await self.daemon._handle(frame)
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed(exc)
            raise

    async def _maintain(self) -> None:
        try:
            tick = 0
            while True:
                if self.owner.stop_requested():
                    callback = getattr(self.app.state, "node_shutdown_callback", None)
                    if callback is not None:
                        callback()
                    return
                await self.ensure_registered()
                if self.ready and tick % 20 == 0:
                    await self.daemon._report_actual_state()
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
        await self.daemon.close_local_execution()
        await self.app.state.machine_bus.unregister_local(self.machine_id, self)
        async with self.app.state.session_factory() as db:
            machine = await db.get(Machine, self.machine_id)
            if machine is not None:
                machine.status = "offline"
                await db.commit()
