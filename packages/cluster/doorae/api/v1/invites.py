"""REST endpoints for room invite links — ``/api/v1/rooms/{room_id}/invites``.

PR B of the anonymous-guest RFC (#22). Covers admin-side lifecycle
only: create, list, revoke. Guest-side acceptance (``POST
/auth/guest``) and the runtime guest JWT flow arrive in PR C.

Authorisation rule (issue/list/revoke):
- Global admin (``User.is_admin``) — no room membership required,
  matches other "admin can operate on any room" endpoints.
- Room-level admin or owner — a ``Participant`` row with role in
  ``{"admin", "owner"}`` grants scoped access.

Anyone else, including guests (``identity.kind == "guest"``, landing
in PR C) and rank-and-file room members, is rejected with 403.

Abuse guards (§11.7 of the design doc):
- Per-admin invite creation rate limit: 10 POSTs/min/user.
- Per-room active invite cap: 20. "Active" = not revoked and not
  expired. The cap targets operational noise rather than security
  (revoking anything brings the room back under the cap).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.auth.invite_token import generate_invite_token, hash_invite_token
from doorae.db.models import Participant, Room, RoomInviteLink
from doorae.dependencies import get_current_identity, get_db

router = APIRouter(tags=["invites"])

# Active-invite cap and creation rate limit live in code rather than
# config because they protect against operator mistakes, not attacker
# exhaustion. Making them tunable without a code change would invite
# misconfiguration.
_MAX_ACTIVE_INVITES_PER_ROOM = 20
_CREATE_RATE_LIMIT_WINDOW_SECONDS = 60
_CREATE_RATE_LIMIT_MAX_PER_WINDOW = 10

# In-process sliding-window counter keyed by user_id. Good enough for
# a single-replica deployment; a multi-replica setup would move this
# to the shared rate limiter in ``orchestration.rules``.
_create_buckets: dict[str, list[float]] = {}


def _check_create_rate_limit(user_id: str) -> None:
    """Raise 429 if *user_id* has issued too many invites recently."""
    now = time.monotonic()
    window_start = now - _CREATE_RATE_LIMIT_WINDOW_SECONDS
    bucket = _create_buckets.setdefault(user_id, [])
    # Drop expired timestamps before counting — keeps the bucket small.
    bucket[:] = [t for t in bucket if t >= window_start]
    if len(bucket) >= _CREATE_RATE_LIMIT_MAX_PER_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Invite creation rate limit exceeded; retry later.",
        )
    bucket.append(now)
    # Prune per-user entries whose bucket became empty after the drop.
    # Without this a stream of distinct user_ids would grow the dict
    # without bound. We walk all keys rather than tracking age because
    # the dict is small in practice and this keeps the invariant simple.
    if len(_create_buckets) > 256:
        empty_keys = [k for k, v in _create_buckets.items() if not v]
        for k in empty_keys:
            del _create_buckets[k]


def _reset_create_rate_limit() -> None:
    """Test helper — clears the in-process bucket."""
    _create_buckets.clear()


# -- Request / Response schemas ----------------------------------------------


class InviteCreate(BaseModel):
    # ``None`` means the link never expires. A concrete number clamps
    # the expiry to ``now + expires_in_seconds``. Ceiling of 30 days
    # keeps dormant links from accumulating indefinitely — admins can
    # always issue a fresh one.
    expires_in_seconds: Optional[int] = Field(
        default=None, ge=60, le=60 * 60 * 24 * 30
    )
    # ``None`` means unlimited redemptions. A concrete number caps
    # how many guests may accept the same token.
    max_uses: Optional[int] = Field(default=None, ge=1, le=1000)


class InviteCreated(BaseModel):
    """Returned exactly once, at creation time. ``token`` is the
    plaintext invite — it's never readable from the server again."""

    id: str
    room_id: str
    token: str
    created_at: datetime
    expires_at: Optional[datetime]
    max_uses: Optional[int]
    use_count: int


class InviteOut(BaseModel):
    id: str
    room_id: str
    created_by_user_id: str
    created_at: datetime
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    max_uses: Optional[int]
    use_count: int


# -- Authorisation helpers ---------------------------------------------------


async def _require_room_admin_or_owner(
    room_id: str, identity: Identity, db: AsyncSession
) -> Participant | None:
    """Return the caller's Participant row when they may manage this
    room's invites, or raise 403.

    Global admins pass without a Participant row (return ``None``);
    room-level admins/owners pass with their matching row.
    """
    # Block guests before touching the DB — defence in depth. The
    # guest identity kind lands in PR C, but writing the guard here
    # means merging PR C doesn't re-open this endpoint.
    if identity.kind == "guest":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if (
        identity.kind == "user"
        and identity.claims is not None
        and identity.claims.is_admin
    ):
        return None

    stmt = select(Participant).where(Participant.room_id == room_id)
    if identity.kind == "user":
        stmt = stmt.where(Participant.user_id == identity.id)
    elif identity.kind == "agent":
        # Agents are room members but never room administrators for
        # this feature — explicitly rejected below.
        stmt = stmt.where(Participant.agent_id == identity.id)
    else:  # pragma: no cover — should not reach here
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    part = (await db.execute(stmt)).scalar_one_or_none()
    if part is None or part.role not in ("admin", "owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Room admin or owner required",
        )
    return part


