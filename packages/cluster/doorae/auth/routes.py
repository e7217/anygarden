"""Auth REST endpoints — ``/api/v1/auth``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.auth.invite_token import verify_invite_token
from doorae.auth.jwt import create_guest_token, create_user_token
from doorae.auth.password import hash_password, verify_password
from doorae.db.models import Participant, RoomInviteLink, User
from doorae.dependencies import forbid_guest, get_db
from doorae.observability.metrics import invites_used_total

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ── Request / Response schemas ───────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str
    password: str


class RegisterResponse(BaseModel):
    user_id: str
    token: str


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginUserOut(BaseModel):
    id: str
    email: str
    is_admin: bool


class LoginResponse(BaseModel):
    token: str
    user: LoginUserOut


class MeResponse(BaseModel):
    id: str
    email: str
    is_admin: bool


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user account."""
    # Check email uniqueness
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # First user gets admin privileges
    count_result = await db.execute(select(func.count()).select_from(User))
    user_count = count_result.scalar()
    is_admin = user_count == 0

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        is_admin=is_admin,
    )
    db.add(user)
    await db.flush()

    config = request.app.state.config
    token = create_user_token(
        user_id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        secret=config.jwt_secret,
    )

    await db.commit()

    return RegisterResponse(user_id=user.id, token=token)


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with email and password."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    config = request.app.state.config
    token = create_user_token(
        user_id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        secret=config.jwt_secret,
    )

    return LoginResponse(
        token=token,
        user=LoginUserOut(id=user.id, email=user.email, is_admin=user.is_admin),
    )


@router.get("/dev-token", response_model=LoginResponse)
async def dev_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Auto-login as admin in dev mode. Disabled in production."""
    config = request.app.state.config
    if not config.dev:
        raise HTTPException(status_code=404, detail="Not found")

    result = await db.execute(select(User).where(User.is_admin.is_(True)).limit(1))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=503, detail="No admin user")

    token = create_user_token(
        user_id=user.id, email=user.email, is_admin=True, secret=config.jwt_secret,
    )
    return LoginResponse(
        token=token,
        user=LoginUserOut(id=user.id, email=user.email, is_admin=user.is_admin),
    )


@router.get("/me", response_model=MeResponse)
async def me(
    # Guests shouldn't hit ``/me`` — their JWT already carries all
    # the self-info they need (``room_id``, ``display_name``) and
    # this endpoint's payload shape is registered-user only. Use
    # ``forbid_guest`` instead of ``get_current_identity`` so they
    # get a clean 403 instead of a KeyError on ``.email``.
    identity: Identity = Depends(forbid_guest),
):
    """Return the current authenticated user's info."""
    if identity.claims is None or not hasattr(identity.claims, "email"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User claims not available",
        )
    return MeResponse(
        id=identity.claims.user_id,
        email=identity.claims.email,
        is_admin=identity.claims.is_admin,
    )


# ── Guest acceptance ─────────────────────────────────────────────────


class GuestAuthRequest(BaseModel):
    token: str = Field(..., min_length=12, max_length=256)
    # Displayed in the room's participant list. Enforced length limit
    # keeps UI layout predictable; we don't try to sanitise further —
    # the frontend renders with autoescaping.
    display_name: str = Field(..., min_length=1, max_length=64)


class GuestAuthResponse(BaseModel):
    """Issued once per accepted invite. The client stores ``token``
    and replays it as ``Authorization: Bearer <jwt>`` or via WS
    subprotocol ``bearer.<jwt>``."""

    token: str
    user_id: str
    room_id: str
    display_name: str
    expires_at: datetime


# Fallback expiry when the invite has no ``expires_at`` of its own.
# Keeps leaked guest tokens from outliving their apparent purpose.
_DEFAULT_GUEST_EXPIRY = timedelta(hours=24)


