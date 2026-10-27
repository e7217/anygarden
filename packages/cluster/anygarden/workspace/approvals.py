"""Hold an ``ask`` action for a person, then run it once (#938).

The agent's request becomes a :class:`WorkspaceActionRequest` and a card
in the room (the agent's message with ``metadata.workspace_action_request``).
The person who asked, or an admin (only an admin for ``admin_only``
actions), approves or rejects it. Approval claims the row
(``pending → executing``) before anything runs, so a second click or a
second tab cannot run it twice; the action then runs with the
*approver's* rights. Either way a server notice reports the outcome
to the agent and wakes it; that turn acts for the person who asked, never
for the approver.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Message
from anygarden.db.workspace_action_models import WorkspaceActionRequest
from anygarden.workspace import policy as p
from anygarden.workspace.actions import ActionFailed, run_action
from anygarden.workspace.requester import Requester

log = logging.getLogger(__name__)

EXECUTING_STALE = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def describe(action: str, params: dict) -> str:
    """One line a person reads on the card."""

    spec = p.ACTIONS[action]
    shown = {k: v for k, v in params.items() if v not in (None, [], {}, "")}
    text = json.dumps(shown, ensure_ascii=False, default=str)
    if len(text) > 600:
        text = text[:600] + "…"
    return f"{spec.summary} {text}"


async def open_request(
    db: AsyncSession, *, requester: Requester, agent_id: str, action: str, params: dict,
) -> tuple[WorkspaceActionRequest, Message | None]:
    """Store the request and post its card (``None``: already open). The caller commits."""

    from anygarden.messages.service import append_message

    # The same proposal again in this turn (a retried call) is the same card.
    for existing in await db.scalars(select(WorkspaceActionRequest).where(
        WorkspaceActionRequest.turn_request_id == requester.turn_request_id,
        WorkspaceActionRequest.action == action,
        WorkspaceActionRequest.status == "pending",
    )):
        if existing.params == params:
            return existing, None
    summary = describe(action, params)
    row = WorkspaceActionRequest(
        agent_id=agent_id,
        agent_participant_id=requester.agent_participant_id,
        room_id=requester.room_id,
        thread_root_id=requester.thread_root_id,
        requested_by_user_id=requester.user_id,
        turn_request_id=requester.turn_request_id,
        action=action,
        params=params,
        summary=summary,
        status="pending",
        expires_at=_now() + timedelta(seconds=p.APPROVAL_TTL_SECONDS),
    )
    db.add(row)
    await db.flush()
    message = await append_message(
        db, requester.room_id, requester.agent_participant_id,
        f"Approval needed: {summary}",
        {"workspace_action_request": {"request_id": row.id, "action": action}},
        thread_root_id=requester.thread_root_id,
    )
    row.request_message_id = message.id
    await db.flush()
    return row, message


class DecisionRefused(ValueError):
    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def may_decide(row: WorkspaceActionRequest, identity: Identity) -> bool:
    is_admin = bool(identity.claims and getattr(identity.claims, "is_admin", False))
    if p.ACTIONS[row.action].admin_only:
        return is_admin
    return is_admin or identity.id == row.requested_by_user_id


async def may_approve(db: AsyncSession, row: WorkspaceActionRequest, identity: Identity) -> bool:
    """:func:`may_decide`, and for a goal the right to set its evaluator.

    The evaluator is a shell command on the lead agent's machine, so only
    that machine's owner or an admin may start it (#930). Checked before
    the claim so an approver without the right never uses up the request.
    """

    if not may_decide(row, identity):
        return False
    if row.action != "create_goal":
        return True
    from anygarden.api.v1.campaigns import _can_set_evaluator
    from anygarden.db.models import Room

    room = await db.get(Room, row.room_id)
    return room is not None and await _can_set_evaluator(db, identity, room.representative_agent_id)


EDITABLE_ACTIONS = frozenset({"create_goal", "create_routine"})


def edited_params(row: WorkspaceActionRequest, params: dict) -> dict:
    """The values a person changed in the form before approving (#947, #948).

    Only a goal or a routine can be edited, and a goal stays in its room.
    Raises :class:`DecisionRefused`.
    """

    from pydantic import ValidationError

    from anygarden.workspace.actions import _validation_text

    if row.action not in EDITABLE_ACTIONS:
        raise DecisionRefused(422, "NOT_EDITABLE", "This request cannot be edited; approve or reject it")
    if row.action == "create_goal":
        params = {**params, "room_id": row.room_id}
    try:
        parsed = p.ACTIONS[row.action].params.model_validate(params)
        if row.action == "create_goal":
            from anygarden.api.v1.campaigns import CampaignCreate

            parsed.spec = CampaignCreate.model_validate(parsed.spec).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise DecisionRefused(422, "INVALID_PARAMS", _validation_text(exc)) from None
    return parsed.model_dump(mode="json")


async def expire_if_due(db: AsyncSession, row: WorkspaceActionRequest) -> bool:
    if row.status == "executing" and row.decided_at and _aware(row.decided_at) <= _now() - EXECUTING_STALE:
        # The process died between the claim and the outcome: whether it ran is unknown.
        await db.execute(
            update(WorkspaceActionRequest)
            .where(WorkspaceActionRequest.id == row.id, WorkspaceActionRequest.status == "executing")
            .values(status="failed", error="The outcome is unknown; check before asking again")
        )
        await db.commit()
        await db.refresh(row)
        return False
    if row.status == "pending" and _aware(row.expires_at) <= _now():
        await db.execute(
            update(WorkspaceActionRequest)
            .where(WorkspaceActionRequest.id == row.id, WorkspaceActionRequest.status == "pending")
            .values(status="expired")
        )
        await db.commit()
        await db.refresh(row)
        return True
    return False


async def _claim(
    db: AsyncSession, row: WorkspaceActionRequest, identity: Identity, status: str, *, params: dict | None = None,
) -> None:
    values: dict = {"status": status, "decided_by_user_id": identity.id, "decided_at": _now()}
    if params is not None:
        # What runs is what the card shows from now on.
        values.update(params=params, summary=describe(row.action, params))
    result = await db.execute(
        update(WorkspaceActionRequest)
        .where(WorkspaceActionRequest.id == row.id, WorkspaceActionRequest.status == "pending")
        .values(**values)
    )
    if result.rowcount != 1:
        await db.rollback()
        raise DecisionRefused(409, "ALREADY_DECIDED", "This request was already decided")
    await db.commit()
    await db.refresh(row)


async def load_for_decision(
    db: AsyncSession, request_id: str, identity: Identity, *, approving: bool = False,
) -> WorkspaceActionRequest:
    row = await db.get(WorkspaceActionRequest, request_id)
    if row is None:
        raise DecisionRefused(404, "NOT_FOUND", "Request not found")
    if not may_decide(row, identity):
        raise DecisionRefused(403, "FORBIDDEN", "Only the person who asked, or an admin, can decide this")
    if approving and not await may_approve(db, row, identity):
        raise DecisionRefused(
            403, "EVALUATOR_FORBIDDEN",
            "Only the owner of the lead agent's machine, or an admin, can start a goal with this evaluator",
        )
    if await expire_if_due(db, row):
        raise DecisionRefused(409, "EXPIRED", "This request expired; ask the agent again")
    if row.status != "pending":
        raise DecisionRefused(409, "ALREADY_DECIDED", "This request was already decided")
    return row


async def approve(
    session_factory, request: Request, *, request_id: str, identity: Identity, params: dict | None = None,
) -> WorkspaceActionRequest:
    """Run the request once. *params* replaces a goal's or routine's values
    with the ones the person edited in the form."""

    async with session_factory() as db:
        row = await load_for_decision(db, request_id, identity, approving=True)
        edited = edited_params(row, params) if params is not None else None
        await _claim(db, row, identity, "executing", params=edited)
        action, params, agent_id = row.action, dict(row.params), row.agent_id
    from anygarden.rooms.authorization import room_authorization_session

    async with room_authorization_session(session_factory) as db:
        try:
            parsed = p.ACTIONS[action].params.model_validate(params)
            result = await run_action(db, request, identity, action, parsed, agent_id=agent_id)
            outcome = {"status": "executed", "result": result, "error": None}
        except ActionFailed as exc:
            outcome = {"status": "failed", "result": None, "error": exc.detail}
        except Exception:
            # Never leave the row ``executing``: report the failure instead.
            log.exception("workspace_action_failed", extra={"request_id": request_id})
            outcome = {"status": "failed", "result": None, "error": "The action failed unexpectedly"}
    # The outcome is durable before anything else can fail.
    async with session_factory() as db:
        await db.execute(
            update(WorkspaceActionRequest).where(WorkspaceActionRequest.id == request_id).values(**outcome)
        )
        await db.commit()
    return await _report_and_load(session_factory, request, request_id)


async def reject(
    session_factory, request: Request, *, request_id: str, identity: Identity,
) -> WorkspaceActionRequest:
    async with session_factory() as db:
        row = await load_for_decision(db, request_id, identity)
        await _claim(db, row, identity, "rejected")
    return await _report_and_load(session_factory, request, request_id)


async def _report_and_load(session_factory, request: Request, request_id: str) -> WorkspaceActionRequest:
    """Wake the agent with the outcome. A failed report never undoes the decision."""

    async with session_factory() as db:
        row = await db.get(WorkspaceActionRequest, request_id)
        try:
            message = await _report(db, row)
            await db.commit()
            await _publish(db, request, message)
        except Exception:
            await db.rollback()
            log.exception("workspace_action_report_failed", extra={"request_id": request_id})
        row = await db.get(WorkspaceActionRequest, request_id, populate_existing=True)
        return row


async def _report(db: AsyncSession, row: WorkspaceActionRequest) -> Message | None:
    """Tell the agent how its request ended and start its turn.

    A server notice, never a message in the approver's name: the woken
    turn acts for the person who *asked* (see ``requester_for_turn``), so
    an admin's approval never lends the agent admin rights.
    """

    from anygarden.db.models import Room
    from anygarden.messages.service import append_message
    from anygarden.turns.service import create_turn

    room = await db.get(Room, row.room_id)
    if room is None or room.archived_at is not None or row.agent_participant_id is None:
        return None
    agent_pid = row.agent_participant_id
    verdict = {
        "executed": "approved and done",
        "failed": "approved, but it failed",
        "rejected": "rejected",
    }[row.status]
    lines = [f"<@user:{agent_pid}> Workspace request {verdict}: {row.summary}"]
    if row.result:
        lines.append(f"Result: {json.dumps(row.result, ensure_ascii=False, default=str)}")
    if row.error:
        lines.append(f"Error: {row.error}")
    lines.append("Tell the person what happened. Do not repeat the request unless they ask again.")
    metadata: dict = {
        "mentions": [{"type": "user", "id": agent_pid}],
        "system_origin": "workspace_action_result",
        "workspace_action_result": {"request_id": row.id, "status": row.status},
    }
    message = await append_message(
        db, row.room_id, None, "\n".join(lines), metadata, thread_root_id=row.thread_root_id,
    )
    await create_turn(
        db, room_id=row.room_id, participant_id=agent_pid, agent_id=row.agent_id,
        trigger_message_id=message.id, thread_root_id=message.root_message_id,
    )
    return message


async def _publish(db: AsyncSession, request: Request, message: Message | None) -> None:
    from anygarden.mcp.project_tools import broadcast_project_messages

    if message is not None:
        await broadcast_project_messages(db, request=request, messages=[message])


def view(row: WorkspaceActionRequest) -> dict:
    return {
        "id": row.id,
        "room_id": row.room_id,
        "agent_id": row.agent_id,
        "action": row.action,
        "params": row.params,
        "summary": row.summary,
        "status": row.status,
        "result": row.result,
        "error": row.error,
        "admin_only": p.ACTIONS[row.action].admin_only,
        "requested_by_user_id": row.requested_by_user_id,
        "decided_by_user_id": row.decided_by_user_id,
        "created_at": _aware(row.created_at).isoformat() if row.created_at else None,
        "expires_at": _aware(row.expires_at).isoformat(),
        "decided_at": _aware(row.decided_at).isoformat() if row.decided_at else None,
    }
