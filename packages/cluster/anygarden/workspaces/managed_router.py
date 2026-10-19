"""Admin-only access to runtime-reported files on the execution machine."""

from __future__ import annotations

import base64
import mimetypes
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent, Machine
from anygarden.dependencies import get_admin_identity, get_db
from anygarden.machine.managed_workspace import (
    CAPABILITY,
    EDIT_CAPABILITY,
    FILES_CAPABILITY,
    MAX_UPLOAD_ENCODED,
    PREVIEW_BYTES,
    WorkspaceSnapshot,
    path_parts,
)
from anygarden.machine.workspace_transfer import CAPABILITY as TRANSFER_CAPABILITY
from anygarden.scheduler.machine_bus import MachineRequestError
from anygarden.workspaces import transfers

router = APIRouter()

EDIT_OPERATIONS = frozenset({"mkdir", "upload", "write", "delete", "rename"})
FILE_OPERATIONS = frozenset({"fetch", "delete", "rename"})


class ManagedWorkspaceOut(BaseModel):
    status: str
    machine_id: str | None = None
    machine_name: str | None = None
    agent_state: str
    snapshot: WorkspaceSnapshot | None = None
    can_edit: bool = False
    # #908 — download, delete and rename (``managed_workspace_files_v2``).
    can_manage_files: bool = False
    # #914 — large files and folder zips over HTTP; limits in bytes.
    can_transfer: bool = False
    transfer_max_bytes: int | None = None
    archive_max_bytes: int | None = None


