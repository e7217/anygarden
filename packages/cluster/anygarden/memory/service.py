"""Room memory snapshots, revision CAS, and authenticated machine writes.

Reads never fall back to Agent.memory_md. Missing rows are authoritative empty
snapshots; only an explicit administrator import copies the legacy archive.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, AgentRoomMemory, Participant, Room
from anygarden.rooms.authorization import AGENT_EXECUTION_ROLES

SCOPE_VERSION = "room-memory-v1"
MAX_MEMORY_BYTES = 262144
logger = logging.getLogger(__name__)


def _reject(code: str, message: str, *, status: int = 409) -> None:
    raise HTTPException(status, {"code": code, "message": message})


def _validate_room_id(room_id: str) -> None:
    try:
        valid = isinstance(room_id, str) and str(UUID(room_id)) == room_id
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        _reject("MEMORY_ROOM_INVALID", "Memory requires a canonical room UUID", status=400)


def _validate_body(memory_md: str) -> None:
    try:
        valid = isinstance(memory_md, str) and len(memory_md.encode("utf-8")) <= MAX_MEMORY_BYTES
    except UnicodeError:
        valid = False
    if not valid:
        _reject("MEMORY_CONTENT_INVALID", "Memory must be UTF-8 text within 256 KiB", status=400)


async def _scope(db: AsyncSession, agent_id: str, room_id: str) -> tuple[Agent, Room]:
    _validate_room_id(room_id)
    pair = (await db.execute(
        select(Agent, Room)
        .join(Participant, Participant.agent_id == Agent.id)
        .join(Room, Room.id == Participant.room_id)
        .where(Agent.id == agent_id, Room.id == room_id, Room.archived_at.is_(None))
        .execution_options(populate_existing=True)
    )).first()
    if pair is None:
        _reject("MEMORY_SCOPE_UNAVAILABLE", "Agent is not assigned to an active room", status=404)
    return pair[0], pair[1]


def _snapshot(room: Room, row: AgentRoomMemory | None, generation: int) -> dict:
    return {
        "room_id": room.id,
        "memory_md": row.memory_md if row is not None else "",
        "revision": row.revision if row is not None else 0,
        "session_epoch": row.session_epoch if row is not None else 0,
        "generation": generation,
        "scope_version": SCOPE_VERSION,
        "ephemeral": bool(room.ephemeral),
    }


async def get_room_memory(db: AsyncSession, agent_id: str, room_id: str) -> dict:
    """Return an assigned room's snapshot, including explicit empty state."""
    agent, room = await _scope(db, agent_id, room_id)
    row = await db.get(AgentRoomMemory, (agent_id, room_id), populate_existing=True)
    return _snapshot(room, row, agent.generation)


async def get_room_memories(
    db: AsyncSession, agent_id: str, room_ids: list[str], generation: int | None = None
) -> dict[str, dict]:
    """Build a manifest map for current memberships; omit departed/archived rooms."""
    if generation is None:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            return {}
        generation = agent.generation
    rooms = list(await db.scalars(
        select(Room).join(Participant, Participant.room_id == Room.id).where(
            Participant.agent_id == agent_id,
            Room.id.in_(room_ids),
            Room.archived_at.is_(None),
        )
    ))
    rows = {row.room_id: row for row in await db.scalars(select(AgentRoomMemory).where(
        AgentRoomMemory.agent_id == agent_id, AgentRoomMemory.room_id.in_([room.id for room in rooms])
    ))}
    return {room.id: _snapshot(room, rows.get(room.id), generation) for room in rooms}


def _write_scope_guard(
    agent_id: str, room_id: str, *, machine_id: str | None = None, generation: int | None = None
):
    query = select(Participant.id).join(Room, Room.id == Participant.room_id).where(
        Participant.agent_id == agent_id,
        Room.id == room_id,
        Room.archived_at.is_(None),
    )
    if machine_id is not None:
        query = query.join(Agent, Agent.id == Participant.agent_id).where(
            Agent.placed_on_machine_id == machine_id,
            Agent.generation == generation,
            Participant.role.in_(AGENT_EXECUTION_ROLES),
            Room.ephemeral.is_(False),
        )
    return exists(query)


