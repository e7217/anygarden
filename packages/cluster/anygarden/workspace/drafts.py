"""An agent drafts a goal or routine; a person creates it with one click (#947, #948).

``/goal <description>`` and ``/routine [@agent] <description>`` open a
:class:`DraftRequest`. The server posts a notice that mentions the drafter
and starts its turn; the notice carries the REQUIRED ACTIONS. In that turn
— and only there (turn proof) — the agent submits the draft with
``propose_goal`` / ``propose_routine``. The server checks it as the create
routes would; a draft that fails goes back to the agent as an error, never
to a card. A draft that passes becomes an ordinary ``ask``
:class:`WorkspaceActionRequest`, so approval, expiry, "runs once with the
approver's rights" and the result report are #938's.

The turn acts for the person who asked (``requester_for_turn`` reads the
notice's draft). A turn that ends without a draft is reported to that
person by :func:`close_unanswered_drafts`.

| request                          | drafter                 | routine assignee                 |
|----------------------------------|-------------------------|----------------------------------|
| ``/goal`` text                   | the room's lead         | —                                |
| ``/routine @agent`` text         | that agent              | that agent                       |
| ``/routine`` text, one agent     | that agent              | that agent                       |
| ``/routine`` text, lead in room  | the lead                | the lead's pick (editable)       |
| ``/routine`` text, otherwise     | refused (name an agent) |                                  |
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, AgentTurn, Goal, Message, Participant, Room, User
from anygarden.db.workspace_action_models import DraftRequest, WorkspaceActionRequest
from anygarden.workspace.actions import ActionFailed, _validation_text
from anygarden.workspace.requester import Requester

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 4000
SYSTEM_ORIGIN = "draft_request"
FAILED_ORIGIN = "draft_request_failed"


class DraftRefused(ValueError):
    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC)


# -- who drafts -----------------------------------------------------------------


@dataclass(frozen=True)
class Drafter:
    agent: Agent
    participant: Participant
    assignee_agent_id: str | None


async def _room_agents(db: AsyncSession, room_id: str) -> list[tuple[Participant, Agent]]:
    rows = await db.execute(
        select(Participant, Agent)
        .join(Agent, Agent.id == Participant.agent_id)
        .where(Participant.room_id == room_id)
        .order_by(Participant.joined_at)
    )
    return [(p, a) for p, a in rows.all()]


async def resolve_drafter(db: AsyncSession, room: Room, kind: str, agent_id: str | None) -> Drafter:
    """Who writes the draft and, for a routine, whose routine it is."""

    agents = await _room_agents(db, room.id)
    by_agent = {a.id: (p, a) for p, a in agents}
    if kind == "goal":
        from anygarden.campaigns.policy import CampaignPolicyError
        from anygarden.campaigns.service import check_campaign_room

        try:
            lead_id = await check_campaign_room(db, room)
        except CampaignPolicyError as exc:
            raise DraftRefused(409, exc.code, exc.message) from None
        participant, agent = by_agent[lead_id]
        return Drafter(agent, participant, None)
    if agent_id is not None:
        if agent_id not in by_agent:
            raise DraftRefused(422, "AGENT_NOT_IN_ROOM", "The named agent is not in this room")
        participant, agent = by_agent[agent_id]
        return Drafter(agent, participant, agent.id)
    if not agents:
        raise DraftRefused(409, "NO_AGENTS", "This room has no agents")
    if len(agents) == 1:
        participant, agent = agents[0]
        return Drafter(agent, participant, agent.id)
    lead = by_agent.get(room.representative_agent_id or "")
    if lead is None:
        raise DraftRefused(409, "AGENT_REQUIRED", "Name an agent with @ to draft the routine")
    return Drafter(lead[1], lead[0], None)


# -- the request ------------------------------------------------------------------


def _goal_brief(pid: str, description: str) -> str:
    return "\n".join([
        f"<@user:{pid}> Goal draft request: {description}",
        "",
        ("Draft a goal (a metric goal for this project room, run by you as the room's lead) from "
            "this request, the conversation and your workspace. REQUIRED ACTIONS:"),
        ("1. Choose one measurable metric, its direction, and a target (mode=target) or a deadline "
            "(mode=optimize). Leave out the token budget unless the person asked for a token limit."),
        ("2. evaluator.command is a shell command run in your workspace on your machine; the last "
            "line of its stdout must be one JSON object with the score under evaluator.score_key and "
            'a boolean "valid", e.g. {"score": 22, "valid": true} ("valid": false when the '
            "measurement itself could not be trusted). It is never prose. If no script measures the "
            "metric yet, write one in your workspace first, run it once and point the command at it. "
            "The script must count exactly what the metric name says. When part of it needs "
            "judgement (for example ideas that repeat each other in meaning, not just in title), "
            "record that judgement in a file field during the work so that the script reads that "
            "field; otherwise name the metric after what the script really counts."),
        ("3. Call propose_goal once with the whole draft. If it returns an error, fix the draft and "
            "call it again. Do not create the goal any other way (no workspace_action, no engine goal "
            "tools)."),
        ("4. Tell the person in one or two sentences that the draft card is ready for them to "
            "create, edit or reject, and end your turn."),
    ])


def _routine_brief(
    pid: str, description: str, *, room_id: str, timezone: str | None,
    assignee: Agent | None, agents: list[Agent],
) -> str:
    if assignee is not None:
        who = f"The routine is yours (agent_id {assignee.id}); write its instructions for yourself."
    else:
        roster = "; ".join(f"{a.name} (agent_id {a.id})" for a in agents)
        who = (f"Pick the agent best suited to run it from this room's agents: {roster}. "
               "Write the instructions for that agent.")
    tz = timezone or "UTC"
    return "\n".join([
        f"<@user:{pid}> Routine draft request: {description}",
        "",
        ("Draft a routine (recurring work an agent runs on a schedule) from this request and the "
            "conversation. REQUIRED ACTIONS:"),
        f"1. {who}",
        ("2. Choose the schedule: trigger_type cron with trigger_config {cron: '<5-field cron>'}, "
            "interval with {interval_seconds: N}, or manual. "
            f"The person's timezone is {tz}; cron times are read in trigger_config.timezone, which "
            f"defaults to {tz} for this draft."),
        f"3. Reports go to this room (report_room_id {room_id}) unless the person asked otherwise.",
        ("4. Call propose_routine once with the whole draft. If it returns an error, fix the draft "
            "and call it again. Do not create the routine any other way."),
        ("5. Tell the person in one or two sentences that the draft card is ready for them to "
            "create, edit or reject, and end your turn."),
    ])


async def open_draft(
    db: AsyncSession, *, room: Room, user: User, kind: str, description: str,
    agent_id: str | None = None, timezone: str | None = None,
) -> tuple[DraftRequest, Message]:
    """Record the request, post its notice and start the drafter's turn. The caller commits."""

    from anygarden.messages.service import append_message
    from anygarden.turns.service import create_turn

    description = description.strip()
    if not description or len(description) > MAX_DESCRIPTION_CHARS:
        raise DraftRefused(422, "INVALID_DESCRIPTION", f"Describe it in 1–{MAX_DESCRIPTION_CHARS} characters")
    if timezone is not None:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            timezone = None
    if room.archived_at is not None:
        raise DraftRefused(409, "ROOM_ARCHIVED", "The room is archived")
    asker = await db.scalar(select(Participant).where(
        Participant.room_id == room.id, Participant.user_id == user.id,
    ))
    if asker is None:
        raise DraftRefused(403, "NOT_A_MEMBER", "Only a member of the room can ask for a draft")
    drafter = await resolve_drafter(db, room, kind, agent_id)
    draft = DraftRequest(
        kind=kind, room_id=room.id, requested_by_user_id=user.id,
        drafter_agent_id=drafter.agent.id, drafter_participant_id=drafter.participant.id,
        assignee_agent_id=drafter.assignee_agent_id, description=description,
        timezone=timezone, status="pending",
    )
    db.add(draft)
    await db.flush()
    pid = drafter.participant.id
    if kind == "goal":
        content = _goal_brief(pid, description)
    else:
        assignee = drafter.agent if drafter.assignee_agent_id else None
        content = _routine_brief(
            pid, description, room_id=room.id, timezone=timezone, assignee=assignee,
            agents=[a for _, a in await _room_agents(db, room.id)],
        )
    message = await append_message(db, room.id, None, content, {
        "mentions": [{"type": "user", "id": pid}],
        "system_origin": SYSTEM_ORIGIN,
        SYSTEM_ORIGIN: {
            "id": draft.id, "kind": kind, "description": description,
            "drafter_participant_id": pid, "requested_by_participant_id": asker.id,
        },
    })
    turn = await create_turn(
        db, room_id=room.id, participant_id=pid, agent_id=drafter.agent.id,
        trigger_message_id=message.id, thread_root_id=message.root_message_id,
    )
    draft.request_message_id = message.id
    draft.turn_request_id = turn.request_id
    await db.flush()
    return draft, message


