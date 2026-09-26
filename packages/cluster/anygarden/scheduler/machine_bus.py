"""In-memory pool of active Machine WebSocket connections."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import structlog
from fastapi import WebSocket

from anygarden.scheduler.execution import LocalFrameReceiver

logger = structlog.get_logger(__name__)


class MachineRequestError(RuntimeError):
    """A bounded, read-only machine query could not complete."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class MachineBus:
    """Maintains a map of ``machine_id → WebSocket`` for connected daemons."""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._local: dict[str, LocalFrameReceiver] = {}
        self._lock = asyncio.Lock()
        self._pending: dict[str, tuple[str, str, int, asyncio.Future[dict[str, Any]]]] = {}

    async def register(self, machine_id: str, ws: WebSocket) -> None:
        """Register a machine daemon WebSocket (called from the handler)."""
        async with self._lock:
            if machine_id in self._local:
                raise ValueError("Local machine ownership cannot be replaced by a daemon")
            self._fail_pending(machine_id)
            self._connections[machine_id] = ws
        logger.info("machine_bus.register", machine_id=machine_id)

    async def register_local(self, machine_id: str, receiver: LocalFrameReceiver) -> None:
        async with self._lock:
            if machine_id in self._connections or machine_id in self._local:
                raise ValueError("Machine already has an execution owner")
            self._local[machine_id] = receiver

    async def unregister_local(self, machine_id: str, receiver: LocalFrameReceiver) -> None:
        async with self._lock:
            if self._local.get(machine_id) is receiver:
                del self._local[machine_id]
                self._fail_pending(machine_id)

    async def unregister(self, machine_id: str) -> None:
        """Remove a machine daemon WebSocket on disconnect."""
        async with self._lock:
            self._connections.pop(machine_id, None)
            self._fail_pending(machine_id)
        logger.info("machine_bus.unregister", machine_id=machine_id)

    async def disconnect(self, machine_id: str) -> bool:
        """Forcibly close a daemon WebSocket and remove it from the pool.

        Used by ``delete_machine`` and ``regenerate_token`` to ensure the
        daemon stops using a now-invalid identity. Returns ``True`` if a
        connection was found and closed.
        """
        async with self._lock:
            ws = self._connections.pop(machine_id, None)
            self._fail_pending(machine_id)
        if ws is None:
            return False
        try:
            await ws.close(code=4001, reason="machine_invalidated")
        except Exception:
            logger.warning("machine_bus.disconnect_failed", machine_id=machine_id)
        logger.info("machine_bus.disconnect", machine_id=machine_id)
        return True

    def is_connected(self, machine_id: str) -> bool:
        """Return ``True`` if the machine has an active WS connection."""
        return machine_id in self._connections or machine_id in self._local

    async def send(self, machine_id: str, frame: dict[str, Any]) -> bool:
        """Send a JSON frame to a machine daemon.  Returns ``True`` on success."""
        async with self._lock:
            local = self._local.get(machine_id)
            ws = self._connections.get(machine_id)
        if local is not None:
            return await local.send(frame)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(frame))
            return True
        except Exception:
            logger.warning("machine_bus.send_failed", machine_id=machine_id)
            return False

    def _fail_pending(self, machine_id: str) -> None:
        for owner, _agent_id, _generation, future in self._pending.values():
            if owner == machine_id and not future.done():
                future.set_exception(MachineRequestError("offline"))

    async def request_workspace(
        self, machine_id: str, *, agent_id: str, generation: int,
        operation: str, path: str, cursor: str | None = None, timeout: float = 5,
    ) -> dict[str, Any]:
        """Correlate a bounded read query over either owned transport."""
        if len(self._pending) >= 32:
            raise MachineRequestError("busy")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (machine_id, agent_id, generation, future)
        try:
            try:
                async with asyncio.timeout(timeout):
                    sent = await self.send(machine_id, {
                        "type": "managed_workspace_request", "request_id": request_id,
                        "agent_id": agent_id, "generation": generation,
                        "operation": operation, "path": path, "cursor": cursor,
                    })
                    if not sent:
                        raise MachineRequestError("offline")
                    return await future
            except TimeoutError as exc:
                raise MachineRequestError("timeout") from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Also retrieve errors when send itself fails.

    def resolve_workspace(self, machine_id: str, data: dict[str, Any]) -> bool:
        pending = self._pending.get(data.get("request_id", ""))
        if pending is None:
            return False
        owner, agent_id, generation, future = pending
        if future.done() or (owner, agent_id, generation) != (
            machine_id, data.get("agent_id"), data.get("generation")
        ):
            return False
        future.set_result(data["snapshot"])
        return True

    def connected_ids(self) -> set[str]:
        """Return the set of currently connected machine IDs."""
        return set(self._connections) | set(self._local)
