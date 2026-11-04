"""People-facing MCP endpoint ``/mcp/user`` (#990).

AUTH-16 — ``/mcp/user`` accepts only a personal API key, and its tools act
with exactly the REST permissions of that key: a read key cannot send, an
agent token or session is refused, and a personal key is refused on the
agent channel ``/mcp/rpc``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, AgentToken, Base, Participant, Project, Room, User
from anygarden.mcp import user_tools


@pytest_asyncio.fixture()
async def env(config: AnygardenSettings, tmp_path) -> AsyncIterator[dict]:
    config.db_url = f"sqlite+aiosqlite:///{tmp_path / f'mcp-user-{uuid.uuid4().hex}.db'}"
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    agent_token = generate_token()
    async with sessions() as db:
        person = User(email="person@anygarden.io", password_hash="x")
        project = Project(name="proj")
        agent = Agent(name="helper", engine="codex", actual_state="running")
        db.add_all([person, project, agent])
        await db.flush()
        room = Room(project_id=project.id, name="main")
        db.add(room)
        await db.flush()
        db.add(Participant(room_id=room.id, user_id=person.id, role="owner"))
        agent_participant = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add(agent_participant)
        token_hash, hint = hash_agent_token(agent_token)
        db.add(AgentToken(agent_id=agent.id, token_hash=token_hash, lookup_hint=hint))
        await db.commit()
        for obj in (person, room, agent_participant):
            await db.refresh(obj)

    app = create_app(config)
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = sessions
    yield {
        "app": app,
        "room": room,
        "agent_participant": agent_participant,
        "agent_token": agent_token,
        "session_token": create_user_token(person.id, person.email, False, secret=config.jwt_secret),
    }
    await engine.dispose()


@pytest_asyncio.fixture()
async def client(env) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _key(client: AsyncClient, env: dict, scope: str) -> str:
    resp = await client.post(
        "/api/v1/auth/api-keys",
        json={"name": scope, "scope": scope},
        headers=_auth(env["session_token"]),
    )
    assert resp.status_code == 201
    return resp.json()["key"]


async def _rpc(client: AsyncClient, token: str, method: str, params: dict | None = None, path="/mcp/user"):
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return await client.post(path, json=body, headers=_auth(token))


async def _tool(client: AsyncClient, token: str, name: str, **arguments) -> dict:
    resp = await _rpc(client, token, "tools/call", {"name": name, "arguments": arguments})
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


# -- handshake and identity ---------------------------------------------------


@pytest.mark.req("AUTH-16")
async def test_handshake_lists_the_people_tools(env, client):
    key = await _key(client, env, "read")
    init = await _rpc(client, key, "initialize", {})
    assert init.json()["result"]["serverInfo"]["name"] == "anygarden"
    assert "wait_for_reply" in init.json()["result"]["instructions"]

    tools = (await _rpc(client, key, "tools/list")).json()["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "list_rooms", "get_room", "read_messages", "send_message", "wait_for_reply", "list_tasks",
    }


async def test_notifications_are_acknowledged_without_a_body(client):
    resp = await client.post("/mcp/user", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp.status_code == 202
    assert resp.content == b""


@pytest.mark.req("AUTH-16")
async def test_only_personal_keys_are_accepted(env, client):
    assert (await _rpc(client, env["agent_token"], "tools/list")).status_code == 403
    assert (await _rpc(client, env["session_token"], "tools/list")).status_code == 403
    assert (await _rpc(client, "agu_" + "x" * 43, "tools/list")).status_code == 401

    key = await _key(client, env, "write")
    assert (await _rpc(client, key, "tools/list", path="/mcp/rpc")).status_code == 403


@pytest.mark.req("AUTH-16")
async def test_revoked_key_is_refused(env, client):
    key = await _key(client, env, "read")
    [listed] = (await client.get("/api/v1/auth/api-keys", headers=_auth(env["session_token"]))).json()
    await client.delete(f"/api/v1/auth/api-keys/{listed['id']}", headers=_auth(env["session_token"]))
    assert (await _rpc(client, key, "tools/list")).status_code == 401


# -- tools ----------------------------------------------------------------------


async def test_read_tools(env, client):
    key = await _key(client, env, "read")
    room_id = env["room"].id

    rooms = await _tool(client, key, "list_rooms")
    assert [r["id"] for r in rooms["structuredContent"]["rooms"]] == [room_id]

    room = (await _tool(client, key, "get_room", room_id=room_id))["structuredContent"]
    agents = [p for p in room["participants"] if p["kind"] == "agent"]
    assert agents == [{"id": env["agent_participant"].id, "kind": "agent", "display_name": "helper"}]

    assert (await _tool(client, key, "read_messages", room_id=room_id))["structuredContent"] == {"messages": []}
    assert (await _tool(client, key, "list_tasks", room_id=room_id))["structuredContent"] == {"tasks": []}


@pytest.mark.req("AUTH-16")
async def test_read_key_cannot_send(env, client):
    key = await _key(client, env, "read")
    result = await _tool(client, key, "send_message", room_id=env["room"].id, content="hi")
    assert result["isError"] is True
    assert "403" in result["content"][0]["text"]
    assert "write scope" in result["content"][0]["text"]


async def test_tool_errors_name_the_refused_call(env, client):
    key = await _key(client, env, "read")
    result = await _tool(client, key, "get_room", room_id="missing")
    assert result["isError"] is True
    # The API does not reveal whether a room exists; the refusal is passed on as is.
    assert "GET /api/v1/rooms/missing failed (403)" in result["content"][0]["text"]
    unknown = await _tool(client, key, "drop_tables")
    assert unknown["isError"] is True


async def test_send_then_wait_for_an_agent_reply(env, client, monkeypatch):
    monkeypatch.setattr(user_tools, "WAIT_POLL_SECONDS", 0.05)
    key = await _key(client, env, "write")
    room_id = env["room"].id

    sent = await _tool(client, key, "send_message", room_id=room_id, content="What changed today?")
    seq = sent["structuredContent"]["message"]["seq"]

    waiting = asyncio.create_task(
        _tool(client, key, "wait_for_reply", room_id=room_id, after_seq=seq, timeout_sec=10)
    )
    await asyncio.sleep(0.2)
    assert not waiting.done(), "the person's own message must not count as a reply"
    reply = await client.post(
        f"/api/v1/rooms/{room_id}/messages",
        json={"content": "Two PRs merged."},
        headers=_auth(env["agent_token"]),
    )
    assert reply.status_code == 201

    result = (await asyncio.wait_for(waiting, 10))["structuredContent"]
    assert result["replied"] is True
    assert result["agent"] == "helper"
    assert result["message"]["content"] == "Two PRs merged."


async def test_wait_for_reply_times_out_with_status(env, client):
    key = await _key(client, env, "write")
    result = await _tool(client, key, "wait_for_reply", room_id=env["room"].id, after_seq=0, timeout_sec=0)
    assert result["structuredContent"] == {"replied": False, "agent_working": False, "last_seq": 0}
