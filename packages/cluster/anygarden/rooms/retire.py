"""#911 — retire a room the user deleted while keeping its history.

A room with recorded invocation history cannot be hard-deleted: the
accounting rows reference it with ``ondelete=RESTRICT`` and must be kept.
Deleting a room therefore archives it (the archive state already blocks new
messages, turns, tasks and executions) and room listings hide archived rooms.

Work still running in the room is cancelled in the same transaction as the
archive, so no new turn can start in between and the execution cancel still
sees an active operating room.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import AgentTurn, ProjectExecution, Room, Task
from anygarden.project_executions.authorization import ACTIVE_STATES
from anygarden.rooms.service import archive_child_rooms
from anygarden.turns.cancel import (
    TurnCancelError,
    cancel_general_turn,
    is_execution_bound,
)
from anygarden.turns.service import OPEN_TURN_STATES

ROOM_DELETED_REASON = "Room was deleted"
# Executions a cancel can still claim (see ``_claim_mutation``).
_CANCELLABLE_EXECUTION_STATES = ACTIVE_STATES | {"revising"}


class RoomRetireError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass
class RoomRetireResult:
    # Cancelled general turns with the stop target to notify after commit.
    cancelled_turns: list[tuple[AgentTurn, tuple[str, int, int] | None]] = field(default_factory=list)
    cancelled_execution_ids: list[str] = field(default_factory=list)


async def _active_execution_ids(db: AsyncSession, room_id: str) -> list[str]:
    """Executions operated from this room or with work assigned in it."""
    in_room = or_(
        ProjectExecution.operating_room_id == room_id,
        ProjectExecution.id.in_(select(Task.execution_id).where(
            Task.room_id == room_id, Task.execution_id.is_not(None))),
        ProjectExecution.id.in_(select(AgentTurn.execution_id).where(
            AgentTurn.room_id == room_id, AgentTurn.execution_id.is_not(None))),
    )
    return list(await db.scalars(
        select(ProjectExecution.id)
        .where(in_room, ProjectExecution.status.in_(_CANCELLABLE_EXECUTION_STATES))
        .order_by(ProjectExecution.created_at)
    ))


async def _cancel_execution(db: AsyncSession, *, identity, execution_id: str) -> None:
    from anygarden.project_executions.mutations import cancel_execution
    from anygarden.project_executions.service import ExecutionConflict

    execution = await db.get(ProjectExecution, execution_id, populate_existing=True)
    body = {
        "operation_id": str(uuid4()),
        "expected_input_revision": execution.input_revision,
        "expected_state_revision": execution.state_revision,
        "reason": ROOM_DELETED_REASON,
    }
    try:
        await cancel_execution(db, identity=identity, execution_id=execution_id, body=body)
    except (HTTPException, ExecutionConflict) as exc:
        # Someone else's run (no right to cancel it) or a run that changed
        # under us: refuse the delete rather than strand that work.
        raise RoomRetireError(
            "ROOM_HAS_ACTIVE_EXECUTION",
            "A project execution is still running in this room; stop it from its operating room first",
        ) from exc


async def retire_room(db: AsyncSession, *, room: Room, identity) -> RoomRetireResult:
    """Cancel the room's open work, archive it and detach its children.

    Never commits; on ``RoomRetireError`` the caller rolls back so nothing
    changes.
    """
    result = RoomRetireResult()
    for execution_id in await _active_execution_ids(db, room.id):
        await _cancel_execution(db, identity=identity, execution_id=execution_id)
        result.cancelled_execution_ids.append(execution_id)

    open_turns = list(await db.scalars(select(AgentTurn).where(
        AgentTurn.room_id == room.id, AgentTurn.state.in_(OPEN_TURN_STATES),
    )))
    for turn in open_turns:
        if await is_execution_bound(db, turn):
            continue  # fenced by the execution cancel above
        try:
            cancelled = await cancel_general_turn(db, turn.request_id, actor_user_id=identity.id)
        except TurnCancelError:
            continue  # already terminal, or its reply is being committed
        if cancelled.changed:
            result.cancelled_turns.append((cancelled.turn, cancelled.stop_target))

    # #930 — stop the room's campaigns and close their open cycles in this
    # same transaction (CMP-12); cycle executions were cancelled above.
    from anygarden.campaigns.lifecycle import stop_room_campaigns

    await stop_room_campaigns(db, room.id, actor=f"user:{identity.id}")

    room.archived_at = datetime.now(UTC)
    room.archived_by = identity.id
    await archive_child_rooms(db, room.id)
    await db.flush()
    return result
