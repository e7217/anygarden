"""Delivery interface shared by local execution and remote machine transport."""

from typing import Any, Protocol


class ExecutionBus(Protocol):
    """Acceptance is delivery acknowledgement, never execution completion."""

    async def send(self, machine_id: str, frame: dict[str, Any]) -> bool: ...
    def is_connected(self, machine_id: str) -> bool: ...
    def connected_ids(self) -> set[str]: ...


class LocalFrameReceiver(Protocol):
    async def send(self, frame: dict[str, Any]) -> bool: ...
