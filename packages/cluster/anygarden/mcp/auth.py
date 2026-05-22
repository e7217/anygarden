"""Bearer-token → ``agent_id`` resolver for the MCP server (#120).

Reuses :func:`anygarden.auth.dependencies.get_identity` so token
revocation, expiry, and argon2 verification share the exact same
path user/agent/guest tokens travel elsewhere.  The extra check
here is that the resolved ``Identity.kind`` must be ``"agent"`` —
user/admin JWTs are a different auth axis and mustn't be able to
create skills on behalf of an arbitrary agent.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import get_identity


async def resolve_agent_id(
    db: AsyncSession,
    *,
    authorization: str | None,
    jwt_secret: str,
) -> str:
    """Return the caller's ``agent_id`` or raise 401/403.

    401 means "couldn't authenticate at all" (missing/invalid token);
    403 means "authenticated but not an agent" (a user/admin token).
    Splitting the codes makes cluster observability more useful —
    401 spikes flag credential issues, 403 spikes flag misconfigured
    clients pointing at the wrong transport.
    """
    identity = await get_identity(
        db,
        jwt_secret=jwt_secret,
        authorization=authorization,
    )
    if identity.kind != "agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MCP channel is agent-only",
        )
    return identity.id