class FolderInput(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    edit_token: str = Field(min_length=64, max_length=64)


class UploadInput(FolderInput):
    content_base64: str = Field(max_length=MAX_UPLOAD_ENCODED)


class RenameInput(FolderInput):
    new_name: str = Field(min_length=1, max_length=255)


class TextInput(FolderInput):
    text: str = Field(max_length=PREVIEW_BYTES)
    expected_sha256: str = Field(min_length=6, max_length=64)


async def _query(
    request: Request,
    db: AsyncSession,
    agent_id: str,
    operation: Literal[
        "list", "read", "fetch", "mkdir", "upload", "write", "delete", "rename"
    ],
    path: str,
    cursor: str | None,
    *,
    edit_token: str | None = None,
    content_base64: str | None = None,
    text: str | None = None,
    expected_sha256: str | None = None,
    new_name: str | None = None,
) -> ManagedWorkspaceOut:
    try:
        parts = path_parts(path)
        if operation != "list" and not parts:
            raise ValueError("file required")
        if operation == "rename":
            if new_name is None or "/" in new_name or len(path_parts(new_name)) != 1:
                raise ValueError("invalid name")
        if operation in EDIT_OPERATIONS and (
            edit_token is None
            or len(edit_token) != 64
            or any(c not in "0123456789abcdef" for c in edit_token)
        ):
            raise ValueError("invalid workspace token")
        if operation == "write" and (
            expected_sha256 != "absent"
            and (
                expected_sha256 is None
                or len(expected_sha256) != 64
                or any(c not in "0123456789abcdef" for c in expected_sha256)
            )
        ):
            raise ValueError("invalid file revision")
        if (
            operation == "write"
            and text is not None
            and len(text.encode("utf-8")) > PREVIEW_BYTES
        ):
            raise ValueError("file too large")
        if cursor is not None and (
            not cursor
            or "/" in cursor
            or "\\" in cursor
            or any(ord(c) < 32 for c in cursor)
        ):
            raise ValueError("invalid cursor")
    except ValueError as exc:
        raise HTTPException(422, "Invalid workspace path") from exc
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    machine_id = agent.placed_on_machine_id
    generation = agent.generation
    result = ManagedWorkspaceOut(status="not_placed", agent_state=agent.actual_state)
    if machine_id is None:
        return result
    machine = await db.get(Machine, machine_id)
    result.machine_id = machine_id
    result.machine_name = machine.name if machine else None
    bus = request.app.state.machine_bus
    if machine is None or not bus.is_connected(machine_id):
        result.status = "offline"
        return result
    if CAPABILITY not in (machine.control_capabilities or []):
        result.status = "unsupported"
        return result
    capabilities = set(machine.control_capabilities or [])
    result.can_edit = EDIT_CAPABILITY in capabilities
    result.can_manage_files = FILES_CAPABILITY in capabilities
    if TRANSFER_CAPABILITY in capabilities:
        config = request.app.state.config
        result.can_transfer = True
        result.transfer_max_bytes = config.workspace_transfer_max_bytes
        result.archive_max_bytes = config.workspace_archive_max_bytes
    if operation in EDIT_OPERATIONS and not result.can_edit:
        result.status = "unsupported"
        return result
    if operation in FILE_OPERATIONS and not result.can_manage_files:
        result.status = "unsupported"
        return result
    # Release the read transaction while waiting on another machine. A fresh
    # read below must observe placement/generation changes during that wait.
    await db.rollback()
    try:
        raw = await bus.request_workspace(
            machine_id,
            agent_id=agent_id,
            generation=generation,
            operation=operation,
            path=path,
            cursor=cursor,
            edit_token=edit_token,
            content_base64=content_base64,
            text=text,
            expected_sha256=expected_sha256,
            new_name=new_name,
        )
    except MachineRequestError as exc:
        result.status = exc.reason
        return result
    current = (
        await db.execute(
            select(
                Agent.placed_on_machine_id,
                Agent.generation,
                Agent.actual_state,
            ).where(Agent.id == agent_id)
        )
    ).first()
    if current is None or (current.placed_on_machine_id, current.generation) != (
        machine_id,
        generation,
    ):
        raise HTTPException(409, "Workspace changed. Refresh and try again.")
    result.agent_state = current.actual_state
    result.snapshot = WorkspaceSnapshot.model_validate(raw)
    result.status = result.snapshot.status
    if operation in EDIT_OPERATIONS and result.status == "conflict":
        raise HTTPException(409, "File or folder changed. Refresh before saving.")
    return result


@router.get("/{agent_id}/workspace", response_model=ManagedWorkspaceOut)
async def list_managed_workspace(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
    path: Annotated[str, Query(max_length=1024)] = "",
    cursor: Annotated[str | None, Query(max_length=255)] = None,
):
    return await _query(request, db, agent_id, "list", path, cursor)


@router.get("/{agent_id}/workspace/file", response_model=ManagedWorkspaceOut)
async def read_managed_workspace(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
    path: Annotated[str, Query(min_length=1, max_length=1024)],
):
    return await _query(request, db, agent_id, "read", path, None)


@router.post("/{agent_id}/workspace/folder", response_model=ManagedWorkspaceOut)
async def create_managed_folder(
    agent_id: str,
    body: FolderInput,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _query(
        request,
        db,
        agent_id,
        "mkdir",
        body.path,
        None,
        edit_token=body.edit_token,
    )


@router.post("/{agent_id}/workspace/upload", response_model=ManagedWorkspaceOut)
async def upload_managed_file(
    agent_id: str,
    body: UploadInput,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _query(
        request,
        db,
        agent_id,
        "upload",
        body.path,
        None,
        edit_token=body.edit_token,
        content_base64=body.content_base64,
    )


@router.put("/{agent_id}/workspace/file", response_model=ManagedWorkspaceOut)
async def write_managed_file(
    agent_id: str,
    body: TextInput,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _query(
        request,
        db,
        agent_id,
        "write",
        body.path,
        None,
        edit_token=body.edit_token,
        text=body.text,
        expected_sha256=body.expected_sha256,
    )


# ── #908 file management ───────────────────────────────────────────


@router.get("/{agent_id}/workspace/download")
async def download_managed_file(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
    path: Annotated[str, Query(min_length=1, max_length=1024)],
):
    """Send one work file from the agent's machine as an attachment."""
    result = await _query(request, db, agent_id, "fetch", path, None)
    snapshot = result.snapshot
    if result.status != "ready" or snapshot is None:
        raise HTTPException(409 if result.status in {"offline", "unsupported", "stale"} else 404,
                            f"File unavailable ({result.status})")
    if snapshot.content_base64 is None:
        raise HTTPException(413, "File is larger than the download limit")
    name = path.rsplit("/", 1)[-1]
    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return Response(
        content=base64.b64decode(snapshot.content_base64),
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


class DeleteInput(FolderInput):
    pass


@router.post("/{agent_id}/workspace/delete", response_model=ManagedWorkspaceOut)
async def delete_managed_entry(
    agent_id: str,
    body: DeleteInput,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a work file or folder (folders up to 1000 entries)."""
    return await _query(
        request, db, agent_id, "delete", body.path, None, edit_token=body.edit_token
    )


@router.post("/{agent_id}/workspace/rename", response_model=ManagedWorkspaceOut)
async def rename_managed_entry(
    agent_id: str,
    body: RenameInput,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Rename a work file or folder within its folder."""
    return await _query(
        request, db, agent_id, "rename", body.path, None,
        edit_token=body.edit_token, new_name=body.new_name,
    )


# ── #914 large transfers over HTTP ─────────────────────────────────

# Seconds allowed per MiB on top of a fixed allowance; capped.
_TRANSFER_BASE_SECONDS = 30.0
_TRANSFER_SECONDS_PER_MIB = 10.0
_TRANSFER_MAX_SECONDS = 600.0


def _transfer_timeout(size: int) -> float:
    return min(
        _TRANSFER_MAX_SECONDS,
        _TRANSFER_BASE_SECONDS + _TRANSFER_SECONDS_PER_MIB * size / (1024 * 1024),
    )


async def _transfer_placement(
    request: Request, db: AsyncSession, agent_id: str, path: str, *, allow_root: bool = False
) -> tuple[str, int]:
    """Return ``(machine_id, generation)`` for a transfer, or raise."""
    try:
        parts = path_parts(path)
    except ValueError as exc:
        raise HTTPException(422, "Invalid workspace path") from exc
    if not parts and not allow_root:
        raise HTTPException(422, "Invalid workspace path")
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if agent.placed_on_machine_id is None:
        raise HTTPException(409, "The agent has no execution machine")
    machine = await db.get(Machine, agent.placed_on_machine_id)
    if machine is None or not request.app.state.machine_bus.is_connected(machine.id):
        raise HTTPException(409, "The agent's machine is offline")
    if TRANSFER_CAPABILITY not in (machine.control_capabilities or []):
        raise HTTPException(409, "Update this machine to transfer large files")
    return machine.id, int(agent.generation or 0)


async def _run_transfer(
    request: Request, machine_id: str, agent_id: str, ticket: transfers.Ticket,
    frame: dict, timeout: float,
) -> dict:
    try:
        return await request.app.state.machine_bus.request_transfer(
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
        raise HTTPException(504 if exc.reason == "timeout" else 409,
                            f"The machine did not finish the transfer ({exc.reason})") from None


def _raise_for(status: str) -> None:
    if status == "ready":
        return
    codes = {"not_found": 404, "too_large": 413, "conflict": 409, "stale": 409, "not_ready": 409}
    raise HTTPException(codes.get(status, 422), f"Transfer refused ({status})")


async def _stream_spooled(
    request: Request, db: AsyncSession, ticket: transfers.Ticket, filename: str, media_type: str
) -> FileResponse:
    spool_dir = request.app.state.config.transfer_spool_dir
    path = transfers.spool_path(spool_dir, ticket.spool_name)
    if not path.is_file():
        raise HTTPException(409, "The transfer did not arrive")
    factory = request.app.state.session_factory

    async def discard() -> None:
        async with factory() as cleanup_db:
            await transfers.discard(cleanup_db, spool_dir, ticket.id)

    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(discard),
    )


@router.get("/{agent_id}/workspace/download-stream")
async def download_large_file(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
    path: Annotated[str, Query(min_length=1, max_length=1024)],
):
    """Download a work file up to ``workspace_transfer_max_bytes``."""
    machine_id, generation = await _transfer_placement(request, db, agent_id, path)
    limit = request.app.state.config.workspace_transfer_max_bytes
    ticket = await transfers.issue(
        db, machine_id=machine_id, agent_id=agent_id, direction="from_machine",
        path=path, max_bytes=limit,
    )
    result = await _run_transfer(
        request, machine_id, agent_id, ticket,
        {"generation": generation, "operation": "send", "path": path, "max_bytes": limit},
        _transfer_timeout(limit),
    )
    if result.get("status") != "ready":
        await transfers.discard(db, request.app.state.config.transfer_spool_dir, ticket.id)
    _raise_for(result.get("status", "failed"))
    name = path.rsplit("/", 1)[-1]
    return await _stream_spooled(
        request, db, ticket, name, mimetypes.guess_type(name)[0] or "application/octet-stream"
    )


@router.get("/{agent_id}/workspace/archive")
async def download_folder_archive(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
    path: Annotated[str, Query(max_length=1024)] = "",
):
    """Download a work folder ("" = the whole workspace) as a zip."""
    machine_id, generation = await _transfer_placement(request, db, agent_id, path, allow_root=True)
    config = request.app.state.config
    ticket = await transfers.issue(
        db, machine_id=machine_id, agent_id=agent_id, direction="from_machine",
        path=path, max_bytes=config.workspace_archive_max_bytes,
    )
    result = await _run_transfer(
        request, machine_id, agent_id, ticket,
        {
            "generation": generation, "operation": "archive", "path": path,
            "max_bytes": config.workspace_archive_max_bytes,
            "max_entries": config.workspace_archive_max_entries,
        },
        _transfer_timeout(config.workspace_archive_max_bytes),
    )
    if result.get("status") != "ready":
        await transfers.discard(db, config.transfer_spool_dir, ticket.id)
    _raise_for(result.get("status", "failed"))
    agent = await db.get(Agent, agent_id)
    base = path.rsplit("/", 1)[-1] if path else (agent.name if agent else "workspace")
    return await _stream_spooled(request, db, ticket, f"{base}.zip", "application/zip")


@router.put("/{agent_id}/workspace/upload-stream")
async def upload_large_file(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    edit_token: Annotated[str, Query(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")],
):
    """Upload a work file up to ``workspace_transfer_max_bytes`` (raw body)."""
    machine_id, generation = await _transfer_placement(request, db, agent_id, path)
    agent = await db.get(Agent, agent_id)
    machine = await db.get(Machine, machine_id)
    if EDIT_CAPABILITY not in (machine.control_capabilities or []):
        raise HTTPException(409, "This machine does not accept file changes")
    config = request.app.state.config
    ticket = await transfers.issue(
        db, machine_id=machine_id, agent_id=agent_id, direction="to_machine",
        path=path, max_bytes=config.workspace_transfer_max_bytes,
    )
    try:
        size, sha256 = await transfers.write_spool(
            config.transfer_spool_dir, ticket.spool_name, request.stream(),
            config.workspace_transfer_max_bytes,
        )
    except transfers.SpoolTooLarge:
        await transfers.discard(db, config.transfer_spool_dir, ticket.id)
        raise HTTPException(413, "File is larger than allowed") from None
    try:
        result = await _run_transfer(
            request, machine_id, agent_id, ticket,
            {
                "generation": generation, "operation": "receive", "path": path,
                "max_bytes": config.workspace_transfer_max_bytes,
                "edit_token": edit_token, "size": size, "sha256": sha256,
            },
            _transfer_timeout(size),
        )
    finally:
        await transfers.discard(db, config.transfer_spool_dir, ticket.id)
    _raise_for(result.get("status", "failed"))
    return {"status": "ready", "size": size, "agent_state": agent.actual_state if agent else None}
