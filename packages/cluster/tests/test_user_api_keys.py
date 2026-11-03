"""Personal API keys for calling the API from outside the browser (#988).

AUTH-11 — a key authenticates REST and WS as the person who issued it.
AUTH-12 — a key is held to its scope: ``read`` cannot write, only ``admin``
carries admin rights, and revoking the person's admin role takes effect
immediately.
AUTH-13 — a revoked or expired key is refused, and the plaintext appears only
in the creation response.
AUTH-14 — keys cannot manage keys, and people cannot touch each other's keys.
AUTH-15 — repeated wrong keys from one client are refused with 429.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from anygarden.app import create_app
from anygarden.auth.api_key import (
    generate_api_key,
    hash_api_key,
    scope_allows,
    verify_api_key_hash,
)
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Participant, Project, Room, User, UserApiKey
from anygarden.dependencies import API_KEY_FAILURE_LIMIT

KEYS = "/api/v1/auth/api-keys"


@pytest_asyncio.fixture()
async def env(config: AnygardenSettings, tmp_path) -> AsyncIterator[dict]:
    # File-backed DB so the WS tests' cancelled handler tasks cannot reset an
    # in-memory StaticPool connection (#726).
    config.db_url = f"sqlite+aiosqlite:///{tmp_path / f'keys-{uuid.uuid4().hex}.db'}"
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sessions() as db:
        member = User(email="member@anygarden.io", password_hash="x")
        admin = User(email="admin@anygarden.io", password_hash="x", is_admin=True)
        project = Project(name="proj")
        db.add_all([member, admin, project])
        await db.flush()
        room = Room(project_id=project.id, name="main")
        db.add(room)
        await db.flush()
        db.add(Participant(room_id=room.id, user_id=member.id, role="member"))
        await db.commit()
        for obj in (member, admin, room):
            await db.refresh(obj)

    app = create_app(config)
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = sessions
    secret = config.jwt_secret
    yield {
        "app": app,
        "sessions": sessions,
        "room": room,
        "member": member,
        "admin": admin,
        "member_token": create_user_token(member.id, member.email, False, secret=secret),
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


async def _issue(client: AsyncClient, session_token: str, scope: str, **extra) -> dict:
    resp = await client.post(
        KEYS, json={"name": f"{scope} key", "scope": scope, **extra}, headers=_auth(session_token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# -- token helpers -------------------------------------------------------------


def test_generated_key_has_prefix_and_verifies():
    key = generate_api_key()
    assert key.startswith("agu_")
    hashed, hint = hash_api_key(key)
    assert hint == key[:12]
    assert verify_api_key_hash(key, hashed)
    assert not verify_api_key_hash(generate_api_key(), hashed)


def test_scopes_are_cumulative():
    assert scope_allows("admin", "write") and scope_allows("write", "read")
    assert not scope_allows("read", "write")
    assert not scope_allows("write", "admin")
    assert not scope_allows("", "read")


# -- AUTH-11: a key acts as its person ------------------------------------------


@pytest.mark.req("AUTH-11")
async def test_key_authenticates_rest_as_its_person(env, client):
    key = (await _issue(client, env["member_token"], "write"))["key"]

    me = await client.get("/api/v1/auth/me", headers=_auth(key))
    assert me.status_code == 200
    assert me.json()["email"] == "member@anygarden.io"

    messages = f"/api/v1/rooms/{env['room'].id}/messages"
    sent = await client.post(messages, json={"content": "from a script"}, headers=_auth(key))
    assert sent.status_code == 201, sent.text
    listed = await client.get(messages, headers=_auth(key))
    assert [m["content"] for m in listed.json()] == ["from a script"]


@pytest.mark.req("AUTH-11")
async def test_key_records_last_use(env, client):
    issued = await _issue(client, env["member_token"], "read")
    assert issued["last_used_at"] is None
    await client.get("/api/v1/auth/me", headers=_auth(issued["key"]))

    [listed] = (await client.get(KEYS, headers=_auth(env["member_token"]))).json()
    assert listed["last_used_at"] is not None


@pytest.mark.req("AUTH-11", "AUTH-12")
async def test_ws_key_can_watch_but_read_key_cannot_send(env, client):
    from starlette.testclient import TestClient

    read_key = (await _issue(client, env["member_token"], "read"))["key"]
    write_key = (await _issue(client, env["member_token"], "write"))["key"]
    room_id = env["room"].id

    with TestClient(env["app"]) as ws_client:
        with ws_client.websocket_connect(
            f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{read_key}"]
        ) as ws:
            assert json.loads(ws.receive_text())["type"] == "welcome"
            ws.send_text(json.dumps({"type": "send", "content": "should not land"}))
            error = json.loads(ws.receive_text())
            assert error["type"] == "error"
            assert "write scope" in error["detail"]

        with ws_client.websocket_connect(
            f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{write_key}"]
        ) as ws:
            assert json.loads(ws.receive_text())["type"] == "welcome"
            ws.send_text(json.dumps({"type": "send", "content": "hello"}))
            data = json.loads(ws.receive_text())
            assert data["type"] == "message"
            assert data["content"] == "hello"


# -- AUTH-12: scope ---------------------------------------------------------------


@pytest.mark.req("AUTH-12")
async def test_read_key_cannot_call_write_methods(env, client):
    key = (await _issue(client, env["member_token"], "read"))["key"]
    messages = f"/api/v1/rooms/{env['room'].id}/messages"

    assert (await client.get(messages, headers=_auth(key))).status_code == 200
    denied = await client.post(messages, json={"content": "nope"}, headers=_auth(key))
    assert denied.status_code == 403
    assert "write scope" in denied.json()["detail"]


@pytest.mark.req("AUTH-12")
async def test_only_admin_key_carries_admin_rights(env, client):
    write_key = (await _issue(client, env["admin_token"], "write"))["key"]
    admin_key = (await _issue(client, env["admin_token"], "admin"))["key"]

    assert (await client.get("/api/v1/agents", headers=_auth(write_key))).status_code == 403
    assert (await client.get("/api/v1/agents", headers=_auth(admin_key))).status_code == 200
    me = await client.get("/api/v1/auth/me", headers=_auth(write_key))
    assert me.json()["is_admin"] is False


@pytest.mark.req("AUTH-12")
async def test_admin_key_stops_working_when_admin_role_is_revoked(env, client):
    admin_key = (await _issue(client, env["admin_token"], "admin"))["key"]
    assert (await client.get("/api/v1/agents", headers=_auth(admin_key))).status_code == 200

    async with env["sessions"]() as db:
        await db.execute(update(User).where(User.id == env["admin"].id).values(is_admin=False))
        await db.commit()

    assert (await client.get("/api/v1/agents", headers=_auth(admin_key))).status_code == 403


@pytest.mark.req("AUTH-12")
async def test_non_admin_cannot_issue_admin_key(env, client):
    resp = await client.post(
        KEYS, json={"name": "x", "scope": "admin"}, headers=_auth(env["member_token"])
    )
    assert resp.status_code == 403


# -- AUTH-13: revocation, expiry, plaintext exposure --------------------------------


@pytest.mark.req("AUTH-13")
async def test_revoked_key_is_refused(env, client):
    issued = await _issue(client, env["member_token"], "read")
    assert (await client.get("/api/v1/auth/me", headers=_auth(issued["key"]))).status_code == 200

    revoke = await client.delete(f"{KEYS}/{issued['id']}", headers=_auth(env["member_token"]))
    assert revoke.status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=_auth(issued["key"]))).status_code == 401
    assert (await client.get(KEYS, headers=_auth(env["member_token"]))).json() == []


@pytest.mark.req("AUTH-13")
async def test_expired_key_is_refused(env, client):
    issued = await _issue(client, env["member_token"], "read", expires_in_days=1)
    assert issued["expires_at"] is not None
    async with env["sessions"]() as db:
        await db.execute(
            update(UserApiKey)
            .where(UserApiKey.id == issued["id"])
            .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )
        await db.commit()

    assert (await client.get("/api/v1/auth/me", headers=_auth(issued["key"]))).status_code == 401


@pytest.mark.req("AUTH-13")
async def test_plaintext_only_in_creation_response(env, client):
    issued = await _issue(client, env["member_token"], "write")
    assert issued["key"].startswith("agu_")
    assert issued["prefix"] == issued["key"][:12]

    [listed] = (await client.get(KEYS, headers=_auth(env["member_token"]))).json()
    assert "key" not in listed and "token_hash" not in listed
    assert issued["key"] not in json.dumps(listed)


# -- AUTH-14: who may manage keys --------------------------------------------------


@pytest.mark.req("AUTH-14")
async def test_key_cannot_manage_keys(env, client):
    issued = await _issue(client, env["admin_token"], "admin")
    key = issued["key"]

    assert (await client.get(KEYS, headers=_auth(key))).status_code == 403
    created = await client.post(KEYS, json={"name": "x", "scope": "read"}, headers=_auth(key))
    assert created.status_code == 403
    assert (await client.delete(f"{KEYS}/{issued['id']}", headers=_auth(key))).status_code == 403


@pytest.mark.req("AUTH-14")
async def test_cannot_revoke_someone_elses_key(env, client):
    issued = await _issue(client, env["admin_token"], "read")
    resp = await client.delete(f"{KEYS}/{issued['id']}", headers=_auth(env["member_token"]))
    assert resp.status_code == 404
    assert (await client.get("/api/v1/auth/me", headers=_auth(issued["key"]))).status_code == 200


@pytest.mark.req("AUTH-14")
async def test_active_key_count_is_capped(env, client, monkeypatch):
    from anygarden.auth import api_key_routes

    monkeypatch.setattr(api_key_routes, "MAX_ACTIVE_API_KEYS", 2)
    await _issue(client, env["member_token"], "read")
    await _issue(client, env["member_token"], "read")
    resp = await client.post(
        KEYS, json={"name": "third", "scope": "read"}, headers=_auth(env["member_token"])
    )
    assert resp.status_code == 409


# -- AUTH-15: wrong keys are rate limited --------------------------------------------


@pytest.mark.req("AUTH-15")
async def test_repeated_wrong_keys_are_limited(env, client):
    wrong = _auth(generate_api_key())
    for _ in range(API_KEY_FAILURE_LIMIT):
        assert (await client.get("/api/v1/auth/me", headers=wrong)).status_code == 401
    limited = await client.get("/api/v1/auth/me", headers=wrong)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
