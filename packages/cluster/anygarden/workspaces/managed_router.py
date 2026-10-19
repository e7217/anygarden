"""Admin-only access to runtime-reported files on the execution machine."""

from __future__ import annotations

import base64
import mimetypes
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
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
from anygarden.scheduler.machine_bus import MachineRequestError

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
