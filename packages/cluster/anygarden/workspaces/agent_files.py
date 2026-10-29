"""Move one work file between an agent's machine and the server (#914, #975).

The admin file manager and the agent's own artifact tools share this path:
the server hands the machine a one-time ticket over its WebSocket and the
bytes travel over HTTP into or out of the spool directory. Callers get
domain errors and map them to their own surface (HTTP codes for the admin
API, tool error codes for MCP).

A ticket is committed in its own session (``transfers.issue`` commits), so
call these outside any transaction you want to keep open.
"""

from __future__ import annotations

import asyncio

from anygarden.db.models import Agent, Machine
from anygarden.machine.managed_workspace import (
    CAPABILITY,
    EDIT_CAPABILITY,
    WorkspaceSnapshot,
    path_parts,
)
from anygarden.machine.workspace_transfer import CAPABILITY as TRANSFER_CAPABILITY
from anygarden.scheduler.machine_bus import MachineRequestError
from anygarden.workspaces import transfers

_TRANSFER_BASE_SECONDS = 30.0
_TRANSFER_SECONDS_PER_MIB = 10.0
_TRANSFER_MAX_SECONDS = 600.0


class AgentFileError(Exception):
    """``status``: ``invalid_path``, ``no_agent``, ``not_placed``, ``offline``,
    ``unsupported``, ``timeout``, ``machine_<reason>`` or a machine transfer
    status (``not_found``, ``too_large``, ``conflict``, ``stale`` ...)."""

    def __init__(self, status: str, detail: str = "") -> None:
        super().__init__(detail or status)
        self.status = status
        self.detail = detail or status


def transfer_timeout(size: int) -> float:
    return min(
        _TRANSFER_MAX_SECONDS,
        _TRANSFER_BASE_SECONDS + _TRANSFER_SECONDS_PER_MIB * size / (1024 * 1024),
    )


async def placement(
    db, bus, agent_id: str, path: str, *, allow_root: bool = False
) -> tuple[Machine, int]:
    """Return ``(machine, generation)`` for a transfer, or raise."""
    try:
        parts = path_parts(path)
    except ValueError as exc:
        raise AgentFileError("invalid_path", "Invalid workspace path") from exc
    if not parts and not allow_root:
        raise AgentFileError("invalid_path", "Invalid workspace path")
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise AgentFileError("no_agent", "Agent not found")
    if agent.placed_on_machine_id is None:
        raise AgentFileError("not_placed", "The agent has no execution machine")
    machine = await db.get(Machine, agent.placed_on_machine_id)
    if machine is None or not bus.is_connected(machine.id):
        raise AgentFileError("offline", "The agent's machine is offline")
    if TRANSFER_CAPABILITY not in (machine.control_capabilities or []):
        raise AgentFileError("unsupported", "Update this machine to transfer large files")
    return machine, int(agent.generation or 0)


async def run_transfer(
    bus, machine_id: str, agent_id: str, ticket: transfers.Ticket,
    frame: dict, timeout: float,
) -> dict:
    try:
        return await bus.request_transfer(
            machine_id,
            {
                "type": "workspace_transfer_request",
                "agent_id": agent_id,
                "ticket_id": ticket.id,
                "ticket_token": ticket.token,
                **frame,
            },
            timeout=timeout,
        )
    except MachineRequestError as exc:
        raise AgentFileError(
            "timeout" if exc.reason == "timeout" else f"machine_{exc.reason}",
            f"The machine did not finish the transfer ({exc.reason})",
        ) from None


async def fetch_agent_file(app, *, agent_id: str, path: str, max_bytes: int) -> bytes:
    """Read one regular file from the agent's working directory."""
    bus = app.state.machine_bus
    spool_dir = app.state.config.transfer_spool_dir
    async with app.state.session_factory() as db:
        machine, generation = await placement(db, bus, agent_id, path)
        machine_id = machine.id  # ``issue`` commits and expires the row
        ticket = await transfers.issue(
            db, machine_id=machine_id, agent_id=agent_id, direction="from_machine",
            path=path, max_bytes=max_bytes,
        )
    try:
        result = await run_transfer(
            bus, machine_id, agent_id, ticket,
            {"generation": generation, "operation": "send", "path": path,
             "max_bytes": max_bytes},
            transfer_timeout(max_bytes),
        )
        status = result.get("status", "failed")
        if status != "ready":
            raise AgentFileError(status, f"Transfer refused ({status})")
        spooled = transfers.spool_path(spool_dir, ticket.spool_name)
        if not spooled.is_file():
            raise AgentFileError("failed", "The transfer did not arrive")
        data = await asyncio.to_thread(spooled.read_bytes)
        if len(data) > max_bytes:
            raise AgentFileError("too_large", "Transfer refused (too_large)")
        return data
    finally:
        async with app.state.session_factory() as db:
            await transfers.discard(db, spool_dir, ticket.id)


async def send_agent_file(app, *, agent_id: str, path: str, data: bytes) -> None:
    """Create one new file in the agent's working directory.

    An existing file is never replaced (status ``conflict``).
    """
    bus = app.state.machine_bus
    config = app.state.config
    async with app.state.session_factory() as db:
        machine, generation = await placement(db, bus, agent_id, path)
        machine_id = machine.id
        capabilities = set(machine.control_capabilities or [])
        if not {CAPABILITY, EDIT_CAPABILITY} <= capabilities:
            raise AgentFileError("unsupported", "This machine does not accept file changes")
        if len(data) > config.workspace_transfer_max_bytes:
            raise AgentFileError("too_large", "File is larger than allowed")
        await db.rollback()
    try:
        listing = await bus.request_workspace(
            machine_id, agent_id=agent_id, generation=generation,
            operation="list", path="",
        )
    except MachineRequestError as exc:
        raise AgentFileError(f"machine_{exc.reason}", "The machine did not answer") from None
    snapshot = WorkspaceSnapshot.model_validate(listing)
    if snapshot.status != "ready" or snapshot.edit_token is None:
        raise AgentFileError(snapshot.status, f"Workspace unavailable ({snapshot.status})")
    async with app.state.session_factory() as db:
        ticket = await transfers.issue(
            db, machine_id=machine_id, agent_id=agent_id, direction="to_machine",
            path=path, max_bytes=config.workspace_transfer_max_bytes,
        )

    async def chunks():
        yield data

    try:
        size, sha256 = await transfers.write_spool(
            config.transfer_spool_dir, ticket.spool_name, chunks(),
            config.workspace_transfer_max_bytes,
        )
        result = await run_transfer(
            bus, machine_id, agent_id, ticket,
            {
                "generation": generation, "operation": "receive", "path": path,
                "max_bytes": config.workspace_transfer_max_bytes,
                "edit_token": snapshot.edit_token, "size": size, "sha256": sha256,
            },
            transfer_timeout(size),
        )
    finally:
        async with app.state.session_factory() as db:
            await transfers.discard(db, config.transfer_spool_dir, ticket.id)
    status = result.get("status", "failed")
    if status != "ready":
        raise AgentFileError(status, f"Transfer refused ({status})")