def draft_id_of(message: Message) -> str | None:
    metadata = message.extra_metadata or {}
    meta = metadata.get(SYSTEM_ORIGIN)
    if metadata.get("system_origin") != SYSTEM_ORIGIN or not isinstance(meta, dict):
        return None
    value = meta.get("id")
    return value if isinstance(value, str) else None


async def asker_of_draft_notice(db: AsyncSession, source: Message) -> Participant | None:
    """The person whose ``/goal`` or ``/routine`` started this turn."""

    draft_id = draft_id_of(source)
    draft = await db.get(DraftRequest, draft_id) if draft_id else None
    if draft is None or draft.room_id != source.room_id or draft.request_message_id != source.id:
        return None
    if draft.requested_by_user_id is None:
        return None
    return await db.scalar(select(Participant).where(
        Participant.room_id == source.room_id, Participant.user_id == draft.requested_by_user_id,
    ))


# -- the draft --------------------------------------------------------------------


async def _draft_for_turn(db: AsyncSession, requester: Requester, kind: str) -> DraftRequest:
    draft = await db.scalar(select(DraftRequest).where(DraftRequest.turn_request_id == requester.turn_request_id))
    if draft is None or draft.kind != kind:
        command = "/goal" if kind == "goal" else "/routine"
        raise ActionFailed(
            "NOT_A_DRAFT_TURN",
            f"propose_{kind} works only in the turn started by a person's {command} draft request",
        )
    if draft.status != "pending":
        raise ActionFailed(
            "DRAFT_ALREADY_PROPOSED",
            "A draft was already submitted for this request; point the person to its card",
        )
    return draft


