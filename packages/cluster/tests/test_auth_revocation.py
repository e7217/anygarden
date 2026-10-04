"""Revocation takes effect before the JWT expires (#782).

AUTH-04 — admin rights and account existence are read from the DB on
every request, not from the token's ``is_admin`` claim.
AUTH-08 — revoking an invite cuts off the guest sessions it already let
in: their tokens stop resolving and their open sockets are closed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.dependencies import get_identity
from anygarden.auth.invite_token import hash_invite_token
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Participant, Project, Room, RoomInviteLink, User
from anygarden.ws.manager import ConnectionManager

INVITE_TOKEN = "inv_" + "r" * 40


class _FakeSocket:
    def __init__(self) -> None:
        self.closed_with: tuple[int, str] | None = None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


@pytest_asyncio.fixture()
async def env(config: AnygardenSettings) -> AsyncIterator[dict]:
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sessions() as db:
        owner = User(email="owner@anygarden.io", password_hash="x")
        admin = User(email="admin@anygarden.io", password_hash="x", is_admin=True)
        project = Project(name="proj")
        db.add_all([owner, admin, project])
        await db.flush()
        room = Room(project_id=project.id, name="main")
        db.add(room)
        await db.flush()
        db.add(Participant(room_id=room.id, user_id=owner.id, role="owner"))
        token_hash, hint = hash_invite_token(INVITE_TOKEN)
        invite = RoomInviteLink(
            room_id=room.id,
            created_by_user_id=owner.id,
            token_hash=token_hash,
            lookup_hint=hint,
        )
        db.add(invite)
        await db.commit()
        for obj in (owner, admin, room, invite):
            await db.refresh(obj)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions
    secret = config.jwt_secret
    yield {
        "app": app,
        "sessions": sessions,
        "secret": secret,
        "room": room,
        "invite": invite,
        "owner": owner,
        "admin": admin,
        "owner_token": create_user_token(owner.id, owner.email, False, secret=secret),
        "admin_token": create_user_token(admin.id, admin.email, True, secret=secret),
    }
    await engine.dispose()


@pytest_asyncio.fixture()
async def client(env) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _accept_guest(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/v1/auth/guest", json={"token": INVITE_TOKEN, "display_name": "Guest"}
    )
    assert resp.status_code == 201
    return resp.json()["token"]


# -- AUTH-08: invite revocation ----------------------------------------------


@pytest.mark.req("AUTH-08")
async def test_revoked_invite_rejects_existing_guest_token(env, client):
    guest_token = await _accept_guest(client)
    messages = f"/api/v1/rooms/{env['room'].id}/messages"
    assert (await client.get(messages, headers=_auth(guest_token))).status_code == 200

    revoke = await client.delete(
        f"/api/v1/invites/{env['invite'].id}", headers=_auth(env["owner_token"])
    )
    assert revoke.status_code == 204

    after = await client.get(messages, headers=_auth(guest_token))
    assert after.status_code == 401
    assert after.json()["detail"] == "Invite has been revoked"


@pytest.mark.req("AUTH-08")
async def test_revoked_invite_rejects_guest_websocket_handshake(env, client):
    guest_token = await _accept_guest(client)
    async with env["sessions"]() as db:
        invite = await db.get(RoomInviteLink, env["invite"].id)
        invite.revoked_at = datetime.now(timezone.utc)
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await get_identity(
                db,
                jwt_secret=env["secret"],
                sec_websocket_protocol=f"anygarden.v1, bearer.{guest_token}",
            )
    assert exc.value.status_code == 401


@pytest.mark.req("AUTH-08")
async def test_revoke_endpoint_closes_open_guest_sockets(env, client):
    # The lifespan normally installs the manager; ASGITransport skips it.
    manager = ConnectionManager()
    env["app"].state.connection_manager = manager
    guest_ws, other_ws = _FakeSocket(), _FakeSocket()
    await manager.subscribe(
        env["room"].id, "guest-part", guest_ws, exclusive=False,
        invite_id=env["invite"].id,
    )
    await manager.subscribe(
        env["room"].id, "other-part", other_ws, exclusive=False,
        invite_id="another-invite",
    )

    revoke = await client.delete(
        f"/api/v1/invites/{env['invite'].id}", headers=_auth(env["owner_token"])
    )

    assert revoke.status_code == 204
    assert guest_ws.closed_with == (4001, "Invite revoked")
    assert other_ws.closed_with is None


@pytest.mark.req("AUTH-08")
async def test_manager_revoke_invite_only_closes_matching_sockets():
    manager = ConnectionManager()
    guest_a, guest_b, user_ws = _FakeSocket(), _FakeSocket(), _FakeSocket()
    await manager.subscribe("r1", "p-a", guest_a, exclusive=False, invite_id="inv-a")
    await manager.subscribe("r2", "p-a2", _FakeSocket(), exclusive=False, invite_id="inv-a")
    await manager.subscribe("r1", "p-b", guest_b, exclusive=False, invite_id="inv-b")
    await manager.subscribe("r1", "p-u", user_ws, exclusive=False, user_id="u1")

    closed = await manager.revoke_invite("inv-a")

    assert closed == 2
    assert guest_a.closed_with == (4001, "Invite revoked")
    assert guest_b.closed_with is None
    assert user_ws.closed_with is None
    assert await manager.revoke_invite("inv-a") == 0


# -- AUTH-04: admin rights come from the DB ----------------------------------


@pytest.mark.req("AUTH-04")
async def test_demoted_admin_token_loses_admin_routes(env, client):
    usage = "/api/v1/usage"
    assert (await client.get(usage, headers=_auth(env["admin_token"]))).status_code == 200

    async with env["sessions"]() as db:
        admin = await db.get(User, env["admin"].id)
        admin.is_admin = False
        await db.commit()

    assert (await client.get(usage, headers=_auth(env["admin_token"]))).status_code == 403


@pytest.mark.req("AUTH-04")
async def test_token_of_deleted_user_is_rejected(env, client):
    async with env["sessions"]() as db:
        await db.delete(await db.get(User, env["admin"].id))
        await db.commit()

    resp = await client.get("/api/v1/auth/me", headers=_auth(env["admin_token"]))
    assert resp.status_code == 401


@pytest.mark.req("AUTH-04")
async def test_identity_admin_flag_follows_db_not_token(env):
    forged = create_user_token(
        env["owner"].id, env["owner"].email, True, secret=env["secret"]
    )
    async with env["sessions"]() as db:
        identity = await get_identity(
            db, jwt_secret=env["secret"], authorization=f"Bearer {forged}"
        )
    assert identity.claims.is_admin is False
