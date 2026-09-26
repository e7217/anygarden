"""Managed disk → real daemon → authenticated/local delivery → admin API."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from anygarden.auth.machine_token import generate_machine_token, hash_machine_token
from anygarden.db.models import Agent, Machine, MachineToken
from anygarden.node.execution import LocalExecutionBackend
from anygarden.scheduler.machine_bus import MachineBus, MachineRequestError
from anygarden.ws.machine_handler import ws_machine
from anygarden_agent.runtime.workspace_receipt import report_workspace
from anygarden_machine.daemon import MachineDaemon
from anygarden_machine.managed_workspace import CAPABILITY, supported
from fastapi import WebSocketDisconnect

from .test_agents_api import agents_env as _agents_env

workspace_env = _agents_env
pytestmark = pytest.mark.skipif(not supported(), reason="POSIX descriptor boundary")


async def seed(env, tmp_path):
    machine_id = env["machine"].id
    async with env["factory"]() as db:
        machine = await db.get(Machine, machine_id)
        machine.control_capabilities = [CAPABILITY]
        agent = Agent(
            id="workspace-agent",
            name="Worker",
            engine="pi-cli",
            generation=2,
            placed_on_machine_id=machine_id,
            desired_state="stopped",
            actual_state="stopped",
        )
        db.add(agent)
        await db.commit()
    root = tmp_path / "agents" / "workspace-agent"
    root.mkdir(parents=True)
    (root / "folder").mkdir()
    (root / "folder" / "result.md").write_text(
        "actual runtime output", encoding="utf-8"
    )
    (root / ".env").write_text("PRIVATE")
    report_workspace(
        root, root, generation=2, engine="pi-cli", permission_level="standard"
    )
    return machine_id, root


@pytest.mark.parametrize("delivery", ["remote", "integrated"])
async def test_real_disk_round_trip_and_persistence(workspace_env, tmp_path, delivery):
    env = workspace_env
    machine_id, root = await seed(env, tmp_path)
    bus = env["bus"]
    await bus.unregister(machine_id)
    headers = {"Authorization": f"Bearer {env['token']}"}
    if delivery == "integrated":
        owner = SimpleNamespace(identity={"machine_id": machine_id}, data_dir=tmp_path)
        backend = LocalExecutionBackend(env["app"], owner)
        await bus.register_local(machine_id, backend)
        receiver = asyncio.create_task(backend._consume())

        async def finish():
            await backend.queue.put(None)
            await receiver
            await bus.unregister_local(machine_id, backend)
    else:
        token = generate_machine_token()
        hashed, hint = hash_machine_token(token)
        async with env["factory"]() as db:
            db.add(
                MachineToken(machine_id=machine_id, token_hash=hashed, lookup_hint=hint)
            )
            await db.commit()
        daemon = MachineDaemon(
            "ws://fixture/ws/machines/" + machine_id,
            machine_id,
            token,
            agent_dirs_root=tmp_path / "agents",
            workspace_registry_path=tmp_path / "registry.json",
            workspace_signing_key_path=tmp_path / "signing.key",
        )
        incoming = asyncio.Queue()
        accepted = asyncio.Event()

        class DaemonSocket:
            async def send(self, data):
                await incoming.put(data)

        daemon._ws = DaemonSocket()

        class AuthenticatedSocket:
            app = env["app"]

            def __init__(self):
                self.headers = {
                    "sec-websocket-protocol": f"anygarden.v1, bearer.{token}"
                }

            async def accept(self, **kwargs):
                accepted.set()

            async def send_text(self, data):
                await daemon._handle(json.loads(data))

            async def receive_text(self):
                value = await incoming.get()
                if value is None:
                    raise WebSocketDisconnect()
                return value

            async def close(self, **kwargs):
                await incoming.put(None)

        receiver = asyncio.create_task(ws_machine(AuthenticatedSocket(), machine_id))
        await asyncio.wait_for(accepted.wait(), 2)

        async def finish():
            await incoming.put(None)
            await receiver

    try:
        base = "/api/v1/agents/workspace-agent/workspace"
        listing = await env["client"].get(base, headers=headers)
        assert listing.status_code == 200
        body = listing.json()
        assert body["status"] == "ready"
        assert body["machine_name"] == "agents-machine"
        assert body["snapshot"]["cwd"] == str(root)
        assert body["snapshot"]["live"] is False
        assert [entry["name"] for entry in body["snapshot"]["entries"]] == ["folder"]
        preview = await env["client"].get(
            base + "/file", params={"path": "folder/result.md"}, headers=headers
        )
        assert preview.json()["snapshot"]["text"] == "actual runtime output"
        assert (
            await env["client"].get(
                base + "/file", params={"path": ".env"}, headers=headers
            )
        ).status_code == 422
        assert (
            await env["client"].get(
                base, headers={"Authorization": f"Bearer {env['regular_token']}"}
            )
        ).status_code == 403
        # The existing managed manifest does not pretend these files are DB rows.
        manifest = await env["client"].get(
            "/api/v1/agents/workspace-agent/files", headers=headers
        )
        assert manifest.json() == []
        assert root.joinpath("folder/result.md").read_text() == "actual runtime output"
    finally:
        await finish()


async def test_unavailable_states_and_placement_change(workspace_env, tmp_path):
    env = workspace_env
    machine_id, _ = await seed(env, tmp_path)
    headers = {"Authorization": f"Bearer {env['token']}"}
    base = "/api/v1/agents/workspace-agent/workspace"
    async with env["factory"]() as db:
        machine = await db.get(Machine, machine_id)
        machine.control_capabilities = []
        await db.commit()
    assert (await env["client"].get(base, headers=headers)).json()[
        "status"
    ] == "unsupported"
    await env["bus"].unregister(machine_id)
    assert (await env["client"].get(base, headers=headers)).json()[
        "status"
    ] == "offline"
    async with env["factory"]() as db:
        machine = await db.get(Machine, machine_id)
        machine.control_capabilities = [CAPABILITY]
        await db.commit()

    class ChangedPlacement:
        async def send(self, frame):
            async with env["factory"]() as db:
                agent = await db.get(Agent, "workspace-agent")
                agent.generation += 1
                await db.commit()
            env["bus"].resolve_workspace(
                machine_id, {**frame, "snapshot": {"status": "ready"}}
            )
            return True

    receiver = ChangedPlacement()
    await env["bus"].register_local(machine_id, receiver)
    assert (await env["client"].get(base, headers=headers)).status_code == 409
    await env["bus"].unregister_local(machine_id, receiver)


async def test_broker_correlates_machine_agent_generation_and_cleans_timeout_cancel_disconnect():
    bus = MachineBus()
    frames = asyncio.Queue()

    class Receiver:
        async def send(self, frame):
            await frames.put(frame)
            return True

    receiver = Receiver()
    await bus.register_local("machine", receiver)
    request = asyncio.create_task(
        bus.request_workspace(
            "machine", agent_id="agent", generation=2, operation="list", path=""
        )
    )
    frame = await frames.get()
    response = {**frame, "snapshot": {"status": "ready"}}
    assert not bus.resolve_workspace("wrong-machine", response)
    assert not bus.resolve_workspace("machine", {**response, "agent_id": "other"})
    assert not bus.resolve_workspace("machine", {**response, "generation": 1})
    assert bus.resolve_workspace("machine", response)
    assert await request == {"status": "ready"}
    assert not bus.resolve_workspace("machine", response)
    with pytest.raises(MachineRequestError, match="timeout"):
        await bus.request_workspace(
            "machine",
            agent_id="agent",
            generation=2,
            operation="list",
            path="",
            timeout=0.001,
        )
    assert not bus._pending
    await frames.get()
    request = asyncio.create_task(
        bus.request_workspace(
            "machine", agent_id="agent", generation=2, operation="list", path=""
        )
    )
    await frames.get()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert not bus._pending
    request = asyncio.create_task(
        bus.request_workspace(
            "machine", agent_id="agent", generation=2, operation="list", path=""
        )
    )
    await frames.get()
    await bus.unregister_local("machine", receiver)
    with pytest.raises(MachineRequestError, match="offline"):
        await request
    assert not bus._pending


async def test_broker_bounds_backlog_and_send_time():
    bus = MachineBus()
    frames = asyncio.Queue()

    class Receiver:
        async def send(self, frame):
            await frames.put(frame)
            return True

    receiver = Receiver()
    await bus.register_local("machine", receiver)
    requests = [
        asyncio.create_task(
            bus.request_workspace(
                "machine", agent_id="agent", generation=1, operation="list", path=""
            )
        )
        for _ in range(32)
    ]
    for _ in requests:
        await frames.get()
    with pytest.raises(MachineRequestError, match="busy"):
        await bus.request_workspace(
            "machine", agent_id="agent", generation=1, operation="list", path=""
        )
    for request in requests:
        request.cancel()
    await asyncio.gather(*requests, return_exceptions=True)
    assert not bus._pending
    await bus.unregister_local("machine", receiver)

    class BlockedReceiver:
        async def send(self, frame):
            await asyncio.Event().wait()

    blocked = BlockedReceiver()
    await bus.register_local("machine", blocked)
    with pytest.raises(MachineRequestError, match="timeout"):
        await bus.request_workspace(
            "machine",
            agent_id="agent",
            generation=1,
            operation="list",
            path="",
            timeout=0.001,
        )
    assert not bus._pending
