"""Helpers that keep Participant inserts and the corresponding WS
notification in lockstep.

Every path that adds someone to a room must (a) insert a ``Participant``
row and (b) poke the newcomer's other WS sessions so they can sync —
agents auto-connect to the new room via ``JoinRoomOut`` (see
``doorae_agent/client.py``) and users refresh their sidebar via
``RoomMembershipChangedOut``. Historically this pair was duplicated in
three places (``rooms/router.py`` add_participant, sub-room creation,
and the ``#room`` auto-join block in ``ws/handler.py``); the last one
forgot the frame, which is what issue #50 set out to fix.

These helpers collapse all three call sites onto the same primitive so
a fourth path added tomorrow can't regress the invariant.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Participant
from doorae.observability.metrics import agent_joinroom_drop_total
from doorae.ws.manager import ConnectionManager
from doorae.ws.protocol import JoinRoomOut, RoomMembershipChangedOut

logger = structlog.get_logger(__name__)


async def ensure_agent_in_room(
    db: AsyncSession,
    manager: ConnectionManager | None,
    *,
    room_id: str,
    agent_id: str,
    role: str = "member",
) -> tuple[Participant, bool]:
    """Ensure *agent_id* is a participant of *room_id*.

    Returns ``(participant, created)`` where ``created`` is True iff a
    new row was inserted. The helper is idempotent — calling it
    repeatedly with the same ``(room_id, agent_id)`` pair is safe and
    returns the existing row.

    Regardless of ``created``, the agent's *other* participant rows are
    looked up and a ``JoinRoomOut(room_id=room_id)`` is pushed to each
    via ``manager.send_to``. This is the key invariant: the agent SDK
    only auto-subscribes to a room when it receives this frame, and
    skipping it — even when the DB row already exists — leaves agents
    silently absent from broadcasts. A ``send_to`` for a pid the SDK
    has already subscribed is a cheap no-op on the SDK side.

    If *manager* is ``None`` (e.g. unit tests that don't wire a real
    connection manager) the notification step is skipped.
    """
    existing_stmt = select(Participant).where(
        Participant.room_id == room_id,
        Participant.agent_id == agent_id,
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()

    if existing is not None:
        participant = existing
        created = False
    else:
        participant = Participant(
            room_id=room_id,
            agent_id=agent_id,
            role=role,
        )
        db.add(participant)
        await db.commit()
        await db.refresh(participant)
        created = True

    if manager is not None:
        other_pids = (
            await db.execute(
                select(Participant.id).where(
                    Participant.agent_id == agent_id,
                    Participant.id != participant.id,
                )
            )
        ).scalars().all()
        if other_pids:
            # #227 — observe silent drops. ``send_to`` is best-effort
            # (it no-ops if the target pid is missing from
            # ``_by_participant``), so counting the frames we *know*
            # will drop is the only way to trip an alert when a whole
            # cohort of agents misses JoinRoomOut. The send itself
            # still happens below as a cheap no-op for the pids that
            # *are* subscribed, plus belt-and-braces for race windows
            # where a subscription arrives between our check and the
            # send. Callers that need delivery guarantees must pair
            # this helper with a machine-side nudge (``bump_generation``
            # or ``request_start``) — see ``AgentLifecycle.on_room_added``.
            try:
                connected_pids = await manager.connected_participant_ids()
            except AttributeError:
                # Legacy test doubles without the method — treat as
                # "nothing connected" conservatively, which is also
                # the only safe assumption if we can't introspect.
                connected_pids = set()
            frame = JoinRoomOut(room_id=room_id, participant_id="")
            for pid in other_pids:
                if pid not in connected_pids:
                    agent_joinroom_drop_total.labels(
                        reason="not_subscribed"
                    ).inc()
                    logger.warning(
                        "membership.joinroom_dropped",
                        agent_id=agent_id,
                        room_id=room_id,
                        dropped_pid=pid,
                        reason="not_subscribed",
                    )
                await manager.send_to(pid, frame)

    return participant, created


async def add_user_to_room(
    db: AsyncSession,
    manager: ConnectionManager | None,
    *,
    room_id: str,
    user_id: str,
    role: str = "member",
) -> Participant:
    """Insert a user Participant row and broadcast an ``added`` frame.

    Unlike :func:`ensure_agent_in_room`, this helper is *not*
    idempotent — duplicate-check is the caller's responsibility (the
    REST API returns 409 on a repeat add and the frontend contract
    depends on that). The helper only handles the common "insert +
    notify the user's other WS sessions" path.

    If *manager* is ``None`` the notification step is skipped.
    """
    participant = Participant(
        room_id=room_id,
        user_id=user_id,
        role=role,
    )
    db.add(participant)
    await db.commit()
    await db.refresh(participant)

    if manager is not None:
        other_pids = (
            await db.execute(
                select(Participant.id).where(
                    Participant.user_id == user_id,
                    Participant.id != participant.id,
                )
            )
        ).scalars().all()
        if other_pids:
            frame = RoomMembershipChangedOut(
                action="added",
                room_id=room_id,
                user_id=user_id,
            )
            for pid in other_pids:
                await manager.send_to(pid, frame)

    return participant
