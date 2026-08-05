"""Shared FastAPI dependency helpers — DB session and authentication."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity, get_identity


async def get_db(request: Request) -> AsyncSession:
    """Yield a scoped DB session from the app-level session factory.

    Read endpoints normally close without committing. The authorization
    service marks sessions that contain a global-admin bypass audit so those
    append-only rows remain durable after a successful read response. Write
    endpoints keep their existing explicit transaction boundaries.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            if session.info.pop("room_authorization_audit_pending", False):
                await session.commit()


async def get_current_identity(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Identity:
    """Resolve the calling identity from the Authorization header."""
    config = request.app.state.config
    auth_header = request.headers.get("authorization")
    return await get_identity(
        db,
        jwt_secret=config.jwt_secret,
        authorization=auth_header,
    )


async def get_admin_identity(
    identity: Identity = Depends(get_current_identity),
) -> Identity:
    """Like ``get_current_identity`` but rejects non-admin callers with 403.

    Used by agent management endpoints and anything that mutates shared
    infrastructure. Agent tokens and non-admin users are both rejected.
    """
    if (
        identity.kind != "user"
        or not identity.claims
        # Guest claims carry no ``is_admin`` field; be explicit.
        or not hasattr(identity.claims, "is_admin")
        or not identity.claims.is_admin
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return identity


async def forbid_guest(
    identity: Identity = Depends(get_current_identity),
) -> Identity:
    """Pass through registered users and agents; reject guests with 403.

    Apply to any endpoint whose semantics would give a guest more
    authority than the §11 design doc allows (room mutations, sub-room
    creation, invite management, cross-room reads, etc.). The
    counterpart to ``get_current_identity`` — call this wherever you
    would have called that but do NOT want guests to pass.
    """
    if identity.kind == "guest":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is not available to guests",
        )
    return identity
