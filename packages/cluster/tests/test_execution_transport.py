"""Agent control correlation, real WS authentication and local runtime delivery."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.db.models import Agent, AgentToken, Participant, Room
from anygarden.federation.execution_transport import (
    ExecutionTransport,
    ExecutionTransportError,
)
from anygarden.orchestration.rules import CooldownManager, TypingTracker
from anygarden.ws.handler import ws_room
from anygarden.ws.manager import ConnectionManager
from anygarden.ws.protocol import ExecutionControlResultFrame
from anygarden_agent.client import ChatClient
from anygarden_agent.runtime.execution.launch import ExecutionLaunch
from starlette.websockets import WebSocket

from .test_agents_api import agents_env as _agents_env

control_env = _agents_env


async def seed(env):
    async with env["factory"]() as db:
        agent = Agent(
            name="control",
            engine="codex-cli",
            generation=7,
            placed_on_machine_id=env["machine"].id,
        )
        db.add(agent)
        await db.flush()
        room = Room(name="DM", is_dm=True, representative_agent_id=agent.id)
        db.add(room)
        await db.flush()
        participant = Participant(room_id=room.id, agent_id=agent.id, role="member")
        token = generate_token()
        hashed, hint = hash_agent_token(token)
        db.add_all(
            [
                participant,
                AgentToken(agent_id=agent.id, token_hash=hashed, lookup_hint=hint),
            ]
        )
        await db.commit()
        return agent.id, room.id, participant.id, token


class Socket:
    def __init__(self):
        self.frames = asyncio.Queue()

    async def send_text(self, value):
        await self.frames.put(json.loads(value))

    async def close(self, **kwargs):
        pass


def response(frame, **kwargs):
    return ExecutionControlResultFrame(
        request_id=frame["request_id"],
        action=frame["action"],
        generation=frame["generation"],
        result={"proof": "actual reply"},
        **kwargs,
    )


async def test_identity_generation_action_and_socket_fences(control_env):
    agent, room, participant, _ = await seed(control_env)
    manager, ws = ConnectionManager(), Socket()
    broker = ExecutionTransport(control_env["factory"], manager)
    await manager.subscribe(room, participant, ws, generation=7, execution_control=True)
    task = asyncio.create_task(broker.request(agent, "reconcile", {}))
    frame = await ws.frames.get()
    args = {
        "agent_id": agent,
        "participant_id": participant,
        "room_id": room,
        "websocket": ws,
        "frame": response(frame),
    }
    for override in (
        {"agent_id": "other"},
        {"participant_id": "other"},
        {"room_id": "other"},
        {"websocket": Socket()},
        {"frame": response({**frame, "generation": 8})},
        {"frame": response({**frame, "action": "cancel"})},
    ):
        assert not await broker.resolve(**{**args, **override})
    assert not task.done()
    assert await broker.resolve(**args)
    assert await task == {"proof": "actual reply"}
    assert not await broker.resolve(**args)
    await broker.close()


async def test_old_socket_cannot_remove_new_subscription_or_complete_request(
    control_env,
):
    agent, room, participant, _ = await seed(control_env)
    manager, old, new = ConnectionManager(), Socket(), Socket()
    broker = ExecutionTransport(control_env["factory"], manager)
    await manager.subscribe(
        room, participant, old, generation=7, execution_control=True
    )
    first = asyncio.create_task(broker.request(agent, "reconcile", {}))
    await old.frames.get()
    await manager.subscribe(
        room, participant, new, generation=7, execution_control=True
    )
    with pytest.raises(ExecutionTransportError, match="CONTROL_DISCONNECTED"):
        await first
    await manager.unsubscribe(participant, websocket=old)
    assert await manager.is_connected(participant)
    second = asyncio.create_task(broker.request(agent, "reconcile", {}))
    frame = await new.frames.get()
    assert not await broker.resolve(
        agent_id=agent,
        participant_id=participant,
        room_id=room,
        websocket=old,
        frame=response(frame),
    )
    await manager.unsubscribe(participant, websocket=new)
    with pytest.raises(ExecutionTransportError, match="CONTROL_DISCONNECTED"):
        await second
    await broker.close()


async def test_legacy_timeout_capacity_and_generation_change_fail_closed(control_env):
    agent, room, participant, _ = await seed(control_env)
    manager, ws = ConnectionManager(), Socket()
    broker = ExecutionTransport(
        control_env["factory"], manager, timeout_seconds=0.1, pending_limit=1
    )
    await manager.subscribe(room, participant, ws, generation=7)
    with pytest.raises(ExecutionTransportError, match="EXECUTION_UNAVAILABLE"):
        await broker.request(agent, "prepare", {})
    await manager.subscribe(room, participant, ws, generation=7, execution_control=True)
    with pytest.raises(ExecutionTransportError, match="GENERATION_CHANGED"):
        await broker.request(agent, "prepare", {}, expected_generation=6)
    first = asyncio.create_task(broker.request(agent, "reconcile", {}))
    await ws.frames.get()
    with pytest.raises(ExecutionTransportError, match="EXECUTION_QUEUE_FULL"):
        await broker.request(agent, "reconcile", {})
    with pytest.raises(ExecutionTransportError, match="EXECUTION_TIMEOUT"):
        await first
    assert not broker._pending
    second = asyncio.create_task(broker.request(agent, "reconcile", {}))
    frame = await ws.frames.get()
    async with control_env["factory"]() as db:
        row = await db.get(Agent, agent)
        row.generation = 8
        await db.commit()
    await broker.resolve(
        agent_id=agent,
        participant_id=participant,
        room_id=room,
        websocket=ws,
        frame=response(frame),
    )
    with pytest.raises(ExecutionTransportError, match="GENERATION_CHANGED"):
        await second
    await broker.close()


async def test_real_authenticated_ws_agent_client_strict_runtime_round_trip(
    control_env, tmp_path, monkeypatch
):
    agent_id, room, participant, token = await seed(control_env)
    env = control_env
    from anygarden.shared_channels.models import ChannelStream

    async with env["factory"]() as db:
        shared = Room(name="shared-mirror")
        db.add(shared)
        await db.flush()
        db.add_all(
            [
                Participant(room_id=shared.id, agent_id=agent_id),
                ChannelStream(
                    authority_node_id="authority",
                    channel_id="channel",
                    local_room_id=shared.id,
                ),
            ]
        )
        await db.commit()
    app = env["app"]
    app.state.config = (
        app.state.config
        if hasattr(app.state, "config")
        else SimpleNamespace(jwt_secret="unused")
    )
    manager = ConnectionManager()
    app.state.connection_manager = manager
    app.state.cooldown_manager = CooldownManager()
    app.state.typing_tracker = TypingTracker()
    broker = ExecutionTransport(env["factory"], manager)
    root = tmp_path / "agent"
    root.mkdir()
    (root / "workspace").mkdir()
    auth = tmp_path / "codex-home"
    auth.mkdir()
    (auth / "auth.json").write_text('{"tokens":{"access_token":"isolated-fixture"}}')
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, sys
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.154.0")
    sys.exit(0)
text = sys.stdin.read()
Path("ran").write_text(text)
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"actual process answer"}}))
print(json.dumps({"type":"turn.completed"}))
Path(sys.argv[sys.argv.index("-o")+1]).write_text("actual process answer")
"""
    )
    executable.chmod(0o700)
    monkeypatch.setenv("ANYGARDEN_AGENT_GENERATION", "7")
    client = ChatClient("http://fixture", token, state_dir=root)
    client.execution_launch_ready = True
    client._execution_adapter = SimpleNamespace(
        _root=root,
        _engine="codex-cli",
        _permission_level="restricted",
        _codex_path=str(executable),
        _runtime_version="0.154.0",
        _environment={"CODEX_HOME": str(auth)},
        _launch=ExecutionLaunch("codex-cli", None, "local", 7, None),
        _system_prompt="",
        _reasoning_effort=None,
        _turn_timeout=5,
    )
    incoming = asyncio.Queue()
    await incoming.put({"type": "websocket.connect"})

    class AgentSocket:
        async def send(self, value):
            await incoming.put({"type": "websocket.receive", "text": value})

    client._connections[room] = AgentSocket()

    async def send(message):
        if message["type"] == "websocket.send":
            frame = json.loads(message["text"])
            if frame["type"] == "welcome":
                assert shared.id not in frame.get("pending_rooms", [])
            await client._process_frame(room, frame)

    websocket = WebSocket(
        {
            "type": "websocket",
            "app": app,
            "path": f"/ws/rooms/{room}",
            "headers": [
                (b"sec-websocket-protocol", f"anygarden.v1, bearer.{token}".encode())
            ],
            "query_string": b"ready=1&generation=7&execution_control=1",
        },
        incoming.get,
        send,
    )
    handler = asyncio.create_task(ws_room(websocket, room))
    try:
        async with asyncio.timeout(5):
            while not await manager.is_connected(participant):
                if handler.done():
                    await handler
                await asyncio.sleep(0.01)
        descriptor = await broker.request(
            agent_id,
            "prepare",
            {
                "execution_id": "execution",
                "execution_node_id": "local",
                "authority_node_id": "remote",
                "channel_id": "shared-channel",
                "prompt": "actual request",
                "policy_epoch": 1,
                "grant_epoch": 1,
                "peer_epoch": 1,
            },
            expected_generation=7,
        )
        recovered = await broker.request(
            agent_id,
            "describe",
            {
                "execution_id": descriptor["execution_id"],
                **{
                    key: descriptor["scope"][key]
                    for key in (
                        "execution_node_id",
                        "authority_node_id",
                        "channel_id",
                    )
                },
            },
        )
        assert recovered == descriptor
        assert not (root / "workspace" / "ran").exists()
        ref = {key: descriptor[key] for key in ("execution_id", "fingerprint")}
        await broker.request(agent_id, "start", ref, expected_generation=7)
        async with asyncio.timeout(5):
            while True:
                receipt = await broker.request(agent_id, "reconcile", ref)
                if receipt["outcome"] is not None:
                    break
                await asyncio.sleep(0.02)
        assert receipt["text"] == "actual process answer"
        assert (root / "workspace" / "ran").read_text() == "actual request"
        assert str(root) not in json.dumps(descriptor)
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await handler
        if client._execution_control:
            await client._execution_control.close()
        await broker.close()
