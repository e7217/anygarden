"""The external-control guide's example works against a real app (#989).

``examples/external_control.py`` is what ``docs/external-control.md`` tells
people to run, so this test drives its functions end to end: issue a
personal API key, list rooms, send a message, and receive an agent's reply
over REST polling and the WebSocket.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, AgentToken, Base, Participant, Project, Room, User

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "external_control.py"


def _load_example():
    spec = importlib.util.spec_from_file_location("external_control_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture()
async def env(config: AnygardenSettings, tmp_path) -> AsyncIterator[dict]:
    config.db_url = f"sqlite+aiosqlite:///{tmp_path / f'ext-{uuid.uuid4().hex}.db'}"
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
        "session_token": create_user_token(
            person.id, person.email, False, secret=config.jwt_secret
        ),
    }
    await engine.dispose()


@pytest.mark.req("AUTH-11")
async def test_guide_example_sends_and_receives_a_reply(env):
    from starlette.testclient import TestClient

    example = _load_example()
    room_id = env["room"].id
    with TestClient(env["app"]) as http:
        issued = http.post(
            "/api/v1/auth/api-keys",
            json={"name": "guide", "scope": "write"},
            headers={"Authorization": f"Bearer {env['session_token']}"},
        )
        assert issued.status_code == 201
        http.headers["Authorization"] = f"Bearer {issued.json()['key']}"

        assert [r["id"] for r in example.list_rooms(http)] == [room_id]
        assert list(example.agent_participants(http, room_id)) == [env["agent_participant"].id]

        with http.websocket_connect(
            f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{issued.json()['key']}"]
        ) as ws:
            assert json.loads(ws.receive_text())["type"] == "welcome"

            sent = example.send_message(http, room_id, "What changed today?")
            # Nothing has answered yet: the poll times out instead of
            # returning the person's own message.
            assert example.wait_for_reply(http, room_id, sent["seq"], timeout=0) is None

            reply = http.post(
                f"/api/v1/rooms/{room_id}/messages",
                json={"content": "Two PRs merged."},
                headers={"Authorization": f"Bearer {env['agent_token']}"},
            )
            assert reply.status_code == 201

            got = example.wait_for_reply(http, room_id, sent["seq"], timeout=0)
            assert got is not None and got["content"] == "Two PRs merged."

            frames = []
            while len([f for f in frames if f["type"] == "message"]) < 2:
                frames.append(json.loads(ws.receive_text()))
            contents = [f["content"] for f in frames if f["type"] == "message"]
            assert contents == ["What changed today?", "Two PRs merged."]


def test_example_refuses_to_run_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("ANYGARDEN_API_KEY", raising=False)
    assert _load_example().main(["rooms"]) == 2
    assert "ANYGARDEN_API_KEY" in capsys.readouterr().err


def test_openapi_schema_describes_the_external_api(config: AnygardenSettings):
    from anygarden import __version__

    schema = create_app(config).openapi()
    assert schema["info"]["version"] == __version__
    for path in ("/api/v1/auth/api-keys", "/api/v1/rooms", "/api/v1/rooms/{room_id}/messages"):
        assert path in schema["paths"]
