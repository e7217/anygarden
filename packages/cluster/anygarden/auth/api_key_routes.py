"""Personal API key management — ``/api/v1/auth/api-keys`` (#988).

Only a signed-in browser session may call these routes
(``get_session_identity``): a key cannot create, list or revoke keys.
The plaintext key appears only in the ``POST`` response.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.api_key import SCOPE_ADMIN, generate_api_key, hash_api_key
from anygarden.auth.dependencies import Identity
from anygarden.db.models import User, UserApiKey
from anygarden.dependencies import get_db, get_session_identity

router = APIRouter(prefix="/api/v1/auth/api-keys", tags=["auth"])

# Active (unrevoked, unexpired) keys one person may hold at once.
MAX_ACTIVE_API_KEYS = 20


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: Literal["read", "write", "admin"]
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)


class ApiKeyOut(BaseModel):
    id: str
    name: str
    scope: str
    prefix: str
    created_at: datetime
    expires_at: Optional[datetime]
    last_used_at: Optional[datetime]


class ApiKeyCreated(ApiKeyOut):
    key: str


def _out(key: UserApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        name=key.name,
        scope=key.scope,
        prefix=key.lookup_hint,
        created_at=key.created_at,
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
    )


def _active(now: datetime):
    return (
        UserApiKey.revoked_at.is_(None),
        (UserApiKey.expires_at.is_(None)) | (UserApiKey.expires_at > now),
    )


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    identity: Identity = Depends(get_session_identity),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyOut]:
    """List the caller's active keys, newest first. No plaintext or hash."""
    now = datetime.now(timezone.utc)
    keys = (
        await db.execute(
            select(UserApiKey)
            .where(UserApiKey.user_id == identity.id, *_active(now))
            .order_by(UserApiKey.created_at.desc())
        )
    ).scalars().all()
    return [_out(k) for k in keys]


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate,
    identity: Identity = Depends(get_session_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    """Issue a key. The response is the only place its plaintext appears."""
    if body.scope == SCOPE_ADMIN:
        user = await db.get(User, identity.id)
        if user is None or not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an admin can issue an admin key",
            )
    now = datetime.now(timezone.utc)
    active = (
        await db.execute(
            select(func.count())
            .select_from(UserApiKey)
            .where(UserApiKey.user_id == identity.id, *_active(now))
        )
    ).scalar_one()
    if active >= MAX_ACTIVE_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have {MAX_ACTIVE_API_KEYS} API keys. Revoke one first.",
        )
    plaintext = generate_api_key()
    token_hash, hint = hash_api_key(plaintext)
    key = UserApiKey(
        user_id=identity.id,
        name=body.name.strip() or body.name,
        scope=body.scope,
        token_hash=token_hash,
        lookup_hint=hint,
        created_at=now,
        expires_at=(
            now + timedelta(days=body.expires_in_days) if body.expires_in_days else None
        ),
    )
    db.add(key)
    await db.commit()
    return ApiKeyCreated(**_out(key).model_dump(), key=plaintext)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    identity: Identity = Depends(get_session_identity),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke one of the caller's keys. Someone else's key is a 404."""
    key = await db.get(UserApiKey, key_id)
    if key is None or key.user_id != identity.id or key.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    key.revoked_at = datetime.now(timezone.utc)
    await db.commit()
