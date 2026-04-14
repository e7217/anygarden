"""JWT creation and verification for human users (HS256)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from jose import JWTError, jwt


class InvalidToken(Exception):
    """Raised when a JWT cannot be decoded or is expired."""


@dataclass(frozen=True, slots=True)
class UserClaims:
    user_id: str
    email: str
    is_admin: bool


def create_user_token(
    user_id: str,
    email: str,
    is_admin: bool,
    *,
    secret: str,
    expire_hours: int = 24,
) -> str:
    """Return a signed JWT for the given user."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "is_admin": is_admin,
        "iat": now,
        "exp": now + timedelta(hours=expire_hours),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_user_token(token: str, *, secret: str) -> UserClaims:
    """Decode and validate a JWT.  Raises :class:`InvalidToken` on failure."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError as exc:
        raise InvalidToken(str(exc)) from exc
    return UserClaims(
        user_id=payload["sub"],
        email=payload["email"],
        is_admin=payload.get("is_admin", False),
    )