async def update_room_memory(
    db: AsyncSession,
    *,
    agent_id: str,
    room_id: str,
    memory_md: str,
    expected_revision: int,
    administrator: bool,
    machine_id: str | None = None,
    generation: int | None = None,
) -> dict:
    """CAS an exact room row. Administrator changes also invalidate old history."""
    _validate_body(memory_md)
    if type(expected_revision) is not int or expected_revision < 0:
        _reject("MEMORY_REVISION_INVALID", "Memory requires a nonnegative base revision", status=400)
    agent, room = await _scope(db, agent_id, room_id)
    if not administrator:
        if machine_id is None or type(generation) is not int or generation < 0:
            _reject("MEMORY_RUNTIME_PROOF_REQUIRED", "Scoped generation and machine proof are required")
        if agent.placed_on_machine_id != machine_id or agent.generation != generation:
            _reject("MEMORY_RUNTIME_STALE", "Machine placement or generation changed")
        role = await db.scalar(select(Participant.role).where(
            Participant.agent_id == agent_id, Participant.room_id == room_id
        ))
        if role not in AGENT_EXECUTION_ROLES:
            _reject("MEMORY_MEMBERSHIP_INVALID", "Agent cannot persist memory in this room")
        if room.ephemeral:
            _reject("MEMORY_EPHEMERAL", "Agents cannot persist memory in an ephemeral room")

    row = await db.get(AgentRoomMemory, (agent_id, room_id))
    if row is None:
        # Keep a conflicting insert from rolling back the caller's transaction.
        try:
            async with db.begin_nested():
                db.add(AgentRoomMemory(agent_id=agent_id, room_id=room_id))
                await db.flush()
        except IntegrityError:
            pass
    statement = update(AgentRoomMemory).where(
        AgentRoomMemory.agent_id == agent_id,
        AgentRoomMemory.room_id == room_id,
        AgentRoomMemory.revision == expected_revision,
        _write_scope_guard(
            agent_id, room_id,
            machine_id=None if administrator else machine_id,
            generation=generation,
        ),
    ).values(
        memory_md=memory_md,
        revision=AgentRoomMemory.revision + 1,
        session_epoch=AgentRoomMemory.session_epoch + (1 if administrator else 0),
        updated_at=datetime.now(UTC),
    ).execution_options(synchronize_session=False)
    changed = await db.execute(statement)
    if changed.rowcount != 1:
        # Reauthorize before returning the current body; revocation must not leak it.
        current_agent, current_room = await _scope(db, agent_id, room_id)
        if not administrator and (
            current_agent.placed_on_machine_id != machine_id or current_agent.generation != generation
        ):
            _reject("MEMORY_RUNTIME_STALE", "Machine placement or generation changed")
        if not administrator:
            current_role = await db.scalar(select(Participant.role).where(
                Participant.agent_id == agent_id, Participant.room_id == room_id
            ))
            if current_role not in AGENT_EXECUTION_ROLES:
                _reject("MEMORY_MEMBERSHIP_INVALID", "Agent cannot persist memory in this room")
            if current_room.ephemeral:
                _reject("MEMORY_EPHEMERAL", "Agents cannot persist memory in an ephemeral room")
        _reject_snapshot = await get_room_memory(db, agent_id, room_id)
        raise HTTPException(409, {
            "code": "MEMORY_REVISION_CONFLICT",
            "message": "Memory changed; reload the authoritative snapshot before writing",
            "room_memory": _reject_snapshot,
        })
    return await get_room_memory(db, agent_id, room_id)


async def apply_machine_memory_update(db: AsyncSession, *, machine_id: str, data: dict) -> dict:
    """Reject unscoped legacy writes and return an explicit, nonsecret ack."""
    ack = {
        "type": "agent_memory_update_ack",
        "agent_id": data.get("agent_id"),
        "room_id": data.get("room_id"),
        "generation": data.get("generation"),
        "base_revision": data.get("base_revision"),
        "status": "rejected",
        "code": "MEMORY_UPDATE_INVALID",
        "room_memory": None,
    }
    try:
        if not isinstance(data.get("agent_id"), str) or not data["agent_id"]:
            _reject("MEMORY_UPDATE_INVALID", "Agent identity is required", status=400)
        snapshot = await update_room_memory(
            db,
            agent_id=data["agent_id"],
            room_id=data.get("room_id"),
            memory_md=data.get("memory_md"),
            expected_revision=data.get("base_revision"),
            administrator=False,
            machine_id=machine_id,
            generation=data.get("generation"),
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        ack["code"] = detail.get("code", "MEMORY_UPDATE_INVALID")
        if ack["code"] == "MEMORY_REVISION_CONFLICT":
            ack["status"] = "conflict"
            ack["room_memory"] = detail["room_memory"]
    else:
        ack.update(status="accepted", code=None, room_memory=snapshot)
    return ack


async def _push_room_memory(app, db: AsyncSession, *, agent_id: str, room_id: str, machine_snapshot: bool) -> None:
    """After commit, update the placed daemon and only this agent's room socket."""
    try:
        agent, _ = await _scope(db, agent_id, room_id)
        snapshot = await get_room_memory(db, agent_id, room_id)
    except HTTPException:
        # An edit already committed remains successful if membership was
        # removed before fanout; never deliver the body to a revoked scope.
        return
    machine_bus = getattr(app.state, "machine_bus", None)
    if machine_snapshot and agent.placed_on_machine_id and machine_bus is not None:
        await machine_bus.send(agent.placed_on_machine_id, {
            "type": "agent_room_memory_snapshot",
            "agent_id": agent_id,
            "generation": agent.generation,
            "room_memory": snapshot,
        })
    manager = getattr(app.state, "connection_manager", None)
    participant_id = await db.scalar(select(Participant.id).where(
        Participant.agent_id == agent_id, Participant.room_id == room_id
    ))
    if manager is not None and participant_id is not None:
        from anygarden.ws.protocol import RoomMemoryChangedOut

        await manager.send_to(participant_id, RoomMemoryChangedOut(
            agent_id=agent_id, room_id=room_id, room_memory=snapshot
        ), expected_generation=agent.generation)


async def push_room_memory(app, db: AsyncSession, *, agent_id: str, room_id: str, machine_snapshot: bool) -> None:
    """Bounded best-effort fanout cannot turn an already committed write into 500.

    The next scoped welcome or authoritative manifest recovers missing delivery.
    Never log the memory body or an exception containing SQL bind parameters.
    """
    try:
        async with asyncio.timeout(2):
            await _push_room_memory(
                app, db, agent_id=agent_id, room_id=room_id, machine_snapshot=machine_snapshot
            )
    except Exception:  # noqa: BLE001 — postcommit transport failures must not undo success
        logger.warning("memory.snapshot_delivery_deferred", extra={"agent_id": agent_id, "room_id": room_id})
