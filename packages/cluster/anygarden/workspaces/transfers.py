"""One-time HTTP transfer tickets between the server and a machine (#914).

A work file larger than one WebSocket frame travels over HTTP instead:

- ``from_machine`` (download, folder zip): the machine PUTs the bytes to
  ``/api/v1/machine-transfers/{id}``; they land in the spool directory and
  the browser request that asked for them streams the spool file back.
- ``to_machine`` (upload): the browser's bytes are spooled first; the
  machine GETs them from the same URL.

The ticket token travels only inside the authenticated machine WebSocket
and is sent back in an ``Authorization: Ticket <token>`` header. Only its
hash is stored. A ticket is bound to one machine, agent, path and
direction, expires after :data:`TICKET_TTL` and is claimed once.

Completion is reported by the machine over its WebSocket (the worker that
holds the browser request may not be the one that received the HTTP
call), so the database only shares the ticket and the spool file name.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import MachineTransfer

TICKET_TTL = timedelta(minutes=2)
# Finished or abandoned tickets and their spool files are removed after this.
RETENTION = timedelta(minutes=15)

Direction = Literal["from_machine", "to_machine"]


class TransferRejected(Exception):
    """The ticket cannot be used; ``status_code`` is the HTTP answer."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class SpoolTooLarge(Exception):
    """More bytes arrived than the ticket allows."""


@dataclass(frozen=True)
class Ticket:
    id: str
    token: str
    spool_name: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def spool_path(spool_dir: Path, spool_name: str) -> Path:
    return spool_dir / spool_name


async def issue(
    db: AsyncSession,
    *,
    machine_id: str,
    agent_id: str,
    direction: Direction,
    path: str,
    max_bytes: int,
    spool_name: str | None = None,
) -> Ticket:
    """Create and commit a ticket; return its id and the plaintext token."""
    token = secrets.token_urlsafe(32)
    name = spool_name or secrets.token_hex(16)
    row = MachineTransfer(
        token_hash=_hash(token),
        machine_id=machine_id,
        agent_id=agent_id,
        direction=direction,
        path=path,
        max_bytes=max_bytes,
        spool_name=name,
        expires_at=datetime.now(timezone.utc) + TICKET_TTL,
    )
    db.add(row)
    await db.commit()
    return Ticket(id=row.id, token=token, spool_name=name)


async def claim(
    db: AsyncSession, ticket_id: str, token: str | None, direction: Direction
) -> MachineTransfer:
    """Mark the ticket in use, refusing a wrong, expired or reused one."""
    row = await db.get(MachineTransfer, ticket_id)
    if row is None or token is None or not hmac.compare_digest(row.token_hash, _hash(token)):
        raise TransferRejected(404, "Unknown transfer")
    if row.direction != direction:
        raise TransferRejected(405, "Wrong transfer direction")
    if row.state != "issued":
        raise TransferRejected(409, "Transfer already used")
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise TransferRejected(410, "Transfer expired")
    row.state = "active"
    await db.commit()
    return row


async def write_spool(
    spool_dir: Path, spool_name: str, chunks: AsyncIterator[bytes], max_bytes: int
) -> tuple[int, str]:
    """Stream ``chunks`` into the spool file; return ``(size, sha256)``.

    The partial file is removed when the limit is exceeded or the stream
    fails.
    """
    spool_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = spool_path(spool_dir, spool_name)
    digest = hashlib.sha256()
    size = 0
    handle = await asyncio.to_thread(open, target, "xb")
    try:
        async for chunk in chunks:
            size += len(chunk)
            if size > max_bytes:
                raise SpoolTooLarge(f"more than {max_bytes} bytes")
            digest.update(chunk)
            await asyncio.to_thread(handle.write, chunk)
        await asyncio.to_thread(handle.flush)
    except BaseException:
        handle.close()
        target.unlink(missing_ok=True)
        raise
    handle.close()
    return size, digest.hexdigest()


async def discard(db: AsyncSession, spool_dir: Path, ticket_id: str) -> None:
    """Drop a ticket and its spool file once the browser request is done."""
    row = await db.get(MachineTransfer, ticket_id)
    if row is None:
        return
    spool_path(spool_dir, row.spool_name).unlink(missing_ok=True)
    await db.delete(row)
    await db.commit()


async def cleanup(db: AsyncSession, spool_dir: Path, *, now: datetime | None = None) -> int:
    """Remove tickets past their retention and any spool file left behind."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - RETENTION
    stale = (
        await db.scalars(select(MachineTransfer).where(MachineTransfer.expires_at < cutoff))
    ).all()
    # Write only when there is something to remove: this runs on every
    # sweeper tick and must not add a write transaction to an idle SQLite.
    if stale:
        for row in stale:
            spool_path(spool_dir, row.spool_name).unlink(missing_ok=True)
        await db.execute(delete(MachineTransfer).where(MachineTransfer.expires_at < cutoff))
        await db.commit()
    if spool_dir.is_dir():
        live = set((await db.scalars(select(MachineTransfer.spool_name))).all())
        for path in spool_dir.iterdir():
            if path.name in live or not path.is_file():
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                path.unlink(missing_ok=True)
    return len(stale)
