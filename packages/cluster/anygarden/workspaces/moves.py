"""Moving an agent's work files to its new machine (#915).

The old machine packs the work directory (without instructions, memory or
private files) and uploads it over a #914 transfer ticket; the new machine
downloads the same spool file and keeps it for the materializer, which
unpacks it right before the agent starts there. The server never keeps the
files: the spool is removed when the move ends, and the old machine's
copy is left in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, Machine, MachineTransfer
from anygarden.machine.workspace_import import work_dir_name
from anygarden.machine.workspace_transfer import CAPABILITY as TRANSFER_CAPABILITY
from anygarden.scheduler.machine_bus import MachineRequestError
from anygarden.workspaces import transfers

PREVIEW_TIMEOUT = 30.0
_BASE_SECONDS = 30.0
_SECONDS_PER_MIB = 10.0
_MAX_SECONDS = 600.0


class MoveFailed(Exception):
    """Work files could not be moved; ``reason`` is a stable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class MovePreview:
    can_move: bool
    reason: str | None = None
    files: int = 0
    bytes: int = 0
    limit_bytes: int = 0


@dataclass
class MoveResult:
    files: int = 0
    archive_bytes: int = 0  # size of the zip that travelled
    skipped: list[str] = field(default_factory=list)


def _timeout(size: int) -> float:
    return min(_MAX_SECONDS, _BASE_SECONDS + _SECONDS_PER_MIB * size / (1024 * 1024))


def _supports(machine: Machine | None) -> bool:
    return machine is not None and TRANSFER_CAPABILITY in (machine.control_capabilities or [])


async def _request(request: Request, machine_id: str, agent: Agent, frame: dict, timeout: float) -> dict:
    try:
        return await request.app.state.machine_bus.request_transfer(
            machine_id,
            {"type": "workspace_transfer_request", "agent_id": agent.id,
             "generation": int(agent.generation or 0), "path": "", **frame},
            timeout=timeout,
        )
    except MachineRequestError as exc:
        raise MoveFailed(f"machine_{exc.reason}") from None


async def preview(request: Request, db: AsyncSession, agent: Agent, target: Machine) -> MovePreview:
    """Whether the work files can move to ``target``, and how much there is."""
    limit = request.app.state.config.workspace_archive_max_bytes
    source_id = agent.placed_on_machine_id
    if source_id is None:
        return MovePreview(False, "not_placed", limit_bytes=limit)
    source = await db.get(Machine, source_id)
    if source is None or not request.app.state.machine_bus.is_connected(source_id):
        return MovePreview(False, "source_offline", limit_bytes=limit)
    if not _supports(source):
        return MovePreview(False, "source_unsupported", limit_bytes=limit)
    if not _supports(target):
        return MovePreview(False, "target_unsupported", limit_bytes=limit)
    try:
        result = await _request(
            request, source_id, agent,
            {"operation": "measure", "max_bytes": limit, "exclude_memory": True},
            PREVIEW_TIMEOUT,
        )
    except MoveFailed as exc:
        return MovePreview(False, exc.reason, limit_bytes=limit)
    if result.get("status") == "not_ready":
        # The agent never ran there, so there is nothing to carry.
        return MovePreview(False, "nothing", limit_bytes=limit)
    if result.get("status") != "ready":
        return MovePreview(False, f"source_{result.get('status')}", limit_bytes=limit)
    files, size = int(result.get("files") or 0), int(result.get("size") or 0)
    if files == 0:
        return MovePreview(False, "nothing", limit_bytes=limit)
    if size > limit or files > request.app.state.config.workspace_archive_max_entries:
        return MovePreview(False, "too_large", files, size, limit)
    return MovePreview(True, None, files, size, limit)


async def move_work_files(
    request: Request, db: AsyncSession, agent: Agent, source_id: str, target_id: str
) -> MoveResult:
    """Copy the work files from ``source_id`` to ``target_id`` (agent stopped)."""
    config = request.app.state.config
    limit = config.workspace_archive_max_bytes
    upload = await transfers.issue(
        db, machine_id=source_id, agent_id=agent.id, direction="from_machine",
        path="", max_bytes=limit,
    )
    download = None
    try:
        packed = await _request(
            request, source_id, agent,
            {"operation": "archive", "ticket_id": upload.id, "ticket_token": upload.token,
             "max_bytes": limit, "max_entries": config.workspace_archive_max_entries,
             "exclude_memory": True},
            _timeout(limit),
        )
        if packed.get("status") != "ready":
            raise MoveFailed(f"source_{packed.get('status')}")
        row = await db.get(MachineTransfer, upload.id)
        await db.refresh(row)
        if row is None or row.size is None or row.sha256 is None:
            raise MoveFailed("archive_missing")
        size, sha256 = row.size, row.sha256
        download = await transfers.issue(
            db, machine_id=target_id, agent_id=agent.id, direction="to_machine",
            path="", max_bytes=size, spool_name=upload.spool_name,
        )
        received = await _request(
            request, target_id, agent,
            {"operation": "import", "ticket_id": download.id, "ticket_token": download.token,
             "max_bytes": size, "size": size, "sha256": sha256,
             "work_dir": work_dir_name(agent.engine)},
            _timeout(size),
        )
        if received.get("status") != "ready":
            raise MoveFailed(f"target_{received.get('status')}")
        return MoveResult(
            files=int(packed.get("files") or 0), archive_bytes=int(packed.get("size") or 0),
            skipped=list(received.get("skipped") or []),
        )
    finally:
        if download is not None:
            await transfers.discard(db, config.transfer_spool_dir, download.id)
        await transfers.discard(db, config.transfer_spool_dir, upload.id)


async def discard_import(request: Request, agent: Agent, machine_id: str) -> None:
    """Best effort: drop an import a failed move left on ``machine_id``."""
    try:
        await _request(request, machine_id, agent, {"operation": "discard_import", "max_bytes": 1}, PREVIEW_TIMEOUT)
    except MoveFailed:
        pass
