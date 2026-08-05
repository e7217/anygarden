"""Room-scoped authorization policy.

Every room resource must be authorized through the same contract:

``identity × room × capability × room state``

The module is deliberately import-light.  It knows about authentication
identities and the three room-scoped ORM rows needed to resolve access, but it
does not import routers, services, or WebSocket code.  REST, MCP, and WS paths
can therefore share it without creating an API-layer dependency cycle.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import StrEnum

from fastapi import HTTPException, status
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.auth.jwt import GuestClaims
from anygarden.db.models import Participant, Room, Task

ROOM_VISIBILITY_VALUES: frozenset[str] = frozenset({"private"})
ROOM_ROLE_VALUES: frozenset[str] = frozenset({"observer", "member", "admin", "owner"})


class Capability(StrEnum):
    """Stable vocabulary for room-scoped actions.

    Keep the names resource-oriented so new transports can reuse the policy
    without translating REST verbs or WS frame names into a second matrix.
    """

    ROOM_READ = "room.read"
    ROOM_SETTINGS_MANAGE = "room.settings.manage"
    ROOM_ROLE_MANAGE = "room.role.manage"
    ROOM_VISIBILITY_MANAGE = "room.visibility.manage"
    ROOM_ARCHIVE = "room.archive"
    ROOM_UNARCHIVE = "room.unarchive"
    ROOM_DELETE = "room.delete"
    SUBROOM_CREATE = "subroom.create"

    MESSAGE_SEND = "message.send"
    TYPING_SEND = "typing.send"
    LIFECYCLE_WRITE = "lifecycle.write"
    SELF_STATE_WRITE = "self_state.write"
    AGENT_WAKE = "agent.wake"

    TASK_READ = "task.read"
    TASK_CREATE = "task.create"
    TASK_UPDATE = "task.update"
    TASK_MANAGE = "task.manage"

    FILE_READ = "file.read"
    FILE_MANAGE = "file.manage"
    ARTIFACT_READ = "artifact.read"
    ARTIFACT_MANAGE = "artifact.manage"

    MEMBER_MANAGE = "member.manage"
    INVITE_MANAGE = "invite.manage"


_READ_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.ROOM_READ,
        Capability.TASK_READ,
        Capability.FILE_READ,
        Capability.ARTIFACT_READ,
    }
)

_MEMBER_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        *_READ_CAPABILITIES,
        Capability.MESSAGE_SEND,
        Capability.TYPING_SEND,
        Capability.SELF_STATE_WRITE,
        Capability.TASK_CREATE,
    }
)

_ADMIN_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        *_MEMBER_CAPABILITIES,
        Capability.ROOM_SETTINGS_MANAGE,
        Capability.ROOM_ARCHIVE,
        Capability.SUBROOM_CREATE,
        Capability.TASK_UPDATE,
        Capability.TASK_MANAGE,
        Capability.FILE_MANAGE,
        Capability.ARTIFACT_MANAGE,
        Capability.MEMBER_MANAGE,
        Capability.INVITE_MANAGE,
        Capability.AGENT_WAKE,
    }
)

_OWNER_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        *_ADMIN_CAPABILITIES,
        Capability.ROOM_ROLE_MANAGE,
        Capability.ROOM_VISIBILITY_MANAGE,
        Capability.ROOM_UNARCHIVE,
        Capability.ROOM_DELETE,
    }
)

# Archive is a state gate, not a role.  Even a global administrator cannot
# keep mutating an archived room through a different transport.  The only
# state-changing escape hatches are explicit unarchive and delete operations.
_ARCHIVED_ALLOWED_CAPABILITIES: frozenset[Capability] = frozenset(
    {*_READ_CAPABILITIES, Capability.ROOM_UNARCHIVE, Capability.ROOM_DELETE}
)


@dataclass(frozen=True, slots=True)
class RoomAccess:
    """Resolved identity membership and current room state."""

    identity: Identity
    room: Room
    participant: Participant | None
    effective_role: str | None
    is_global_admin: bool = False

    @property
    def is_archived(self) -> bool:
        return self.room.archived_at is not None


def is_global_admin(identity: Identity) -> bool:
    """Return whether *identity* carries the operator-level bypass claim."""

    return bool(
        identity.kind == "user"
        and identity.claims is not None
        and getattr(identity.claims, "is_admin", False)
    )


def validate_room_role(role: str) -> str:
    """Validate a persisted/requested participant role, failing closed."""

    if role not in ROOM_ROLE_VALUES:
        raise ValueError(f"role must be one of {sorted(ROOM_ROLE_VALUES)}")
    return role


def validate_room_visibility(visibility: str) -> str:
    """MVP visibility validation: rooms are private-only."""

    if visibility not in ROOM_VISIBILITY_VALUES:
        raise ValueError(f"visibility must be one of {sorted(ROOM_VISIBILITY_VALUES)}")
    return visibility


async def resolve_access(
    db: AsyncSession,
    *,
    room_id: str,
    identity: Identity,
) -> RoomAccess:
    """Resolve current room access or raise 403/404.

    For ordinary identities, membership is checked before loading the Room so
    an outsider gets the same 403 for an existing and a nonexistent room.
    Global administrators already have room-discovery authority and therefore
    receive a conventional 404 for an unknown id.

    Agent participants are always clamped to ``member``.  This makes a stale
    or hand-edited ``admin``/``owner`` row harmless and enforces the invariant
    that agents never manage membership, roles, invites, visibility, or room
    lifecycle.
    """

    if is_global_admin(identity):
        room = await db.scalar(select(Room).where(Room.id == room_id))
        if room is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Room not found",
            )
        # A global administrator does not need membership, but preserve an
        # existing Participant when present. Stateful transports such as the
        # room WebSocket still need that row as their sender/subscription id.
        participant = await db.scalar(
            select(Participant)
            .where(
                Participant.room_id == room_id,
                Participant.user_id == identity.id,
            )
            .order_by(
                case(
                    (Participant.role == "owner", 0),
                    (Participant.role == "admin", 1),
                    (Participant.role == "member", 2),
                    (Participant.role == "observer", 3),
                    else_=4,
                ),
                Participant.joined_at.asc(),
                Participant.id.asc(),
            )
            .limit(1)
        )
        return RoomAccess(
            identity=identity,
            room=room,
            participant=participant,
            effective_role=(
                participant.role
                if participant is not None and participant.role in ROOM_ROLE_VALUES
                else None
            ),
            is_global_admin=True,
        )

    if identity.kind == "guest":
        if (
            not isinstance(identity.claims, GuestClaims)
            or identity.claims.room_id != room_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Guest token bound to a different room",
            )
        identity_predicate = Participant.user_id == identity.id
    elif identity.kind == "user":
        identity_predicate = Participant.user_id == identity.id
    elif identity.kind == "agent":
        identity_predicate = Participant.agent_id == identity.id
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Room access denied",
        )

    # Migration 052 prevents new duplicates, but ordering/limit keeps legacy
    # or partially migrated databases available and preserves the highest
    # human role. Unknown roles sort last and are denied below.
    participant = await db.scalar(
        select(Participant)
        .where(Participant.room_id == room_id, identity_predicate)
        .order_by(
            case(
                (Participant.role == "owner", 0),
                (Participant.role == "admin", 1),
                (Participant.role == "member", 2),
                (Participant.role == "observer", 3),
                else_=4,
            ),
            Participant.joined_at.asc(),
            Participant.id.asc(),
        )
        .limit(1)
    )
    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this room",
        )

    room = await db.scalar(select(Room).where(Room.id == room_id))
    if room is None:
        # A valid Participant should make this unreachable with foreign keys
        # enabled, but do not manufacture a usable access object from corrupt
        # state.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found",
        )

    effective_role = "member" if identity.kind == "agent" else participant.role
    if effective_role not in ROOM_ROLE_VALUES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid room role",
        )

    return RoomAccess(
        identity=identity,
        room=room,
        participant=participant,
        effective_role=effective_role,
    )


async def accessible_room_ids(
    db: AsyncSession,
    *,
    identity: Identity,
) -> frozenset[str]:
    """Return rooms whose read capability is currently available.

    Collection/search endpoints use this batch primitive instead of embedding
    their own Participant joins. It preserves the same guest binding, global
    admin bypass, private-membership semantics, and fail-closed identity
    handling as :func:`resolve_access`.
    """

    if is_global_admin(identity):
        rows = await db.scalars(select(Room.id))
        return frozenset(rows.all())

    if identity.kind == "guest":
        if not isinstance(identity.claims, GuestClaims):
            return frozenset()
        room_id = identity.claims.room_id
        predicate = Participant.user_id == identity.id
    elif identity.kind == "user":
        room_id = None
        predicate = Participant.user_id == identity.id
    elif identity.kind == "agent":
        room_id = None
        predicate = Participant.agent_id == identity.id
    else:
        return frozenset()

    stmt = select(Participant.room_id).where(
        predicate,
        Participant.role.in_(ROOM_ROLE_VALUES),
    )
    if room_id is not None:
        stmt = stmt.where(Participant.room_id == room_id)
    rows = await db.scalars(stmt)
    return frozenset(rows.all())


def require_active_room(access: RoomAccess) -> RoomAccess:
    """Require the resolved room to be active for a write operation."""

    if access.is_archived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Room is archived",
        )
    return access


def _role_capabilities(access: RoomAccess) -> AbstractSet[Capability]:
    if access.is_global_admin:
        return frozenset(Capability)
    if access.identity.kind == "guest":
        # A guest token is single-room scoped and intentionally has no Task,
        # file-management, settings, or administration surface.
        return frozenset(
            {
                Capability.ROOM_READ,
                Capability.MESSAGE_SEND,
                Capability.TYPING_SEND,
            }
        )
    if access.effective_role == "observer":
        return _READ_CAPABILITIES
    if access.effective_role == "member":
        caps = set(_MEMBER_CAPABILITIES)
        if access.identity.kind == "agent":
            caps.add(Capability.LIFECYCLE_WRITE)
        return frozenset(caps)
    if access.effective_role == "admin":
        return _ADMIN_CAPABILITIES
    if access.effective_role == "owner":
        return _OWNER_CAPABILITIES
    return frozenset()


def _require_task_update_scope(
    access: RoomAccess,
    *,
    task: Task | None,
    changed_fields: AbstractSet[str] | None,
) -> None:
    """Apply the member-agent exception for self-assigned status updates."""

    if access.is_global_admin or access.effective_role in {"admin", "owner"}:
        return
    if access.identity.kind != "agent" or access.participant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Task management requires a room admin or owner",
        )
    if task is None or task.assignee_participant_id != access.participant.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assignee agent may update this task",
        )
    if not changed_fields or not set(changed_fields) <= {"status"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Assignee agents may update task status only",
        )


async def require_capability(
    db: AsyncSession,
    *,
    room_id: str,
    identity: Identity,
    capability: Capability,
    task: Task | None = None,
    changed_fields: AbstractSet[str] | None = None,
) -> RoomAccess:
    """Resolve access and require *capability* against current room state.

    Call this on every request and every client-to-server WS frame.  It does
    not cache membership or archive state, which makes removals and archives
    effective without reconnecting the client.
    """

    access = await resolve_access(db, room_id=room_id, identity=identity)

    if access.is_archived and capability not in _ARCHIVED_ALLOWED_CAPABILITIES:
        require_active_room(access)

    # Agents get a narrow, target-sensitive exception even though the generic
    # member capability set deliberately excludes TASK_UPDATE.
    if capability == Capability.TASK_UPDATE and access.identity.kind == "agent":
        _require_task_update_scope(
            access,
            task=task,
            changed_fields=changed_fields,
        )
        return access

    if capability not in _role_capabilities(access):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Room capability required: {capability.value}",
        )

    if capability == Capability.LIFECYCLE_WRITE and access.identity.kind != "agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Lifecycle frames require an agent identity",
        )

    return access


__all__ = [
    "ROOM_ROLE_VALUES",
    "ROOM_VISIBILITY_VALUES",
    "Capability",
    "RoomAccess",
    "accessible_room_ids",
    "is_global_admin",
    "require_active_room",
    "require_capability",
    "resolve_access",
    "validate_room_role",
    "validate_room_visibility",
]
