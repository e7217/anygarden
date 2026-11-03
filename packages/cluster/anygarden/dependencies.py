"""Shared FastAPI dependency helpers — DB session and authentication."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.api_key import API_KEY_PREFIX, SCOPE_READ, SCOPE_WRITE, scope_allows
from anygarden.auth.dependencies import Identity, get_identity
from anygarden.auth.limiter import FailureLimiter
from anygarden.rooms.authorization import room_authorization_session


async def get_db(request: Request) -> AsyncSession:
    """Yield a DB session, then persist buffered authorization audits.

    The caller session is closed before the audit writer checks out another
    pooled connection. This keeps business rollback independent from audit
    durability without risking nested pool-checkout deadlocks.
    """

    session_factory = request.app.state.session_factory
    async with room_authorization_session(session_factory) as session:
        yield session


# #988 — wrong API keys allowed per client address within the window
# before 429. Scripts retry, so this is looser than the login limit.
API_KEY_FAILURE_LIMIT = 20
API_KEY_FAILURE_WINDOW_SECONDS = 15 * 60

# HTTP methods a ``read`` API key may call; every other method needs
# ``write``. Applied here once instead of annotating every route.
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _api_key_limiter(request: Request) -> FailureLimiter:
    limiter = getattr(request.app.state, "api_key_limiter", None)
    if limiter is None:
        limiter = request.app.state.api_key_limiter = FailureLimiter(
            API_KEY_FAILURE_LIMIT, API_KEY_FAILURE_WINDOW_SECONDS
        )
    return limiter


def required_api_key_scope(method: str) -> str:
    """Return the scope an API key needs to call *method*."""
    return SCOPE_READ if method.upper() in READ_METHODS else SCOPE_WRITE


async def get_current_identity(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Identity:
    """Resolve the calling identity from the Authorization header.

    A personal API key (#988) is also held to its scope here: a ``read``
    key may only call read methods.
    """
    config = request.app.state.config
    auth_header = request.headers.get("authorization")
    if not (auth_header and auth_header[7:].strip().startswith(API_KEY_PREFIX)):
        return await get_identity(
            db,
            jwt_secret=config.jwt_secret,
            authorization=auth_header,
        )

    limiter = _api_key_limiter(request)
    client = request.client.host if request.client else "unknown"
    retry_after = limiter.retry_after(client)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many invalid API keys. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        identity = await get_identity(
            db,
            jwt_secret=config.jwt_secret,
            authorization=auth_header,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            limiter.record_failure(client)
        raise
    limiter.reset(client)
    required = required_api_key_scope(request.method)
    if not scope_allows(identity.api_key_scope or "", required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This API key does not have the {required} scope",
        )
    return identity


async def get_session_identity(
    identity: Identity = Depends(get_current_identity),
) -> Identity:
    """Accept only a registered person's browser session (#988).

    API key management uses this so a key can never mint, list or
    revoke keys — a leaked key cannot extend its own reach.
    """
    if identity.kind != "user" or identity.api_key_scope is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sign in to manage API keys",
        )
    return identity


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
