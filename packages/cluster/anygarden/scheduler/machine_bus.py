"""In-memory pool of active Machine WebSocket connections."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket
import structlog

logger = structlog.get_logger(__name__)


class MachineBus:
    """Maintains a map of ``machine_id → WebSocket`` for connected daemons."""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def register(self, machine_id: str, ws: WebSocket) -> None:
        """Register a machine daemon WebSocket (called from the handler)."""
        async with self._lock:
            self._connections[machine_id] = ws
        logger.info("machine_bus.register", machine_id=machine_id)

    async def unregister(self, machine_id: str) -> None:
        """Remove a machine daemon WebSocket on disconnect."""
        async with self._lock:
            self._connections.pop(machine_id, None)
        logger.info("machine_bus.unregister", machine_id=machine_id)

    async def disconnect(self, machine_id: str) -> bool:
        """Forcibly close a daemon WebSocket and remove it from the pool.

        Used by ``delete_machine`` and ``regenerate_token`` to ensure the
        daemon stops using a now-invalid identity. Returns ``True`` if a
        connection was found and closed.
        """
        async with self._lock:
            ws = self._connections.pop(machine_id, None)
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
        return machine_id in self._connections

    async def send(self, machine_id: str, frame: dict[str, Any]) -> bool:
        """Send a JSON frame to a machine daemon.  Returns ``True`` on success."""
        async with self._lock:
            ws = self._connections.get(machine_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(frame))
            return True
        except Exception:
            logger.warning("machine_bus.send_failed", machine_id=machine_id)
            return False

    def connected_ids(self) -> set[str]:
        """Return the set of currently connected machine IDs."""
        return set(self._connections.keys())
