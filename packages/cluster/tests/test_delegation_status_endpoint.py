"""Read-only delegation status endpoint (#593 support, task #35).

Room-scoped ``DelegationMirror`` reads for UI polling. Authorization goes
through the ordinary rooms path with the explicit shared-room opt-in, so
membership/archive/role fences stay intact; ordinary rooms keep rejecting
shared-channel traffic on every other endpoint.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from anygarden.auth.dependencies import Identity
from anygarden.auth.jwt import GuestClaims
from anygarden.db.models import Base, Participant, Room, User
from anygarden.dependencies import get_current_identity, get_db
from anygarden.federation.delegation_models import DelegationMirror
from anygarden.rooms.authorization import room_authorization_session
from anygarden.rooms.delegation_status import router
from anygarden.shared_channels.models import ChannelStream

AUTHORITY, CHANNEL = "node-authority", "channel-1"


def uid() -> str:
    return str(uuid4())


@pytest.fixture
async def env():
    path = f"/tmp/anygarden-delegation-status-{uid()}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    room, other = uid(), uid()
    member, outsider, guest = uid(), uid(), uid()
    async with sessions.begin() as db:
        for user_id in (member, outsider, guest):
            db.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="x"))
        db.add(Room(id=room, name="mirror"))
        db.add(Room(id=other, name="ordinary"))
        db.add(Participant(room_id=room, user_id=member, role="member"))
        db.add(Participant(room_id=room, user_id=guest, role="observer"))
        db.add(Participant(room_id=other, user_id=member, role="member"))
        db.add(
            ChannelStream(
                authority_node_id=AUTHORITY,
                channel_id=CHANNEL,
                local_room_id=room,
                last_seq=0,
                applied_seq=0,
            )
        )

    def mirror(state: str, process_state: str, task_status: str, revision: int):
        delegation = uid()
        return DelegationMirror(
            authority_node_id=AUTHORITY,
            channel_id=CHANNEL,
            delegation_id=delegation,
            task_id=uid(),
            source_message_id=uid(),
            requester={"node_id": AUTHORITY, "kind": "agent", "principal_id": uid()},
            executor={"node_id": "node-executor", "agent_id": uid()},
            execution_id=uid() if state != "requested" else None,
            revision=revision,
            state=state,
            process_state=process_state,
            task_status=task_status,
        )

    rows = {
        "accepted": mirror("accepted", "unknown", "in_progress", 2),
        "running": mirror("running", "running", "in_progress", 3),
        "cancel_requested": mirror("cancel_requested", "running", "blocked", 4),
        "unknown": mirror("unknown", "unknown", "blocked", 3),
        "completed": mirror("completed", "finished", "done", 4),
        "other_channel": DelegationMirror(
            authority_node_id=AUTHORITY,
            channel_id="channel-elsewhere",
            delegation_id=uid(),
            task_id=uid(),
            source_message_id=uid(),
            requester={"node_id": AUTHORITY, "kind": "agent", "principal_id": uid()},
            executor={"node_id": "node-executor", "agent_id": uid()},
            execution_id=uid(),
            revision=5,
            state="completed",
            process_state="finished",
            task_status="done",
        ),
    }
    async with sessions.begin() as db:
        db.add_all(rows.values())

    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = sessions

    async def _get_db():
        async with room_authorization_session(sessions) as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    def identity_for(user_id: str, *, guest_of: str | None = None):
        claims = (
            GuestClaims(
                user_id=user_id,
                room_id=guest_of,
                invite_id=uid(),
                display_name="guest",
            )
            if guest_of
            else None
        )
        app.dependency_overrides[get_current_identity] = lambda: Identity(
            kind="guest" if guest_of else "user",
            id=user_id,
            claims=claims,
        )

    yield {"app": app, "sessions": sessions, "room": room, "other": other,
           "member": member, "outsider": outsider, "guest": guest, "rows": rows,
           "identity_for": identity_for}
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
async def client(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_member_sees_only_bound_channel_mirror_states(env, client):
    env["identity_for"](env["member"])
    r = await client.get(f"/api/v1/rooms/{env['room']}/delegations")
    assert r.status_code == 200
    body = r.json()
    expected = sorted(
        (row for key, row in env["rows"].items() if key != "other_channel"),
        key=lambda row: row.delegation_id,
    )
    assert [row["delegation_id"] for row in body] == [
        row.delegation_id for row in expected
    ]
    states = {row["delegation_id"]: row["state"] for row in body}
    assert set(states.values()) == {
        "accepted", "running", "cancel_requested", "unknown", "completed",
    }
    by_id = {row["delegation_id"]: row for row in body}
    sample = by_id[env["rows"]["accepted"].delegation_id]
    stored = env["rows"]["accepted"]
    assert sample["delegation_id"] == stored.delegation_id
    assert sample["execution_id"] == stored.execution_id
    assert sample["requester"] == stored.requester
    assert sample["executor"] == stored.executor
    assert sample["task_status"] == "in_progress"
    # The other channel's mirror never leaks through this room binding.
    assert all(row["channel_id"] == CHANNEL for row in body)


async def test_read_is_side_effect_free(env, client):
    env["identity_for"](env["member"])
    before = (await env["sessions"]().execute(
        select(DelegationMirror.revision, DelegationMirror.state)
    )).all()
    for _ in range(2):
        assert (await client.get(f"/api/v1/rooms/{env['room']}/delegations")).status_code == 200
    after = (await env["sessions"]().execute(
        select(DelegationMirror.revision, DelegationMirror.state)
    )).all()
    assert before == after


async def test_outsider_and_unknown_room_are_indistinguishable_403(env, client):
    env["identity_for"](env["outsider"])
    real = await client.get(f"/api/v1/rooms/{env['room']}/delegations")
    missing = await client.get(f"/api/v1/rooms/{uid()}/delegations")
    assert real.status_code == missing.status_code == 403


async def test_ordinary_room_returns_empty_list_for_member(env, client):
    env["identity_for"](env["member"])
    r = await client.get(f"/api/v1/rooms/{env['other']}/delegations")
    assert r.status_code == 200
    assert r.json() == []


async def test_guest_cannot_read_federated_delegation_state(env, client):
    # Channel membership authority is the federated roster; a local guest
    # binding must not bypass it (P2 fix, #34 B-6 parity). Both the bound and
    # the cross-room guest get the outsider-indistinguishable 403.
    env["identity_for"](env["guest"], guest_of=env["room"])
    bound = await client.get(f"/api/v1/rooms/{env['room']}/delegations")
    assert bound.status_code == 403
    env["identity_for"](env["outsider"])
    outsider = await client.get(f"/api/v1/rooms/{env['room']}/delegations")
    assert bound.json() == outsider.json()
