"""Machine-facing HTTP endpoints for work-file transfers (#914).

Authenticated by the one-time ticket alone (``Authorization: Ticket …``):
the integrated node's local machine has no machine token, and the ticket
reaches the machine only through its authenticated WebSocket.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.dependencies import get_db
from anygarden.workspaces import transfers

router = APIRouter(prefix="/api/v1/machine-transfers", tags=["machine-transfers"])


def _ticket_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() or None if scheme.lower() == "ticket" else None


async def _claim(db: AsyncSession, request: Request, ticket_id: str, direction):
    try:
        return await transfers.claim(db, ticket_id, _ticket_token(request), direction)
    except transfers.TransferRejected as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.put("/{ticket_id}")
async def receive_from_machine(
    ticket_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """The machine uploads a work file or folder zip it was asked for."""
    row = await _claim(db, request, ticket_id, "from_machine")
    spool_dir = request.app.state.config.transfer_spool_dir
    try:
        size, sha256 = await transfers.write_spool(
            spool_dir, row.spool_name, request.stream(), row.max_bytes
        )
    except transfers.SpoolTooLarge:
        row.state = "failed"
        await db.commit()
        raise HTTPException(413, "Transfer is larger than allowed") from None
    row.state = "done"
    row.size = size
    row.sha256 = sha256
    await db.commit()
    return {"size": size, "sha256": sha256}


@router.get("/{ticket_id}")
async def send_to_machine(
    ticket_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """The machine downloads a spooled upload."""
    row = await _claim(db, request, ticket_id, "to_machine")
    path = transfers.spool_path(request.app.state.config.transfer_spool_dir, row.spool_name)
    if not path.is_file():
        raise HTTPException(404, "Transfer data is gone")
    row.state = "done"
    await db.commit()
    return FileResponse(path, media_type="application/octet-stream")
