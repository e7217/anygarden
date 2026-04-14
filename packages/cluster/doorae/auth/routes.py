"""Auth REST endpoints — ``/api/v1/auth``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.auth.jwt import create_user_token
from doorae.auth.password import hash_password, verify_password
from doorae.db.models import User
from doorae.dependencies import get_current_identity, get_db

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
    identity: Identity = Depends(get_current_identity),
):
    """Return the current authenticated user's info."""
    if identity.claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User claims not available",
        )
    return MeResponse(
        id=identity.claims.user_id,
        email=identity.claims.email,
        is_admin=identity.claims.is_admin,
    )
