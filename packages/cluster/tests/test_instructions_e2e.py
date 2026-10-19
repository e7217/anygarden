"""#908 — disk instructions over the real machine handler and daemon.

API → server service → machine WebSocket handler → MachineDaemon → disk,
and the reverse path for edits the agent makes on disk.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy import select

from anygarden.auth.machine_token import generate_machine_token, hash_machine_token
from anygarden.db.models import Agent, AgentFile, Machine, MachineToken
from anygarden.machine import instructions as ins
from anygarden.machine.daemon import MachineDaemon
from anygarden.machine.protocol.frames import SyncDesiredStateFrame
from anygarden.machine.spawner import SpawnManifest
from anygarden.ws.machine_handler import ws_machine

from .test_agents_api import agents_env as _agents_env

instructions_env = _agents_env
AGENT = "ins-agent"
ROOM = "11111111-1111-1111-1111-111111111111"


async def _until(predicate, timeout: float = 3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = await predicate()
        if value:
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


@pytest.mark.req("AGT-11")
@pytest.mark.asyncio
# Several sessions run concurrently here (machine frames, API calls); the
# in-memory StaticPool shares one connection, so one session's rollback
# could undo another's flushed write (#726/#758). Use a file database.
@pytest.mark.parametrize("instructions_env", ["file"], indirect=True)
async def test_disk_instructions_round_trip(instructions_env, tmp_path):
    env = instructions_env
    machine_id = env["machine"].id
    headers = {"Authorization": f"Bearer {env['token']}"}
    token = generate_machine_token()
    hashed, hint = hash_machine_token(token)
    async with env["factory"]() as db:
        machine = await db.get(Machine, machine_id)
        machine.control_capabilities = [ins.CAPABILITY]
        db.add(MachineToken(machine_id=machine_id, token_hash=hashed, lookup_hint=hint))
        db.add(Agent(id=AGENT, name="Infra", engine="codex-cli", agents_md="admin rule",
                     placed_on_machine_id=machine_id, desired_state="running",
                     actual_state="running", generation=1))
        db.add(AgentFile(agent_id=AGENT, path="skills/deploy/SKILL.md", content="# Deploy"))
        await db.commit()
    await env["bus"].unregister(machine_id)

    daemon = MachineDaemon(
        "ws://fixture/ws/machines/" + machine_id, machine_id, token,
        agent_dirs_root=tmp_path / "agents",
        workspace_registry_path=tmp_path / "registry.json",
        workspace_signing_key_path=tmp_path / "signing.key",
    )
    # The server's spawn manifest, as a capable machine receives it.
    daemon._manifest_store.save_if_authoritative(SyncDesiredStateFrame(
        agent_id=AGENT, desired_state="running", generation=1, engine="codex-cli",
        rooms=[ROOM], agents_md="admin rule", files={"skills/deploy/SKILL.md": "# Deploy"},
        instructions_revision=0,
    ))
    root = daemon._spawner._materialize_agent_dir(SpawnManifest(
        agent_id=AGENT, engine="codex-cli", agent_token="t", rooms=[ROOM],
        agents_md="admin rule", files={"skills/deploy/SKILL.md": "# Deploy"},
        instructions_revision=0,
    ))
    daemon._spawner.list_running = lambda: [{"agent_id": AGENT}]  # type: ignore[method-assign]
    # Process start/stop is not under test. A real reconcile would spawn the
    # engine and re-materialize while the test writes AGENTS.md directly,
    # outside the per-agent lock a running agent never races with.
    daemon._reconcile_agent = AsyncMock()  # type: ignore[method-assign]

    incoming: asyncio.Queue = asyncio.Queue()
    accepted = asyncio.Event()

    class DaemonSocket:
        async def send(self, data):
            await incoming.put(data)

    daemon._ws = DaemonSocket()

    class AuthenticatedSocket:
        app = env["app"]

        def __init__(self):
            self.headers = {"sec-websocket-protocol": f"anygarden.v1, bearer.{token}"}

        async def accept(self, **kwargs):
            accepted.set()

        async def send_text(self, data):
            # Server → daemon frames are handled off the receive loop, as a
            # real daemon reads them from its own socket.
            asyncio.get_running_loop().create_task(daemon._handle(json.loads(data)))

        async def receive_text(self):
            value = await incoming.get()
            if value is None:
                raise WebSocketDisconnect()
            return value

        async def close(self, **kwargs):
            await incoming.put(None)

    # The restart that follows an instructions change is not under test and
    # must not spawn a real engine process.
    env["lifecycle"].bump_generation = AsyncMock()
    receiver = asyncio.create_task(ws_machine(AuthenticatedSocket(), machine_id))
    await asyncio.wait_for(accepted.wait(), 2)
    client = env["client"]

    async def agent_row():
        async with env["factory"]() as db:
            return await db.get(Agent, AGENT)

    try:
        # 1. The agent edits its own instructions on disk.
        (root / "AGENTS.md").write_text(ins.compose("agent rule", "stale"))
        (root / "skills" / "notes").mkdir()
        (root / "skills" / "notes" / "SKILL.md").write_text("# Notes")
        # A report tick may read the files mid-edit and record that first;
        # later ticks (one update in flight at a time) bring the rest up.

        async def synced() -> bool:
            await daemon._flush_instruction_updates()
            row = await agent_row()
            receipt = ins.read_receipt(root)
            return (row.agents_md == "agent rule" and receipt is not None
                    and receipt.revision == int(row.instructions_revision or 0)
                    and receipt.digest == ins.collect(root).digest())

        await _until(synced)
        agent_revision = int((await agent_row()).instructions_revision)
        files = (await client.get(f"/api/v1/agents/{AGENT}/files", headers=headers)).json()
        assert {f["path"] for f in files} == {"skills/deploy/SKILL.md", "skills/notes/SKILL.md"}

        # 2. A person edits them through the API; the disk changes first.
        response = await client.put(f"/api/v1/agents/{AGENT}",
                                    json={"agents_md": "person rule", "agents_md_set": True},
                                    headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["instructions_mode"] == "disk"
        assert ins.split_user_area((root / "AGENTS.md").read_text()) == "person rule"
        await _until(lambda: _receipt_is(root, agent_revision + 1))
        await daemon._flush_instruction_updates()

        # 3. Restoring the agent's version writes it back to disk.
        restored = await client.post(
            f"/api/v1/agents/{AGENT}/instructions/revisions/{agent_revision}/restore", headers=headers)
        assert restored.status_code == 200, restored.text
        assert ins.split_user_area((root / "AGENTS.md").read_text()) == "agent rule"
        await _until(lambda: _receipt_is(root, agent_revision + 2))

        history = (await client.get(f"/api/v1/agents/{AGENT}/instructions/revisions",
                                    headers=headers)).json()
        assert [(r["revision"], r["source"], r["restored_from"]) for r in history[:3]] == [
            (agent_revision + 2, "user", agent_revision),
            (agent_revision + 1, "user", None),
            (agent_revision, "agent", None),
        ]
        assert {r["source"] for r in history[2:]} == {"agent"}
    finally:
        await incoming.put(None)
        await receiver

    # 4. Offline: the snapshot is read-only.
    listed = (await client.get("/api/v1/agents", headers=headers)).json()
    assert next(a for a in listed if a["id"] == AGENT)["instructions_mode"] == "disk_offline"
    refused = await client.put(f"/api/v1/agents/{AGENT}",
                               json={"agents_md": "x", "agents_md_set": True}, headers=headers)
    assert refused.status_code == 409
    async with env["factory"]() as db:
        skills = (await db.scalars(select(AgentFile.path).where(AgentFile.agent_id == AGENT))).all()
    assert set(skills) == {"skills/deploy/SKILL.md", "skills/notes/SKILL.md"}


async def _receipt_is(root, revision: int) -> bool:
    receipt = ins.read_receipt(root)
    return receipt is not None and receipt.revision == revision
