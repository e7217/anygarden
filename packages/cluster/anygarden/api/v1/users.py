"""Admin account management — ``/api/v1/admin/users`` (#1058).

Admins list registered accounts, create accounts, grant or remove admin
rights, suspend or restore accounts and reset passwords. Accounts are
suspended rather than deleted: rooms, messages and tasks keep pointing
at the row. ``/api/v1/admin/account-settings`` holds node-wide account
settings (whether new accounts may register).

Guard: an admin cannot suspend or demote themselves. Only an active
admin can call these endpoints, so the node always keeps one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.auth.password import hash_password
from anygarden.auth.settings import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    registration_open,
    set_registration_open,
)
from anygarden.db.models import User
from anygarden.dependencies import get_admin_identity, get_db

router = APIRouter(prefix="/api/v1/admin", tags=["admin-users"])


# ── Schemas ────────────────────────────────────────────────────────────


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    is_admin: bool
    disabled: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=255, pattern=r"^\S+@\S+$")
    password: str = Field(
        ..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )
    is_admin: bool = False


class AdminUserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_admin: Optional[bool] = None
    disabled: Optional[bool] = None


class AdminPasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(
        ..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )


class AccountSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration_open: bool


# ── Helpers ────────────────────────────────────────────────────────────


def _out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email or "",
        is_admin=bool(user.is_admin),
        disabled=user.disabled_at is not None,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


async def _registered_user(db: AsyncSession, user_id: str) -> User:
    user = await db.get(User, user_id)
    if user is None or user.is_anonymous:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


# ── Users ──────────────────────────────────────────────────────────────


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    q: Optional[str] = Query(None, max_length=255),
    _: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Registered accounts, oldest first. Guests are not listed."""
    stmt = select(User).where(User.is_anonymous.is_(False)).order_by(User.created_at)
    if q and q.strip():
        stmt = stmt.where(func.lower(User.email).contains(q.strip().lower()))
    users = (await db.execute(stmt)).scalars().all()
    return [_out(u) for u in users]


@router.post("/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminUserCreate,
    _: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Create an account directly — works while sign-up is closed."""
    email = body.email.strip()
    existing = await db.execute(select(User.id).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )
    user = User(email=email, password_hash=hash_password(body.password), is_admin=body.is_admin)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _out(user)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: str,
    body: AdminUserPatch,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Grant or remove admin rights; suspend or restore the account."""
    user = await _registered_user(db, user_id)
    demoting = body.is_admin is False and user.is_admin
    disabling = body.disabled is True and user.disabled_at is None
    if (demoting or disabling) and user.id == identity.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot suspend or demote your own account",
        )
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.disabled is not None:
        if body.disabled and user.disabled_at is None:
            user.disabled_at = datetime.now(timezone.utc)
        elif not body.disabled:
            user.disabled_at = None
    await db.commit()
    await db.refresh(user)
    return _out(user)


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: str,
    body: AdminPasswordReset,
    _: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Set a new password. The account's existing sessions end."""
    user = await _registered_user(db, user_id)
    user.password_hash = hash_password(body.password)
    user.credentials_changed_at = datetime.now(timezone.utc)
    await db.commit()


# ── Settings ───────────────────────────────────────────────────────────


@router.get("/account-settings", response_model=AccountSettings)
async def get_account_settings(
    _: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    return AccountSettings(registration_open=await registration_open(db))


@router.put("/account-settings", response_model=AccountSettings)
async def put_account_settings(
    body: AccountSettings,
    _: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    await set_registration_open(db, body.registration_open)
    await db.commit()
    return AccountSettings(registration_open=await registration_open(db))
