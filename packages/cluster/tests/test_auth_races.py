"""Login throttling and race-free first-admin / invite-use accounting (#790)."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from anygarden.app import create_app
from anygarden.auth.invite_token import hash_invite_token
from anygarden.auth.password import hash_password
from anygarden.auth.routes import LOGIN_FAILURE_LIMIT
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Participant, Project, Room, RoomInviteLink, User
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

INVITE_TOKEN = "inv_" + "c" * 40


@pytest_asyncio.fixture()
async def env(tmp_path) -> AsyncIterator[dict]:
    # A file DB with separate connections, so concurrent requests really
    # contend for the write lock (an in-memory StaticPool would serialize).
    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}",
        jwt_secret=secrets.token_urlsafe(32),
    )
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "sessions": sessions}
    await engine.dispose()


async def _add_user(env, email: str, password: str = "correct-horse") -> None:
    async with env["sessions"]() as db:
        db.add(User(email=email, password_hash=hash_password(password)))
        await db.commit()


def _login(env, email: str, password: str):
    return env["client"].post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


# -- AUTH-02: login throttling -------------------------------------------------


@pytest.mark.req("AUTH-02")
async def test_repeated_login_failures_are_throttled(env):
    await _add_user(env, "a@anygarden.io")
    for _ in range(LOGIN_FAILURE_LIMIT):
        assert (await _login(env, "a@anygarden.io", "wrong")).status_code == 401

    blocked = await _login(env, "a@anygarden.io", "correct-horse")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    # Case differences do not reset the counter.
    assert (await _login(env, "A@Anygarden.io", "wrong")).status_code == 429


@pytest.mark.req("AUTH-02")
async def test_throttling_is_per_email_and_success_resets_it(env):
    await _add_user(env, "a@anygarden.io")
    await _add_user(env, "b@anygarden.io")
    for _ in range(LOGIN_FAILURE_LIMIT - 1):
        await _login(env, "a@anygarden.io", "wrong")
    assert (await _login(env, "a@anygarden.io", "correct-horse")).status_code == 200

    # The success cleared a@'s failures; another full window is allowed.
    for _ in range(LOGIN_FAILURE_LIMIT - 1):
        assert (await _login(env, "a@anygarden.io", "wrong")).status_code == 401
    assert (await _login(env, "a@anygarden.io", "correct-horse")).status_code == 200
    # Unknown accounts count too, and b@ is unaffected by a@.
    for _ in range(LOGIN_FAILURE_LIMIT):
        await _login(env, "nobody@anygarden.io", "x")
    assert (await _login(env, "nobody@anygarden.io", "x")).status_code == 429
    assert (await _login(env, "b@anygarden.io", "correct-horse")).status_code == 200


# -- AUTH-01: exactly one first admin -----------------------------------------


@pytest.mark.req("AUTH-01")
async def test_only_first_registration_is_admin_even_when_concurrent(env):
    responses = await asyncio.gather(*(
        env["client"].post(
            "/api/v1/auth/register",
            json={"email": f"u{i}@anygarden.io", "password": "correct-horse"},
        )
        for i in range(6)
    ))
    assert [r.status_code for r in responses] == [201] * 6

    async with env["sessions"]() as db:
        admins = (await db.scalars(select(User).where(User.is_admin.is_(True)))).all()
    assert len(admins) == 1

    later = await env["client"].post(
        "/api/v1/auth/register", json={"email": "late@anygarden.io", "password": "pw"}
    )
    assert later.status_code == 201
    me = await env["client"].get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {later.json()['token']}"}
    )
    assert me.json()["is_admin"] is False


# -- AUTH-06: invite max_uses under concurrency ------------------------------


@pytest.mark.req("AUTH-06")
async def test_concurrent_accepts_never_exceed_max_uses(env):
    async with env["sessions"]() as db:
        owner = User(email="owner@anygarden.io", password_hash="x")
        project = Project(name="p")
        db.add_all([owner, project])
        await db.flush()
        room = Room(project_id=project.id, name="r")
        db.add(room)
        await db.flush()
        db.add(Participant(room_id=room.id, user_id=owner.id, role="owner"))
        token_hash, hint = hash_invite_token(INVITE_TOKEN)
        invite = RoomInviteLink(
            room_id=room.id, created_by_user_id=owner.id,
            token_hash=token_hash, lookup_hint=hint, max_uses=2,
        )
        db.add(invite)
        await db.commit()
        invite_id = invite.id

    responses = await asyncio.gather(*(
        env["client"].post(
            "/api/v1/auth/guest", json={"token": INVITE_TOKEN, "display_name": f"g{i}"}
        )
        for i in range(6)
    ))

    codes = sorted(r.status_code for r in responses)
    assert codes == [201, 201, 401, 401, 401, 401]
    async with env["sessions"]() as db:
        invite = await db.get(RoomInviteLink, invite_id)
        guests = (await db.scalars(select(User).where(User.is_anonymous.is_(True)))).all()
    assert invite.use_count == 2
    assert len(guests) == 2