@router.post(
    "/guest",
    status_code=status.HTTP_201_CREATED,
    response_model=GuestAuthResponse,
)
async def accept_guest_invite(
    body: GuestAuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Exchange an invite token for a scoped guest JWT.

    Atomically:
    1. Resolve the invite by ``lookup_hint`` and argon2-verify the
       submitted plaintext.
    2. Reject if revoked, expired, or at its ``max_uses``.
    3. Create a fresh ``User(is_anonymous=True, display_name=…)``
       and a ``Participant`` row in the invite's room.
    4. Increment ``use_count``.
    5. Issue a guest JWT bound to the room, clamped to the invite's
       expiry (or to ``_DEFAULT_GUEST_EXPIRY`` if the invite never
       expires).
    6. Broadcast ``RoomMembershipChangedOut`` so existing room
       members' UIs refresh — mirrors the registered-user path added
       in #19.
    """
    token = body.token
    hint = token[:12]

    invite = (
        await db.execute(
            select(RoomInviteLink).where(RoomInviteLink.lookup_hint == hint)
        )
    ).scalar_one_or_none()
    # Running argon2 on a sentinel keeps the error-path timing close
    # to the happy path. Purely defensive — the attack surface is
    # small, but the cost is negligible.
    if invite is None or not verify_invite_token(token, invite.token_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired invite",
        )

    now = datetime.now(timezone.utc)
    if invite.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invite has been revoked",
        )
    # SQLite returns naive datetimes; normalize before comparing so
    # a "-aware vs -naive" error can't pass as a happy path.
    expires_at_aware = (
        invite.expires_at.replace(tzinfo=timezone.utc)
        if invite.expires_at is not None and invite.expires_at.tzinfo is None
        else invite.expires_at
    )
    if expires_at_aware is not None and expires_at_aware <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invite has expired",
        )
    if invite.max_uses is not None and invite.use_count >= invite.max_uses:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invite has no uses remaining",
        )

    guest = User(
        email=None,
        password_hash=None,
        is_anonymous=True,
        display_name=body.display_name,
    )
    db.add(guest)
    await db.flush()

    participant = Participant(
        room_id=invite.room_id,
        user_id=guest.id,
        role="member",
    )
    db.add(participant)

    invite.use_count = invite.use_count + 1

    # Clamp expiry. A concrete invite expiry caps the JWT; unlimited
    # invites fall back to the default so tokens cannot persist
    # indefinitely. ``expires_at_aware`` was normalized above.
    expires_at = (
        min(expires_at_aware, now + _DEFAULT_GUEST_EXPIRY)
        if expires_at_aware is not None
        else now + _DEFAULT_GUEST_EXPIRY
    )

    config = request.app.state.config
    jwt_token = create_guest_token(
        user_id=guest.id,
        room_id=invite.room_id,
        invite_id=invite.id,
        display_name=guest.display_name or "",
        secret=config.jwt_secret,
        expires_at=expires_at,
    )

    await db.commit()
    await db.refresh(participant)

    invites_used_total.inc()

    # Best-effort UI-refresh push to *existing* room members. The
    # guest itself has no prior WS in this (or any) room, so the
    # only interesting audience is the people already subscribed —
    # their participant_ids are scoped by room, not by user_id.
    # (An earlier version of this code filtered by the newly-made
    # guest's user_id, which was always empty — the notification
    # never fired. Review feedback for PR C.)
    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None:
        from doorae.ws.protocol import RoomMembershipChangedOut

        other_pids = (
            await db.execute(
                select(Participant.id).where(
                    Participant.room_id == invite.room_id,
                    Participant.id != participant.id,
                )
            )
        ).scalars().all()
        frame = RoomMembershipChangedOut(
            action="added", room_id=invite.room_id, user_id=guest.id
        )
        for pid in other_pids:
            await manager.send_to(pid, frame)

    return GuestAuthResponse(
        token=jwt_token,
        user_id=guest.id,
        room_id=invite.room_id,
        display_name=guest.display_name or "",
        expires_at=expires_at,
    )
