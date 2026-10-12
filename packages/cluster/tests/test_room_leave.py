"""ROOM-05 — a user can leave a room themselves.

``POST /api/v1/rooms/{room_id}/leave`` removes the caller's own
participant row. The last admin/owner cannot leave (409, the same
invariant as admin removal), DM rooms cannot be left (409 — a DM is the
user's 1:1 channel with an agent and is deleted, not left), and the
leaver's open room sockets are closed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_guest_token, create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentToken,
    Base,
    Participant,
    Project,
    Room,
    RoomInviteLink,
    User,
)
from anygarden.auth.invite_token import hash_invite_token
from anygarden.ws.manager import ConnectionManager


class _FakeSocket:
    def __init__(self) -> None:
        self.closed_with: tuple[int, str] | None = None
        self.sent: list[str] = []

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def send_json(self, data) -> None:  # pragma: no cover - fallback
        self.sent.append(str(data))


@pytest_asyncio.fixture()
async def env(config: AnygardenSettings) -> AsyncIterator[dict]:
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sessions() as db:
        owner = User(email="owner@anygarden.io", password_hash="x")
        member = User(email="member@anygarden.io", password_hash="x")
        outsider = User(email="out@anygarden.io", password_hash="x")
        guest = User(is_anonymous=True, display_name="G")
        project = Project(name="proj")
        db.add_all([owner, member, outsider, guest, project])
        await db.flush()
        agent = Agent(name="bot", engine="codex")
        db.add(agent)
        await db.flush()
        agent_token = generate_token()
        hashed, agent_hint = hash_agent_token(agent_token)
        db.add(AgentToken(agent_id=agent.id, token_hash=hashed, lookup_hint=agent_hint))
        room = Room(project_id=project.id, name="main")
        dm = Room(project_id=None, name="DM: bot", is_dm=True)
        db.add_all([room, dm])
        await db.flush()
        owner_part = Participant(room_id=room.id, user_id=owner.id, role="owner")
        member_part = Participant(
            room_id=room.id, user_id=member.id, role="member", pinned=True, sort_order=0
        )
        guest_part = Participant(room_id=room.id, user_id=guest.id, role="member")
        agent_part = Participant(room_id=room.id, agent_id=agent.id, role="member")
        dm_owner = Participant(room_id=dm.id, user_id=member.id, role="owner")
        dm_agent = Participant(room_id=dm.id, agent_id=agent.id, role="member")
        token_hash, hint = hash_invite_token("inv_" + "z" * 40)
        invite = RoomInviteLink(
            room_id=room.id,
            created_by_user_id=owner.id,
            token_hash=token_hash,
            lookup_hint=hint,
        )
        db.add_all(
            [owner_part, member_part, guest_part, agent_part, dm_owner, dm_agent, invite]
        )
        await db.commit()
        for obj in (
            owner, member, outsider, guest, agent, room, dm,
            owner_part, member_part, guest_part, agent_part, invite,
        ):
            await db.refresh(obj)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions
    manager = ConnectionManager()
    app.state.connection_manager = manager
    secret = config.jwt_secret
    yield {
        "app": app,
        "sessions": sessions,
        "manager": manager,
        "room": room,
        "dm": dm,
        "owner": owner,
        "member": member,
        "owner_part": owner_part,
        "member_part": member_part,
        "guest_part": guest_part,
        "agent_part": agent_part,
        "owner_token": create_user_token(owner.id, owner.email, False, secret=secret),
        "member_token": create_user_token(member.id, member.email, False, secret=secret),
        "outsider_token": create_user_token(
            outsider.id, outsider.email, False, secret=secret
        ),
        "guest_token": create_guest_token(
            user_id=guest.id,
            room_id=room.id,
            invite_id=invite.id,
            display_name="G",
            secret=secret,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
        "agent_token": agent_token,
    }
    await engine.dispose()


@pytest_asyncio.fixture()
async def client(env) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _participant_ids(env, room_id: str) -> set[str]:
    async with env["sessions"]() as db:
        rows = await db.execute(select(Participant.id).where(Participant.room_id == room_id))
        return set(rows.scalars().all())


@pytest.mark.req("ROOM-05")
async def test_member_can_leave_room(env, client):
    room = env["room"]
    resp = await client.post(
        f"/api/v1/rooms/{room.id}/leave", headers=_auth(env["member_token"])
    )
    assert resp.status_code == 204

    remaining = await _participant_ids(env, room.id)
    assert env["member_part"].id not in remaining
    assert env["owner_part"].id in remaining

    # The room is gone from the leaver's sidebar (pin state went with the row).
    listing = await client.get("/api/v1/rooms", headers=_auth(env["member_token"]))
    assert room.id not in {r["id"] for r in listing.json()}
    # And the room API now refuses them.
    again = await client.get(
        f"/api/v1/rooms/{room.id}/messages", headers=_auth(env["member_token"])
    )
    assert again.status_code == 403


@pytest.mark.req("ROOM-05")
async def test_leave_closes_leavers_sockets_and_notifies_others(env, client):
    manager: ConnectionManager = env["manager"]
    room = env["room"]
    tab_a, tab_b, owner_ws = _FakeSocket(), _FakeSocket(), _FakeSocket()
    member_pid, owner_pid = env["member_part"].id, env["owner_part"].id
    await manager.subscribe(
        room.id, member_pid, tab_a, exclusive=False, user_id=env["member"].id
    )
    await manager.subscribe(
        room.id, member_pid, tab_b, exclusive=False, user_id=env["member"].id
    )
    await manager.subscribe(
        room.id, owner_pid, owner_ws, exclusive=False, user_id=env["owner"].id
    )

    resp = await client.post(
        f"/api/v1/rooms/{room.id}/leave", headers=_auth(env["member_token"])
    )
    assert resp.status_code == 204

    assert tab_a.closed_with is not None and tab_a.closed_with[0] == 4003
    assert tab_b.closed_with is not None and tab_b.closed_with[0] == 4003
    assert owner_ws.closed_with is None
    assert await manager.is_connected(member_pid) is False
    assert any('"removed"' in frame for frame in owner_ws.sent)


@pytest.mark.req("ROOM-05")
async def test_last_owner_cannot_leave(env, client):
    room = env["room"]
    resp = await client.post(
        f"/api/v1/rooms/{room.id}/leave", headers=_auth(env["owner_token"])
    )
    assert resp.status_code == 409
    assert env["owner_part"].id in await _participant_ids(env, room.id)


@pytest.mark.req("ROOM-05")
async def test_owner_can_leave_when_another_admin_remains(env, client):
    room = env["room"]
    async with env["sessions"]() as db:
        part = await db.get(Participant, env["member_part"].id)
        part.role = "admin"
        await db.commit()

    resp = await client.post(
        f"/api/v1/rooms/{room.id}/leave", headers=_auth(env["owner_token"])
    )
    assert resp.status_code == 204
    assert env["owner_part"].id not in await _participant_ids(env, room.id)


@pytest.mark.req("ROOM-05")
async def test_dm_room_cannot_be_left(env, client):
    dm = env["dm"]
    resp = await client.post(
        f"/api/v1/rooms/{dm.id}/leave", headers=_auth(env["member_token"])
    )
    assert resp.status_code == 409
    assert "DM" in resp.json()["detail"]
    assert len(await _participant_ids(env, dm.id)) == 2


@pytest.mark.req("ROOM-05")
async def test_guest_can_leave_room(env, client):
    room = env["room"]
    resp = await client.post(
        f"/api/v1/rooms/{room.id}/leave", headers=_auth(env["guest_token"])
    )
    assert resp.status_code == 204
    assert env["guest_part"].id not in await _participant_ids(env, room.id)


@pytest.mark.req("ROOM-05")
async def test_agent_cannot_self_leave(env, client):
    room = env["room"]
    resp = await client.post(
        f"/api/v1/rooms/{room.id}/leave", headers=_auth(env["agent_token"])
    )
    assert resp.status_code == 403
    assert env["agent_part"].id in await _participant_ids(env, room.id)


@pytest.mark.req("ROOM-05")
async def test_non_member_leave_is_forbidden(env, client):
    room = env["room"]
    resp = await client.post(
        f"/api/v1/rooms/{room.id}/leave", headers=_auth(env["outsider_token"])
    )
    assert resp.status_code == 403


@pytest.mark.req("ROOM-05")
async def test_self_removal_points_at_leave_route(env, client):
    room = env["room"]
    resp = await client.delete(
        f"/api/v1/rooms/{room.id}/participants/{env['owner_part'].id}",
        headers=_auth(env["owner_token"]),
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "leave-room" in detail
    assert f"/api/v1/rooms/{room.id}/leave" in detail