async def _goal_params(db: AsyncSession, draft: DraftRequest, arguments: dict) -> dict:
    from anygarden.api.v1.campaigns import CampaignCreate
    from anygarden.campaigns.policy import CampaignPolicyError, validate_campaign_spec
    from anygarden.campaigns.service import check_campaign_room

    try:
        body = CampaignCreate.model_validate(arguments)
    except ValidationError as exc:
        raise ActionFailed("INVALID_DRAFT", _validation_text(exc)) from None
    room = await db.get(Room, draft.room_id)
    try:
        await check_campaign_room(db, room)
        validate_campaign_spec(
            title=body.title, purpose=body.purpose, mode=body.mode, metric=body.metric.model_dump(),
            evaluator=body.evaluator.model_dump(), budget=body.budget.model_dump(),
            deadline_at=body.deadline_at, trusted_eval=body.trusted_eval, now=_now(),
        )
    except CampaignPolicyError as exc:
        raise ActionFailed(exc.code, exc.message) from None
    return {"room_id": draft.room_id, "spec": body.model_dump(mode="json", exclude_none=True)}


async def _routine_params(db: AsyncSession, draft: DraftRequest, requester: Requester, arguments: dict) -> dict:
    from anygarden.api.v1.goals import MAX_ACTIVE_GOALS_PER_OWNER
    from anygarden.goals.policy import InvalidTriggerConfig, validate_trigger_config
    from anygarden.workspace.policy import CreateRoutineParams

    raw = dict(arguments)
    raw.setdefault("report_room_id", draft.room_id)
    if draft.assignee_agent_id is not None:
        if raw.get("agent_id") not in (None, draft.assignee_agent_id):
            raise ActionFailed("ASSIGNEE_FIXED", "The person asked for this agent's routine; keep agent_id as it is")
        raw["agent_id"] = draft.assignee_agent_id
    config = raw.get("trigger_config")
    if raw.get("trigger_type") == "cron" and isinstance(config, dict) and draft.timezone and "timezone" not in config:
        # "Every day at 9" means the person's 9, not UTC's.
        raw["trigger_config"] = {**config, "timezone": draft.timezone}
    try:
        params = CreateRoutineParams.model_validate(raw)
        validate_trigger_config(params.trigger_type, params.trigger_config)
    except ValidationError as exc:
        raise ActionFailed("INVALID_DRAFT", _validation_text(exc)) from None
    except InvalidTriggerConfig as exc:
        raise ActionFailed("INVALID_DRAFT", f"trigger_config: {exc}") from None
    member = await db.scalar(select(Participant.id).where(
        Participant.room_id == params.report_room_id, Participant.agent_id == params.agent_id,
    ))
    if member is None:
        raise ActionFailed("AGENT_NOT_IN_REPORT_ROOM", "The assignee must be a member of report_room_id")
    active = await db.scalar(select(func.count()).select_from(Goal).where(
        Goal.owner_id == requester.user_id, Goal.status == "active",
    ))
    if active >= MAX_ACTIVE_GOALS_PER_OWNER:
        raise ActionFailed(
            "ROUTINE_LIMIT", f"The person already has {MAX_ACTIVE_GOALS_PER_OWNER} active routines",
        )
    return params.model_dump(mode="json")


