"""Tests for JWT and agent token authentication."""

from __future__ import annotations

import time
import secrets

import pytest

from doorae.auth.jwt import InvalidToken, UserClaims, create_user_token, verify_user_token
from doorae.auth.token import generate_token, hash_token, verify_token_hash
from doorae.auth.dependencies import Identity, get_identity
from doorae.config import DooraeSettings


_SECRET = "test-secret-key-for-jwt-testing-only"


# ── JWT Tests ─────────────────────────────────────────────────────────


class TestJWTCreate:
    def test_create_returns_string(self) -> None:
        token = create_user_token("u1", "a@b.com", False, secret=_SECRET)
        assert isinstance(token, str)
        assert len(token) > 20

    def test_create_token_is_decodable(self) -> None:
        token = create_user_token("u1", "a@b.com", True, secret=_SECRET)
        claims = verify_user_token(token, secret=_SECRET)
        assert claims.user_id == "u1"
        assert claims.email == "a@b.com"
        assert claims.is_admin is True


class TestJWTVerify:
    def test_verify_valid_token(self) -> None:
        token = create_user_token("u2", "b@c.com", False, secret=_SECRET)
        claims = verify_user_token(token, secret=_SECRET)
        assert isinstance(claims, UserClaims)
        assert claims.user_id == "u2"

    def test_verify_expired_token(self) -> None:
        from datetime import datetime, timedelta, timezone
        from jose import jwt as jose_jwt

        # Manually craft an already-expired token (exp in the past)
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "u3",
            "email": "c@d.com",
            "is_admin": False,
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
        }
        token = jose_jwt.encode(payload, _SECRET, algorithm="HS256")
        with pytest.raises(InvalidToken):
            verify_user_token(token, secret=_SECRET)

    def test_verify_invalid_token_string(self) -> None:
        with pytest.raises(InvalidToken):
            verify_user_token("not.a.valid.jwt", secret=_SECRET)

    def test_verify_wrong_secret(self) -> None:
        token = create_user_token("u4", "d@e.com", False, secret=_SECRET)
        with pytest.raises(InvalidToken):
            verify_user_token(token, secret="wrong-secret")


# ── Agent Token Tests ─────────────────────────────────────────────────


class TestAgentToken:
    def test_generate_has_prefix(self) -> None:
        token = generate_token()
        assert token.startswith("agt_")

    def test_generate_is_unique(self) -> None:
        tokens = {generate_token() for _ in range(50)}
        assert len(tokens) == 50

    def test_hash_and_verify_match(self) -> None:
        token = generate_token()
        hashed = hash_token(token)
        assert verify_token_hash(token, hashed) is True

    def test_verify_wrong_token(self) -> None:
        hashed = hash_token(generate_token())
        assert verify_token_hash("agt_wrong", hashed) is False


# ── Identity Parsing Tests ────────────────────────────────────────────


class TestIdentityParsing:
    @pytest.mark.asyncio
    async def test_identity_from_bearer_header(self, db, config) -> None:
        token = create_user_token("u10", "x@y.com", False, secret=config.jwt_secret)
        identity = await get_identity(
            db,
            jwt_secret=config.jwt_secret,
            authorization=f"Bearer {token}",
        )
        assert identity.kind == "user"
        assert identity.id == "u10"

    @pytest.mark.asyncio
    async def test_identity_from_ws_subprotocol(self, db, config) -> None:
        token = create_user_token("u11", "y@z.com", True, secret=config.jwt_secret)
        identity = await get_identity(
            db,
            jwt_secret=config.jwt_secret,
            sec_websocket_protocol=f"doorae.v1, bearer.{token}",
        )
        assert identity.kind == "user"
        assert identity.id == "u11"
