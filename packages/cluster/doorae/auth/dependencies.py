"""FastAPI / WebSocket dependency helpers for authentication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.jwt import InvalidToken, UserClaims, verify_user_token
from doorae.auth.machine_token import verify_machine_token_hash
from doorae.auth.token import verify_token_hash
from doorae.db.models import AgentToken, MachineToken, Participant


@dataclass(frozen=True, slots=True)
class Identity:
    """Represents an authenticated caller — either a human or an agent."""

    kind: str  # "user" | "agent"
    id: str
    claims: Optional[UserClaims] = None


@dataclass(frozen=True, slots=True)
class MachineIdentity:
    """Represents an authenticated machine daemon."""

    machine_id: str


async def get_identity(
    db: AsyncSession,
    *,
    jwt_secret: str,
    authorization: str | None = None,
    sec_websocket_protocol: str | None = None,
) -> Identity:
    """Resolve an ``Identity`` from HTTP header or WS subprotocol.

    HTTP:  ``Authorization: Bearer <jwt>``
    WS:   ``Sec-WebSocket-Protocol: doorae.v1, bearer.<token>``
    """
    token: str | None = None

    # --- HTTP Authorization header ---
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    # --- WebSocket Sec-WebSocket-Protocol ---
    if token is None and sec_websocket_protocol:
        for part in sec_websocket_protocol.split(","):
            part = part.strip()
            if part.startswith("bearer."):
                token = part[7:]
                break

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )

    # Try JWT first (user tokens)
    if not token.startswith("agt_"):
        try:
            claims = verify_user_token(token, secret=jwt_secret)
            return Identity(kind="user", id=claims.user_id, claims=claims)
        except InvalidToken:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )

    # Agent token — O(1) lookup via AgentToken table using lookup_hint
    hint = token[:12]
    result = await db.execute(
        select(AgentToken).where(
            AgentToken.lookup_hint == hint,
            AgentToken.revoked_at.is_(None),
        )
    )
    candidates = result.scalars().all()

    now = datetime.now(timezone.utc)
    for at in candidates:
        if at.expires_at and at.expires_at < now:
            continue
        if verify_token_hash(token, at.token_hash):
            return Identity(kind="agent", id=at.agent_id)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid agent token",
    )


async def get_machine_identity(
    db: AsyncSession,
    *,
    sec_websocket_protocol: str | None = None,
) -> MachineIdentity | None:
    """Resolve a ``MachineIdentity`` from WS subprotocol.

    Expected: ``Sec-WebSocket-Protocol: doorae.v1, bearer.<mch_token>``
    Returns ``None`` if authentication fails.
    """
    if not sec_websocket_protocol:
        return None

    token: str | None = None
    for part in sec_websocket_protocol.split(","):
        part = part.strip()
        if part.startswith("bearer."):
            token = part[7:]
            break

    if token is None or not token.startswith("mch_"):
        return None

    hint = token[:12]
    result = await db.execute(
        select(MachineToken).where(
            MachineToken.lookup_hint == hint,
            MachineToken.revoked_at.is_(None),
        )
    )
    candidates = result.scalars().all()

    now = datetime.now(timezone.utc)
    for mt in candidates:
        if mt.expires_at and mt.expires_at < now:
            continue
        if verify_machine_token_hash(token, mt.token_hash):
            return MachineIdentity(machine_id=mt.machine_id)

    return None


async def require_room_member(
    room_id: str,
    identity: Identity,
    db: AsyncSession,
) -> Participant:
    """Return the :class:`Participant` row or raise 403."""
    stmt = select(Participant).where(Participant.room_id == room_id)
    if identity.kind == "user":
        stmt = stmt.where(Participant.user_id == identity.id)
    else:
        stmt = stmt.where(Participant.agent_id == identity.id)

    result = await db.execute(stmt)
    participant = result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this room",
        )
    return participant
