"""``/clear`` — a room admin starts agents' engine sessions over (#986)."""

from __future__ import annotations

import secrets
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentRoomMemory,
    Base,
    Machine,
    Message,
    Participant,
    Project,
    Room,
    User,
)


class _RecordingBus:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, machine_id: str, frame: dict[str, Any]) -> bool:
        self.sent.append((machine_id, frame))
        return True


@pytest_asyncio.fixture()
async def env():
    from cryptography.fernet import Fernet

    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
    )
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        owner = User(email="owner@example.com", password_hash="x", display_name="Owner")
        member = User(email="member@example.com", password_hash="x")
        db.add_all([owner, member])
        await db.flush()
        project = Project(name="p")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="room")
        temp = Room(project_id=project.id, name="temp", ephemeral=True)
        empty = Room(project_id=project.id, name="empty")
        db.add_all([room, temp, empty])
        await db.flush()
        db.add(Machine(id="m1", name="m1", hostname="localhost", owner_user_id=owner.id))
        await db.flush()
        pm = Agent(name="pm", engine="codex", placed_on_machine_id="m1", generation=2)
        dev = Agent(name="dev", engine="codex", placed_on_machine_id="m1", generation=2)
        db.add_all([pm, dev])
        await db.flush()
        owner_p = Participant(room_id=room.id, user_id=owner.id, role="owner")
        member_p = Participant(room_id=room.id, user_id=member.id, role="member")
        pm_p = Participant(room_id=room.id, agent_id=pm.id, role="member")
        dev_p = Participant(room_id=room.id, agent_id=dev.id, role="member")
        db.add_all([owner_p, member_p, pm_p, dev_p])
        for other in (temp, empty):
            db.add(Participant(room_id=other.id, user_id=owner.id, role="owner"))
        db.add(Participant(room_id=temp.id, agent_id=pm.id, role="member"))
        db.add(AgentRoomMemory(agent_id=pm.id, room_id=room.id, memory_md="keep me", revision=4))
        await db.commit()
        ids = {
            "room": room.id, "temp": temp.id, "empty": empty.id, "pm": pm.id, "dev": dev.id,
            "pm_p": pm_p.id, "owner_p": owner_p.id, "member_p": member_p.id,
        }
        owner_token = create_user_token(owner.id, owner.email, False, secret=config.jwt_secret)
        member_token = create_user_token(member.id, member.email, False, secret=config.jwt_secret)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = session_factory
    bus = _RecordingBus()
    app.state.machine_bus = bus  # type: ignore[assignment]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client, "session_factory": session_factory, "bus": bus,
            "owner": {"Authorization": f"Bearer {owner_token}"},
            "member": {"Authorization": f"Bearer {member_token}"},
            **ids,
        }
    await engine.dispose()


def _url(env: dict, room: str = "room") -> str:
    return f"/api/v1/rooms/{env[room]}/session-reset"


async def _memory(env: dict, agent: str) -> AgentRoomMemory | None:
    async with env["session_factory"]() as db:
        return await db.get(AgentRoomMemory, (env[agent], env["room"]))


@pytest.mark.req("COL-13")
async def test_clear_rotates_every_agents_session_and_keeps_memory(env):
    resp = await env["client"].post(_url(env), json={}, headers=env["owner"])
    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["agent_ids"]) == sorted([env["pm"], env["dev"]])

    async with env["session_factory"]() as db:
        notice = await db.get(Message, resp.json()["message_id"])
        assert notice.participant_id is None
        meta = notice.extra_metadata["session_reset"]
        assert notice.extra_metadata["system_origin"] == "session_reset"
        assert meta["actor_participant_id"] == env["owner_p"]
        assert [a["name"] for a in meta["agents"]] == ["dev", "pm"]

    pm = await _memory(env, "pm")
    assert (pm.memory_md, pm.revision, pm.session_epoch) == ("keep me", 5, 1)
    assert pm.session_reset_seq == notice.seq
    dev = await _memory(env, "dev")
    assert (dev.memory_md, dev.revision, dev.session_epoch) == ("", 1, 1)
    assert dev.session_reset_seq == notice.seq

    # The placed daemon gets the new epoch for each agent.
    snapshots = [f for _, f in env["bus"].sent if f["type"] == "agent_room_memory_snapshot"]
    assert sorted((f["agent_id"], f["room_memory"]["session_epoch"]) for f in snapshots) == sorted(
        [(env["pm"], 1), (env["dev"], 1)]
    )


@pytest.mark.req("COL-13")
async def test_clear_one_agent_leaves_the_others(env):
    resp = await env["client"].post(
        _url(env), json={"agent_participant_id": env["pm_p"]}, headers=env["owner"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_ids"] == [env["pm"]]
    assert (await _memory(env, "pm")).session_epoch == 1
    assert await _memory(env, "dev") is None


@pytest.mark.req("COL-13")
async def test_a_plain_member_cannot_clear(env):
    resp = await env["client"].post(_url(env), json={}, headers=env["member"])
    assert resp.status_code == 403, resp.text
    assert (await _memory(env, "pm")).session_epoch == 0
    async with env["session_factory"]() as db:
        assert (await db.execute(select(Message))).first() is None


@pytest.mark.req("COL-13")
@pytest.mark.parametrize(
    ("room", "body_key", "status", "code"),
    [
        ("room", "member_p", 400, "SESSION_RESET_NOT_AN_AGENT"),
        ("empty", None, 400, "SESSION_RESET_NO_AGENTS"),
        ("temp", None, 400, "SESSION_RESET_EPHEMERAL"),
    ],
)
async def test_clear_refuses_what_has_no_session(env, room, body_key, status, code):
    body = {"agent_participant_id": env[body_key]} if body_key else {}
    resp = await env["client"].post(_url(env, room), json=body, headers=env["owner"])
    assert resp.status_code == status, resp.text
    assert resp.json()["detail"]["code"] == code