async def propose(
    db: AsyncSession, *, requester: Requester, agent_id: str, kind: str, arguments: dict,
) -> tuple[WorkspaceActionRequest, Message | None]:
    """Check the draft and open its approval card. Raises :class:`ActionFailed`. The caller commits."""

    from anygarden.workspace import approvals

    draft = await _draft_for_turn(db, requester, kind)
    if kind == "goal":
        action, params = "create_goal", await _goal_params(db, draft, arguments)
    else:
        action, params = "create_routine", await _routine_params(db, draft, requester, arguments)
    row, message = await approvals.open_request(
        db, requester=requester, agent_id=agent_id, action=action, params=params,
    )
    draft.status = "proposed"
    draft.action_request_id = row.id
    draft.closed_at = _now()
    await db.flush()
    return row, message


# -- a turn that ended without a draft ------------------------------------------------


TERMINAL = ("completed", "cancelled", "failed")


async def _fail(db: AsyncSession, draft: DraftRequest) -> Message:
    from anygarden.messages.service import append_message

    draft.status = "failed"
    draft.closed_at = _now()
    what = "goal" if draft.kind == "goal" else "routine"
    # No mention: this tells the person, it does not wake the agent.
    return await append_message(db, draft.room_id, None, f"No {what} draft was made for: {draft.description}", {
        "system_origin": FAILED_ORIGIN,
        FAILED_ORIGIN: {
            "id": draft.id, "kind": draft.kind, "description": draft.description,
            "drafter_participant_id": draft.drafter_participant_id,
            "assignee_agent_id": draft.assignee_agent_id,
        },
    })


async def close_unanswered_drafts(session_factory, *, app=None) -> int:
    """Report every pending draft whose turn has ended. Returns how many."""

    from anygarden.mcp.project_tools import broadcast_project_messages

    async with session_factory() as db:
        rows = await db.execute(
            select(DraftRequest)
            .outerjoin(AgentTurn, AgentTurn.request_id == DraftRequest.turn_request_id)
            .where(
                DraftRequest.status == "pending",
                (AgentTurn.request_id.is_(None)) | (AgentTurn.state.in_(TERMINAL)),
            )
        )
        drafts = list(rows.scalars())
        if not drafts:
            return 0
        messages = []
        for draft in drafts:
            room = await db.get(Room, draft.room_id)
            if room is None or room.archived_at is not None:
                draft.status, draft.closed_at = "failed", _now()
                continue
            messages.append(await _fail(db, draft))
        await db.commit()
        if messages:
            try:
                await broadcast_project_messages(db, app=app, messages=messages)
            except Exception:
                log.exception("draft_failure_broadcast_failed")
        return len(drafts)


def view(draft: DraftRequest) -> dict[str, Any]:
    return {
        "id": draft.id,
        "kind": draft.kind,
        "room_id": draft.room_id,
        "drafter_agent_id": draft.drafter_agent_id,
        "assignee_agent_id": draft.assignee_agent_id,
        "status": draft.status,
        "request_message_id": draft.request_message_id,
        "turn_request_id": draft.turn_request_id,
    }
