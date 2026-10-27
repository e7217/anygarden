"""Whose rights a workspace tool call uses (#938).

A workspace tool acts for the person whose message started the agent's
turn, with that person's rights, and only for that turn. The turn proof
names the turn; the turn names its trigger message; the message names its
author. Anything that is not a registered person's own message — another
agent's call, a server notice, a guest, a shared (federated) room — has no
one to act for, so the call is refused.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.auth.jwt import UserClaims
from anygarden.db.models import AgentTurn, Message, Participant, User


class RequesterRefused(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Requester:
    identity: Identity
    user_id: str
    is_admin: bool
    room_id: str
    """Room of the message that started the turn."""
    participant_id: str
    """The person's participant in ``room_id``."""
    agent_participant_id: str
    thread_root_id: str | None
    turn_request_id: str


async def requester_for_turn(db: AsyncSession, *, agent_id: str, proof) -> Requester:
    """The person this turn acts for. Raises :class:`RequesterRefused`."""

    from anygarden.general_tasks import is_system_source
    from anygarden.project_executions.service import ExecutionConflict, authorize_turn

    if proof is None:
        raise RequesterRefused("TURN_PROOF_REQUIRED", "Workspace tools need the current delivered turn")
    try:
        turn: AgentTurn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    except ExecutionConflict as exc:
        raise RequesterRefused(exc.code, exc.detail) from None
    source = await db.get(Message, turn.trigger_message_id) if turn.trigger_message_id else None
    author = await db.get(Participant, source.participant_id) if source and source.participant_id else None
    if source is not None and author is None:
        # The outcome of an approval wakes the agent for the person who
        # asked — never for the approver, whose rights may be higher.
        author = await _asker_of_reported_request(db, source)
    elif source is not None and is_system_source(source):
        author = None
    if source is None or author is None or author.user_id is None or author.agent_id is not None:
        raise RequesterRefused(
            "NO_PERSON_TO_ACT_FOR",
            "Workspace tools act only for a person's own message; this turn was started by an agent or the server",
        )
    user = await db.get(User, author.user_id)
    if user is None or user.is_anonymous:
        raise RequesterRefused("NO_PERSON_TO_ACT_FOR", "Guests cannot direct workspace changes")
    if await _is_shared_room(db, turn.room_id):
        raise RequesterRefused("SHARED_ROOM", "Workspace tools are not available in a shared (federated) room")
    claims = UserClaims(user_id=user.id, email=user.email or "", is_admin=bool(user.is_admin))
    return Requester(
        identity=Identity(kind="user", id=user.id, claims=claims),
        user_id=user.id,
        is_admin=bool(user.is_admin),
        room_id=turn.room_id,
        participant_id=author.id,
        agent_participant_id=turn.target_participant_id,
        thread_root_id=source.root_message_id,
        turn_request_id=turn.request_id,
    )


async def _asker_of_reported_request(db: AsyncSession, source: Message) -> Participant | None:
    from anygarden.db.workspace_action_models import WorkspaceActionRequest

    metadata = source.extra_metadata or {}
    meta = metadata.get("workspace_action_result")
    if metadata.get("system_origin") != "workspace_action_result" or not isinstance(meta, dict) or not isinstance(
        meta.get("request_id"), str
    ):
        return None
    row = await db.get(WorkspaceActionRequest, meta["request_id"])
    if row is None or row.room_id != source.room_id or row.requested_by_user_id is None:
        return None
    return await db.scalar(select(Participant).where(
        Participant.room_id == source.room_id, Participant.user_id == row.requested_by_user_id,
    ))


async def identity_for_user(db: AsyncSession, user_id: str) -> Identity | None:
    """A user's identity with ``is_admin`` read from the DB, as the API does."""

    user = await db.get(User, user_id)
    if user is None or user.is_anonymous:
        return None
    return Identity(
        kind="user", id=user.id,
        claims=UserClaims(user_id=user.id, email=user.email or "", is_admin=bool(user.is_admin)),
    )


async def _is_shared_room(db: AsyncSession, room_id: str) -> bool:
    from anygarden.shared_channels.models import ChannelStream

    return await db.scalar(select(ChannelStream.channel_id).where(ChannelStream.local_room_id == room_id)) is not None