def _active_invite_predicate(now: datetime):
    """SQLAlchemy predicate for "this invite is still usable"."""
    return and_(
        RoomInviteLink.revoked_at.is_(None),
        or_(
            RoomInviteLink.expires_at.is_(None),
            RoomInviteLink.expires_at > now,
        ),
        or_(
            RoomInviteLink.max_uses.is_(None),
            RoomInviteLink.use_count < RoomInviteLink.max_uses,
        ),
    )


# -- Endpoints ---------------------------------------------------------------


@router.post(
    "/api/v1/rooms/{room_id}/invites",
    status_code=status.HTTP_201_CREATED,
    response_model=InviteCreated,
)
async def create_invite(
    room_id: str,
    body: InviteCreate,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Issue a new invite link for *room_id*.

    Returns the plaintext token **once**. The admin must copy it
    immediately; the server keeps only the argon2 hash.
    """
    # Authorisation runs BEFORE the existence check to avoid leaking
    # "room X exists" to non-members through 404-vs-403 timing. For
    # non-admins, missing Participant → 403 regardless of whether
    # the room exists. Global admins can already enumerate rooms via
    # ``GET /api/v1/rooms``, so the later 404 for them adds no
    # oracle.
    await _require_room_admin_or_owner(room_id, identity, db)

    room = (
        await db.execute(select(Room).where(Room.id == room_id))
    ).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    # Global admins have no Participant in the room, so rate-limit
    # by identity.id (user id) — that's the principal that performed
    # the action regardless of role.
    _check_create_rate_limit(identity.id)

    # Active-invite cap check. NOTE: this is a best-effort guard
    # against operator noise, NOT a security boundary. Two concurrent
    # POSTs can both pass the SELECT and both INSERT, briefly pushing
    # the active count to N+1; the per-user 10/min rate limit bounds
    # the worst-case overshoot at ≤ (concurrency) per window. A
    # stricter guarantee would require advisory locking or a unique
    # partial index on ``(room_id, <active-predicate>)``, which SQLite
    # can't express succinctly. If the overshoot ever matters, wrap
    # the SELECT+INSERT in SERIALIZABLE isolation on the target DB.
    now = datetime.now(timezone.utc)
    active_count_stmt = select(RoomInviteLink).where(
        RoomInviteLink.room_id == room_id,
        _active_invite_predicate(now),
    )
    active = (await db.execute(active_count_stmt)).scalars().all()
    if len(active) >= _MAX_ACTIVE_INVITES_PER_ROOM:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Room already has {len(active)} active invites "
                f"(max {_MAX_ACTIVE_INVITES_PER_ROOM}); revoke one first."
            ),
        )

    token = generate_invite_token()
    token_hash, hint = hash_invite_token(token)
    expires_at = (
        now + timedelta(seconds=body.expires_in_seconds)
        if body.expires_in_seconds is not None
        else None
    )

    invite = RoomInviteLink(
        room_id=room_id,
        created_by_user_id=identity.id,
        token_hash=token_hash,
        lookup_hint=hint,
        expires_at=expires_at,
        max_uses=body.max_uses,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    return InviteCreated(
        id=invite.id,
        room_id=invite.room_id,
        token=token,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        max_uses=invite.max_uses,
        use_count=invite.use_count,
    )


@router.get(
    "/api/v1/rooms/{room_id}/invites",
    response_model=list[InviteOut],
)
async def list_invites(
    room_id: str,
    include_revoked: bool = False,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """List invites for *room_id*. Token hashes are never returned."""
    # Authz first — see ``create_invite`` for the rationale.
    await _require_room_admin_or_owner(room_id, identity, db)

    room = (
        await db.execute(select(Room).where(Room.id == room_id))
    ).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    stmt = select(RoomInviteLink).where(RoomInviteLink.room_id == room_id)
    if not include_revoked:
        stmt = stmt.where(RoomInviteLink.revoked_at.is_(None))
    stmt = stmt.order_by(RoomInviteLink.created_at.desc())

    rows = (await db.execute(stmt)).scalars().all()
    return [
        InviteOut(
            id=r.id,
            room_id=r.room_id,
            created_by_user_id=r.created_by_user_id,
            created_at=r.created_at,
            expires_at=r.expires_at,
            revoked_at=r.revoked_at,
            max_uses=r.max_uses,
            use_count=r.use_count,
        )
        for r in rows
    ]


@router.delete("/api/v1/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an existing invite. Idempotent — revoking twice is a no-op."""
    invite = (
        await db.execute(
            select(RoomInviteLink).where(RoomInviteLink.id == invite_id)
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    # Authorisation is scoped to the invite's room, not the URL —
    # this endpoint doesn't carry a room_id to spoof.
    await _require_room_admin_or_owner(invite.room_id, identity, db)

    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(timezone.utc)
        await db.commit()
    return None
