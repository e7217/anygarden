"""JWT creation and verification for human users (HS256).

This module hosts both *registered-user* tokens (``UserClaims``,
issued on email/password login) and *anonymous-guest* tokens
(``GuestClaims``, issued by ``POST /api/v1/auth/guest`` after invite
acceptance). They share the same HS256 secret and signing envelope;
the ``is_guest`` flag in the payload selects the claim shape.
"""

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


@dataclass(frozen=True, slots=True)
class GuestClaims:
    """Anonymous-guest JWT claims.

    Bound to a single ``room_id``; any endpoint that resolves to a
    different room MUST reject the caller. ``invite_id`` preserves
    audit-log provenance even after the guest row is anonymised.
    """

    user_id: str
    room_id: str
    invite_id: str
    display_name: str


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


def create_guest_token(
    *,
    user_id: str,
    room_id: str,
    invite_id: str,
    display_name: str,
    secret: str,
    expires_at: datetime,
) -> str:
    """Return a signed guest JWT.

    ``expires_at`` should be clamped to the invite's expiry (or to a
    short default when the invite has none) so a leaked token never
    outlives the link it came from.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "room_id": room_id,
        "invite_id": invite_id,
        "display_name": display_name,
        "is_guest": True,
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_user_token(token: str, *, secret: str) -> UserClaims:
    """Decode and validate a registered-user JWT.

    Raises :class:`InvalidToken` on signature failure, expiry, or if
    the payload carries ``is_guest=True`` (guests must go through
    ``verify_guest_token``). Callers that need to discriminate should
    use :func:`decode_any_user_token` instead.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError as exc:
        raise InvalidToken(str(exc)) from exc
    if payload.get("is_guest"):
        raise InvalidToken("Guest token cannot authenticate as user")
    return UserClaims(
        user_id=payload["sub"],
        email=payload["email"],
        is_admin=payload.get("is_admin", False),
    )


def verify_guest_token(token: str, *, secret: str) -> GuestClaims:
    """Decode and validate a guest JWT.

    Raises :class:`InvalidToken` if the signature fails, the token is
    expired, or the payload lacks ``is_guest=True``. The returned
    ``GuestClaims.room_id`` MUST be compared to any URL-scoped
    resource the caller intends to touch.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError as exc:
        raise InvalidToken(str(exc)) from exc
    if not payload.get("is_guest"):
        raise InvalidToken("Not a guest token")
    return GuestClaims(
        user_id=payload["sub"],
        room_id=payload["room_id"],
        invite_id=payload["invite_id"],
        display_name=payload.get("display_name", ""),
    )


def decode_any_user_token(token: str, *, secret: str) -> UserClaims | GuestClaims:
    """Decode either a registered-user OR a guest token and dispatch
    by payload. Used by the generic identity resolver which cannot
    know upfront which variant the caller presented."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError as exc:
        raise InvalidToken(str(exc)) from exc
    if payload.get("is_guest"):
        return GuestClaims(
            user_id=payload["sub"],
            room_id=payload["room_id"],
            invite_id=payload["invite_id"],
            display_name=payload.get("display_name", ""),
        )
    return UserClaims(
        user_id=payload["sub"],
        email=payload["email"],
        is_admin=payload.get("is_admin", False),
    )
