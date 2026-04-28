"""REST endpoint for batch auto-routing of unassigned tasks (#313).

Single endpoint: ``POST /api/v1/rooms/{room_id}/auto-route-unassigned``.

Flow:
1. Caller authenticates and the room must exist.
2. Collect every Task in the room with ``assignee_participant_id IS
   NULL`` and a non-terminal status.
3. Resolve the rep agent (#312 invariant guarantees one exists when
   any agents are in the room) and confirm it's actually running.
   ``404`` if the rep slot is unset (empty room), ``422`` if it's
   not running.
4. Compose the routing prompt via ``format_routing_prompt`` and
   inject it into the room as a synthetic mention message addressed
   to the rep. Same plumbing as task-assignment messages (#266) so
   the rep wakes up through ``decide_policy``.
5. Register an ``asyncio.Future`` keyed by the request id in
   ``app.state.routing_futures``. The WS message hook
   (``ws/handler.py``) resolves the Future when the rep emits a
   message bearing the response marker.
6. ``await fut`` with a 30s timeout. On timeout, return 504 with
   the request id so the caller can present a useful error.
7. For each ``{task_id: agent_id}`` mapping in the parsed result,
   look up the agent's Participant in the room and call
   ``inject_task_assignment_message`` (the same path the
   ``PUT /tasks/{id}`` reassign flow uses) so each newly-routed
   task wakes its assignee.

Out of scope (TODO):
- LiteLLM gateway fallback when the rep is offline / unresponsive.
  The protocol module's ``parse_routing_response`` is reusable, so
  a future PR can implement a server-side router using the same
  Future registry.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.db.models import Agent, Participant, Room, Task
from doorae.dependencies import get_current_identity, get_db
from doorae.messages.service import (
    fanout_task_event,
    inject_task_assignment_message,
)
from doorae.routing.protocol import (
    RoutingResult,
    _AgentLine,
    _TaskLine,
    format_routing_prompt,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["routing"])

ROUTING_REQUEST_TIMEOUT = 30.0


class RoutedTask(BaseModel):
    task_id: str
    assignee_agent_id: str


class SkippedTask(BaseModel):
    task_id: str
    reason: str


class AutoRouteResult(BaseModel):
    routed: list[RoutedTask]
    skipped: list[SkippedTask]
    rep_agent_id: str
    request_id: str


@router.post(
    "/api/v1/rooms/{room_id}/auto-route-unassigned",
    response_model=AutoRouteResult,
)
async def auto_route_unassigned(
    room_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> AutoRouteResult:
    """Ask the room's representative agent to assign every
    unassigned task to a fitting agent based on descriptions."""
    room = (
        await db.execute(select(Room).where(Room.id == room_id))
    ).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    if room.representative_agent_id is None:
        # #312's invariant guarantees this is the empty-room case
        # — if anyone is in the room, the rep slot is filled.
        raise HTTPException(
            status_code=422,
            detail=(
                "Room has no representative agent — add an agent "
                "to the room first."
            ),
        )

    # Confirm rep is actually running. Routing requires the rep to
    # process the synthetic mention; an offline rep would just
    # leave the request dangling until timeout.
    rep_agent = (
        await db.execute(
            select(Agent).where(Agent.id == room.representative_agent_id)
        )
    ).scalar_one_or_none()
    if rep_agent is None:
        raise HTTPException(
            status_code=422,
            detail="Representative agent record not found",
        )
    if rep_agent.actual_state != "running":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Representative agent {rep_agent.name} is not "
                f"running (state={rep_agent.actual_state}). Start "
                "it before requesting auto-route."
            ),
        )

    # Collect candidate agents (room agent participants) + their
    # descriptions so the rep has the same roster the user sees.
    agent_rows = (
        await db.execute(
            select(Agent, Participant.id)
            .join(Participant, Participant.agent_id == Agent.id)
            .where(Participant.room_id == room_id)
        )
    ).all()
    if not agent_rows:
        raise HTTPException(
            status_code=422,
            detail="Room has no agent participants",
        )
    agents = [
        _AgentLine(agent_id=a.id, name=a.name, description=a.description)
        for a, _ in agent_rows
    ]
    agent_id_to_pid = {a.id: pid for a, pid in agent_rows}

    # Unassigned tasks. ``done`` is excluded — auto-routing
    # completed work doesn't make sense; ``blocked`` stays so a
    # human-blocked task can still be re-routed if appropriate.
    task_rows = (
        await db.execute(
            select(Task).where(
                Task.room_id == room_id,
                Task.assignee_participant_id.is_(None),
                Task.status.in_(["todo", "in_progress", "blocked"]),
            )
        )
    ).scalars().all()
    if not task_rows:
        # Empty bucket isn't an error — the UI's button is supposed
        # to be disabled in this case anyway. Return a clean empty
        # result so the client can render "Nothing to route" toast.
        return AutoRouteResult(
            routed=[],
            skipped=[],
            rep_agent_id=rep_agent.id,
            request_id="",
        )
    tasks = [_TaskLine(task_id=t.id, title=t.title) for t in task_rows]
    task_by_id = {t.id: t for t in task_rows}

    # Find rep's Participant.id — needed for the synthetic mention.
    rep_pid = agent_id_to_pid.get(rep_agent.id)
    if rep_pid is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Representative agent is not currently a "
                "participant of the room"
            ),
        )

    # Register the Future BEFORE injecting the prompt — otherwise a
    # race with a very fast rep response could leave us looking up
    # an empty registry slot.
    request_id = str(uuid4())
    futures: dict[str, asyncio.Future[RoutingResult]] = getattr(
        request.app.state, "routing_futures", None
    )  # type: ignore[assignment]
    if futures is None:
        futures = {}
        request.app.state.routing_futures = futures
    fut: asyncio.Future[RoutingResult] = asyncio.get_event_loop().create_future()
    futures[request_id] = fut

    # Build + inject the prompt as a system-origin message addressed
    # to the rep. Same shape as ``inject_task_assignment_message``
    # (#266) so the rep's ``decide_policy`` mention path picks it up.
    prompt_body = format_routing_prompt(
        request_id=request_id,
        room_name=room.name,
        agents=agents,
        tasks=tasks,
    )
    content = f"<@user:{rep_pid}> {prompt_body}"
    metadata = {
        "mentions": [{"type": "user", "id": rep_pid}],
        "system_origin": "auto_route_request",
        "routing_request_id": request_id,
    }
    from doorae.messages.service import append_message

    inject_msg = await append_message(
        db,
        room_id=room_id,
        participant_id=None,
        content=content,
        metadata=metadata,
    )
    await db.commit()

    # Broadcast the mention so the rep's WS subscription wakes up.
    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None:
        from doorae.ws.protocol import MessageOut

        out = MessageOut(
            id=inject_msg.id,
            room_id=inject_msg.room_id,
            participant_id=inject_msg.participant_id,
            content=inject_msg.content,
            seq=inject_msg.seq,
            created_at=inject_msg.created_at,
            metadata=inject_msg.extra_metadata,
        )
        await manager.broadcast(room_id, out)

    log.info(
        "auto_route_request",
        extra={
            "room_id": room_id,
            "request_id": request_id,
            "rep": rep_agent.id,
            "task_count": len(tasks),
            "agent_count": len(agents),
        },
    )

    # Wait for the rep's response. The WS handler resolves the
    # Future when it sees a message containing the response marker
    # with this request_id.
    try:
        result: RoutingResult = await asyncio.wait_for(
            fut, timeout=ROUTING_REQUEST_TIMEOUT
        )
    except asyncio.TimeoutError:
        futures.pop(request_id, None)
        raise HTTPException(
            status_code=504,
            detail=(
                f"Representative agent did not respond within "
                f"{ROUTING_REQUEST_TIMEOUT:.0f}s"
            ),
        ) from None

    if not result.ok or result.mapping is None:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Routing response could not be parsed: "
                f"{result.error or 'unknown error'}"
            ),
        )

    # Apply the assignments. Each successful mapping triggers
    # ``inject_task_assignment_message`` (the same path used by
    # ``PUT /tasks/{id}`` reassignment) so the assignee wakes up
    # through the standard #266 mention flow.
    routed: list[RoutedTask] = []
    skipped: list[SkippedTask] = []
    for task_id, mapped_agent_id in result.mapping.items():
        task = task_by_id.get(task_id)
        if task is None:
            skipped.append(
                SkippedTask(
                    task_id=task_id,
                    reason="task no longer unassigned in room",
                )
            )
            continue
        target_pid = agent_id_to_pid.get(mapped_agent_id)
        if target_pid is None:
            skipped.append(
                SkippedTask(
                    task_id=task_id,
                    reason=(
                        f"agent {mapped_agent_id} is not a "
                        "participant of the room"
                    ),
                )
            )
            continue
        task.assignee_participant_id = target_pid
        await db.flush()
        target_p = (
            await db.execute(
                select(Participant).where(Participant.id == target_pid)
            )
        ).scalar_one()
        await inject_task_assignment_message(
            db,
            room=room,
            task=task,
            sender_participant_id=None,
            event="assigned",
        )
        routed.append(
            RoutedTask(
                task_id=task_id,
                assignee_agent_id=target_p.agent_id or mapped_agent_id,
            )
        )

    # Tasks the rep didn't assign at all (LLM omission) are also
    # surfaced as skipped — the UI can show "1/3 routed" without
    # users having to compare lists themselves.
    for orphan_id in set(t.id for t in task_rows) - set(result.mapping.keys()):
        skipped.append(
            SkippedTask(
                task_id=orphan_id,
                reason="rep response did not include this task",
            )
        )

    await db.commit()

    if manager is not None:
        # Fan out task updates so subscribers refresh their lists.
        for r in routed:
            t = task_by_id.get(r.task_id)
            if t is not None:
                await fanout_task_event(
                    db,
                    manager=manager,
                    event="reassigned",
                    task=t,
                    room_name=room.name,
                )

    return AutoRouteResult(
        routed=routed,
        skipped=skipped,
        rep_agent_id=rep_agent.id,
        request_id=request_id,
    )
