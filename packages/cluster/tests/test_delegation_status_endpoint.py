"""Read-only delegation status endpoint (#593 support, task #35).

Room-scoped ``DelegationMirror`` reads for UI polling. Authorization goes
through the ordinary rooms path with the explicit shared-room opt-in, so
membership/archive/role fences stay intact; ordinary rooms keep rejecting
shared-channel traffic on every other endpoint.

The app is mounted through the **product** ``create_app`` path: the endpoint
must answer at the real ``/api/v1/rooms/{id}/delegations`` URL, not only on a
standalone router mount (the double-prefix regression from final acceptance
#43).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_guest_token, create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Participant, Room, User
from anygarden.federation.delegation_models import DelegationMirror
from anygarden.shared_channels.models import ChannelStream

AUTHORITY, CHANNEL = "node-authority", "channel-1"


def uid() -> str:
    return str(uuid4())


@pytest_asyncio.fixture()
async def env(tmp_path: Path):
    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'delegations.db'}",
        jwt_secret=secrets.token_urlsafe(32),
    )
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    room, other = uid(), uid()
    async with sessions.begin() as db:
        member = User(email="member@example.test", password_hash="x")
        outsider = User(email="nobody@example.test", password_hash="x")
        db.add_all([member, outsider])
        await db.flush()
        db.add(Room(id=room, name="mirror"))
        db.add(Room(id=other, name="ordinary"))
        db.add(Participant(room_id=room, user_id=member.id, role="member"))
        db.add(Participant(room_id=other, user_id=member.id, role="member"))
        db.add(
            ChannelStream(
                authority_node_id=AUTHORITY,
                channel_id=CHANNEL,
                local_room_id=room,
                last_seq=0,
                applied_seq=0,
            )
        )
        rows = {
            "accepted": DelegationMirror(
                authority_node_id=AUTHORITY,
                channel_id=CHANNEL,
                delegation_id=uid(),
                task_id=uid(),
                source_message_id=uid(),
                requester={"node_id": AUTHORITY, "kind": "agent", "principal_id": uid()},
                executor={"node_id": "node-executor", "agent_id": uid()},
                execution_id=uid(),
                revision=2,
                state="accepted",
                process_state="unknown",
                task_status="in_progress",
            ),
            "cancel_requested": DelegationMirror(
                authority_node_id=AUTHORITY,
                channel_id=CHANNEL,
                delegation_id=uid(),
                task_id=uid(),
                source_message_id=uid(),
                requester={"node_id": AUTHORITY, "kind": "agent", "principal_id": uid()},
                executor={"node_id": "node-executor", "agent_id": uid()},
                execution_id=uid(),
                revision=4,
                state="cancel_requested",
                process_state="running",
                task_status="blocked",
            ),
            "unknown": DelegationMirror(
                authority_node_id=AUTHORITY,
                channel_id=CHANNEL,
                delegation_id=uid(),
                task_id=uid(),
                source_message_id=uid(),
                requester={"node_id": AUTHORITY, "kind": "agent", "principal_id": uid()},
                executor={"node_id": "node-executor", "agent_id": uid()},
                execution_id=uid(),
                revision=3,
                state="unknown",
                process_state="unknown",
                task_status="blocked",
            ),
        }
        db.add_all(rows.values())

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions
    yield {
        "app": app,
        "sessions": sessions,
        "room": room,
        "other": other,
        "member": member,
        "outsider": outsider,
        "rows": rows,
    }
    await engine.dispose()


@pytest_asyncio.fixture()
async def client(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def auth(client: AsyncClient, env, who) -> None:
    token = create_user_token(
        who.id, who.email, False, secret=env["app"].state.config.jwt_secret
    )
    client.headers["Authorization"] = f"Bearer {token}"


def guest_auth(client: AsyncClient, env, *, room_id: str) -> None:
    token = create_guest_token(
        user_id=env["member"].id,
        room_id=room_id,
        invite_id=uid(),
        display_name="guest",
        secret=env["app"].state.config.jwt_secret,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    client.headers["Authorization"] = f"Bearer {token}"


async def test_product_app_mounts_endpoint_at_documented_path(env, client):
    # Final-acceptance #43 regression: the rooms-router include chain must
    # not double the prefix. The real URL answers 200; the doubled URL must
    # never answer delegation JSON. The SPA catch-all also returns a bare 404
    # for unknown API paths when static UI files are present.
    auth(client, env, env["member"])
    r = await client.get(f"/api/v1/rooms/{env['room']}/delegations")
    assert r.status_code == 200
    assert {row["state"] for row in r.json()} == {
        "accepted", "cancel_requested", "unknown",
    }
    doubled = await client.get(f"/api/v1/rooms/api/v1/rooms/{env['room']}/delegations")
    assert doubled.status_code == 404
    content_type = doubled.headers.get("content-type", "")
    if "application/json" in content_type:
        # Without UI serving, FastAPI returns a JSON 404 detail object.
        assert not isinstance(doubled.json(), list)
    sample = next(row for row in r.json() if row["state"] == "accepted")
    stored = env["rows"]["accepted"]
    assert sample["delegation_id"] == stored.delegation_id
    assert sample["task_status"] == "in_progress"
    assert sample["executor"] == stored.executor


async def test_outsider_and_unknown_room_are_indistinguishable_403(env, client):
    auth(client, env, env["outsider"])
    real = await client.get(f"/api/v1/rooms/{env['room']}/delegations")
    missing = await client.get(f"/api/v1/rooms/{uid()}/delegations")
    assert real.status_code == missing.status_code == 403
    assert real.json() == missing.json()


async def test_ordinary_room_returns_empty_list_for_member(env, client):
    auth(client, env, env["member"])
    r = await client.get(f"/api/v1/rooms/{env['other']}/delegations")
    assert r.status_code == 200
    assert r.json() == []


async def test_guest_cannot_read_federated_delegation_state(env, client):
    # Channel membership authority is the federated roster; a local guest
    # binding must not bypass it (P2 fix, #34 B-6 parity). The guest 403 body
    # equals the outsider 403 body.
    guest_auth(client, env, room_id=env["room"])
    bound = await client.get(f"/api/v1/rooms/{env['room']}/delegations")
    assert bound.status_code == 403
    auth(client, env, env["outsider"])
    outsider = await client.get(f"/api/v1/rooms/{env['room']}/delegations")
    assert bound.json() == outsider.json()
