"""Two product apps, real mTLS and agent WS, with a no-network fake Codex CLI.

Only engine inference is substituted. User APIs, startup workers, channel
commands, agent control, subprocess supervision and authority receipts are real.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import uvicorn
from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.models import Agent, AgentToken, Machine, Participant, Room, Task
from anygarden.federation.delegation_models import Delegation
from anygarden.federation.delegation_wiring import (
    install_product_delegation,
    make_local_policy,
)
from anygarden.federation.models import Peer
from anygarden.shared_channels.product import derived_id
from anygarden_agent.client import ChatClient
from anygarden_agent.runtime.execution.launch import ExecutionLaunch
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from . import test_shared_channels
from .test_delegation_product_mount import _add_remote_actor
from .test_federation_trust import endpoint, pair  # noqa: F401

channels = test_shared_channels.channels


def uid():
    return str(uuid4())


@asynccontextmanager
async def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(20):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Product app failed to start")
                await asyncio.sleep(0.02)
        yield port
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 10)
        finally:
            sock.close()


@pytest.fixture
async def execution_pair(channels, tmp_path, monkeypatch):
    a, b = channels
    for key in (
        "ANYGARDEN_SKILL_STALE_INTERVAL_HOURS",
        "ANYGARDEN_ORPHAN_SWEEP_INTERVAL_SEC",
        "ANYGARDEN_TURN_RECOVERY_INTERVAL_SEC",
        "ANYGARDEN_DELEGATION_SWEEPER_INTERVAL_SEC",
    ):
        monkeypatch.setenv(key, "0")
    monkeypatch.setenv("ANYGARDEN_FEDERATION_EXECUTION_INTERVAL_SEC", "0.1")
    for node in (a, b):
        node.delegation = install_product_delegation(node.c)
        node.s.local_policy = make_local_policy(node.s.node_id)
    async with a.s.sessions.begin() as db:
        db.add(Participant(room_id=a.channel, user_id=a.admin, role="owner"))
    principal = {"node_id": a.s.node_id, "kind": "human", "principal_id": a.admin}
    async with a.s.sessions.begin() as db:
        await a.c.publication(
            db, actor_id=a.admin, channel_id=a.channel, principal=principal, active=True
        )
    async with a.s.sessions.begin() as db:
        await a.c.change_participant(
            db,
            actor_id=a.admin,
            channel_id=a.channel,
            operation_id=uid(),
            principal=principal,
            role="owner",
            active=True,
            expected_revision=0,
        )
    await _add_remote_actor(a, b, a.channel, b.actor)
    dm, machine = uid(), uid()
    token = generate_token()
    token_hash, hint = hash_agent_token(token)
    async with b.s.sessions.begin() as db:
        db.add(
            Machine(
                id=machine,
                name="remote test machine",
                hostname="test-machine",
                owner_user_id=b.admin,
                status="online",
            )
        )
        await db.flush()
        db.add(
            Agent(
                id=b.actor,
                name="Remote builder",
                engine="codex-cli",
                permission_level="standard",
                generation=1,
                placed_on_machine_id=machine,
                actual_state="running",
                desired_state="running",
            )
        )
        await db.flush()
        db.add(
            Room(
                id=dm,
                name="Remote builder DM",
                is_dm=True,
                representative_agent_id=b.actor,
            )
        )
        await db.flush()
        db.add_all(
            [
                Participant(room_id=dm, agent_id=b.actor, role="member"),
                Participant(room_id=b.mirror, agent_id=b.actor, role="member"),
                AgentToken(agent_id=b.actor, token_hash=token_hash, lookup_hint=hint),
            ]
        )
    root = tmp_path / "agent-workspace"
    root.mkdir()
    (root / "workspace").mkdir()
    auth = tmp_path / "synthetic-codex-home"
    auth.mkdir()
    (auth / "auth.json").write_text('{"OPENAI_API_KEY":"synthetic-offline-test"}')
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, sys, time
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.154.0"); sys.exit(0)
prompt = sys.stdin.read()
with Path("calls.jsonl").open("a") as file:
    file.write(json.dumps({"prompt": prompt, "cwd": str(Path.cwd()), "env_keys": sorted(os.environ)}) + "\n")
print(json.dumps({"type":"thread.started","thread_id":"local-only-session"}), flush=True)
print(json.dumps({"type":"turn.started"}), flush=True)
if prompt == "wait-for-cancel": time.sleep(30)
if prompt == "engine-failure":
    print(json.dumps({"type":"turn.failed","error":{"message":"fixture failure"}}), flush=True)
    sys.exit(1)
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"Remote execution complete"}}), flush=True)
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}), flush=True)
Path(sys.argv[sys.argv.index("-o")+1]).write_text("Remote execution complete")
"""
    )
    executable.chmod(0o700)
    async with AsyncExitStack() as stack:
        for node in (a, b):
            config = AnygardenSettings(
                db_url=str(node.engine.url),
                jwt_secret="synthetic-federation-flow-secret",
                mcp_secrets_key=Fernet.generate_key().decode(),
                peer_credentials_dir=tmp_path / "no-compose",
                peer_listen_host="127.0.0.1",
                peer_listen_port=0,
                room_files_dir=tmp_path / node.channel,
            )
            app = create_app(config, channel_service=node.c)
            app.state.engine, app.state.session_factory = node.engine, node.s.sessions
            app.state.peer_service = node.s
            app.state.delegation_service = node.delegation
            app.state.goal_scheduler = SimpleNamespace(
                start=lambda: None, stop=AsyncMock()
            )
            node.app = app
            node.port = await stack.enter_async_context(serve(app))
        for node, other in ((a, b), (b, a)):
            async with node.s.sessions.begin() as db:
                peer = await db.get(Peer, other.s.node_id)
                peer.endpoint = endpoint(
                    other.app.state.peer_listener.port
                ).model_dump()
        client = ChatClient(f"ws://127.0.0.1:{b.port}", token, "Remote builder")
        client._generation, client._agent_id = 1, b.actor
        client.execution_launch_ready = True
        client._execution_adapter = SimpleNamespace(
            _engine="codex-cli",
            _codex_path=str(executable),
            _root=root,
            _runtime_version="0.154.0",
            _environment={"CODEX_HOME": str(auth)},
            _launch=ExecutionLaunch("codex-cli", None, None, 1, None),
            _permission_level="standard",
            _system_prompt="",
            _reasoning_effort=None,
            _turn_timeout=60,
            stop=AsyncMock(),
        )
        await client.join_room(dm)
        await client.wait_for_room(dm, timeout=10)
        stack.push_async_callback(client.close)
        bearer = create_user_token(
            a.admin, "a@example.test", True, secret=a.app.state.config.jwt_secret
        )
        http = await stack.enter_async_context(
            AsyncClient(
                transport=ASGITransport(app=a.app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {bearer}"},
            )
        )
        yield SimpleNamespace(
            a=a,
            b=b,
            http=http,
            client=client,
            root=root,
            base=f"/api/v1/shared-channels/{a.s.node_id}/{a.channel}",
        )


async def submit_task(env, text):
    message_request, delegation_request = uid(), uid()
    response = await env.http.post(
        env.base + "/messages", json={"request_id": message_request, "text": text}
    )
    assert response.status_code == 200, response.text
    source = derived_id(
        "shared-message", env.a.s.node_id, env.a.channel, message_request
    )
    response = await env.http.post(
        env.base + "/delegations",
        json={
            "request_id": delegation_request,
            "source_message_id": source,
            "executor": {"node_id": env.b.s.node_id, "agent_id": env.b.actor},
        },
    )
    assert response.status_code == 200, response.text
    return derived_id(
        "shared-delegation", env.a.s.node_id, env.a.channel, delegation_request
    )


async def wait_state(env, delegation, states):
    last = None
    async with asyncio.timeout(15):
        while True:
            async with env.a.s.sessions() as db:
                row = await db.get(Delegation, delegation)
                last = row.state
                if last in states:
                    return row
            await asyncio.sleep(0.05)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [("perform the isolated task", "completed"), ("engine-failure", "failed")],
)
async def test_user_api_runs_on_remote_agent_and_returns_real_receipt(
    execution_pair, prompt, expected
):
    env = execution_pair
    delegation = await submit_task(env, prompt)
    result = await wait_state(env, delegation, {expected})
    assert result.execution_id
    calls = [
        json.loads(line)
        for line in (env.root / "workspace" / "calls.jsonl").read_text().splitlines()
    ]
    assert len(calls) == 1 and calls[0]["prompt"] == prompt
    assert not any(
        key.startswith(("ANYGARDEN_", "RAFT_", "SLOCK_"))
        for key in calls[0]["env_keys"]
    )
    async with env.a.s.sessions() as db:
        task = await db.get(Task, result.task_id)
        assert task.spec == prompt
    response = await env.http.get(env.base)
    assert response.status_code == 200
    entry = next(
        row
        for row in response.json()["delegations"]
        if row["delegation_id"] == delegation
    )
    assert entry["state"] == expected
    if expected == "completed":
        assert entry["result_markdown"] == "Remote execution complete"


async def test_user_cancel_stops_remote_process_before_confirmation(execution_pair):
    env = execution_pair
    delegation = await submit_task(env, "wait-for-cancel")
    result = await wait_state(env, delegation, {"running"})
    response = await env.http.post(
        env.base + f"/delegations/{delegation}/cancel",
        json={"request_id": uid(), "expected_revision": result.revision},
    )
    assert response.status_code == 200, response.text
    result = await wait_state(env, delegation, {"cancelled"})
    assert result.process_state == "stopped"
    assert len((env.root / "workspace" / "calls.jsonl").read_text().splitlines()) == 1


async def test_cancel_before_pickup_never_starts_an_offline_agent(execution_pair):
    env = execution_pair
    await env.client.close()
    delegation = await submit_task(env, "must not execute")
    result = await wait_state(env, delegation, {"requested"})
    response = await env.http.post(
        env.base + f"/delegations/{delegation}/cancel",
        json={"request_id": uid(), "expected_revision": result.revision},
    )
    assert response.status_code == 200, response.text
    result = await wait_state(env, delegation, {"cancelled"})
    assert result.process_state == "not_started"
    assert not (env.root / "workspace" / "calls.jsonl").exists()


async def test_cancel_after_prepare_tombstones_without_start(
    execution_pair, monkeypatch
):
    env = execution_pair
    broker = env.b.app.state.execution_transport
    original = broker.request
    prepared, release = asyncio.Event(), asyncio.Event()

    async def delayed_prepare(agent_id, action, payload, **kwargs):
        result = await original(agent_id, action, payload, **kwargs)
        if action == "prepare":
            prepared.set()
            await release.wait()
        return result

    monkeypatch.setattr(broker, "request", delayed_prepare)
    delegation = await submit_task(env, "prepared but must not execute")
    await asyncio.wait_for(prepared.wait(), 10)
    current = await wait_state(env, delegation, {"requested"})
    response = await env.http.post(
        env.base + f"/delegations/{delegation}/cancel",
        json={"request_id": uid(), "expected_revision": current.revision},
    )
    assert response.status_code == 200, response.text
    release.set()
    result = await wait_state(env, delegation, {"cancelled"})
    assert result.process_state == "not_started"
    assert not (env.root / "workspace" / "calls.jsonl").exists()
    records = env.client._execution_control.db.execute(
        "SELECT state FROM prepared"
    ).fetchall()
    assert len(records) == 1 and records[0][0] == "revoked_prepared"


async def test_lost_result_ack_and_worker_restart_do_not_execute_twice(
    execution_pair, monkeypatch
):
    from anygarden.federation import execution_worker
    from anygarden.federation.delegation_models import DelegationOutbox
    from anygarden.federation.errors import PeerError
    from anygarden.federation.execution_transport import ExecutionTransport

    env = execution_pair
    committed, release = asyncio.Event(), asyncio.Event()
    original = execution_worker.send_executor_command

    async def lost_ack(service, command):
        receipt = await original(service, command)
        if command["kind"] == "task.result" and not committed.is_set():
            committed.set()
            await release.wait()
            raise PeerError("PEER_UNAVAILABLE", 503)
        return receipt

    monkeypatch.setattr(execution_worker, "send_executor_command", lost_ack)
    delegation = await submit_task(env, "survive the lost acknowledgement")
    await asyncio.wait_for(committed.wait(), 10)
    # Restart the app-owned coordinator while its ACK is unavailable. The
    # authority DB and the deployed agent's receipt store remain independent.
    await env.b.app.state.federation_execution_worker.close()
    release.set()
    broker = ExecutionTransport(env.b.s.sessions, env.b.app.state.connection_manager)
    replacement = execution_worker.FederationExecutionWorker(
        env.b.c, broker, interval=0.1
    )
    env.b.app.state.execution_transport = broker
    env.b.app.state.federation_execution_worker = replacement
    replacement.start()
    await wait_state(env, delegation, {"completed"})
    async with asyncio.timeout(10):
        while True:
            async with env.b.s.sessions() as db:
                pending = list(
                    await db.scalars(
                        select(DelegationOutbox).where(
                            DelegationOutbox.delegation_id == delegation,
                            DelegationOutbox.state == "pending",
                        )
                    )
                )
            if not pending:
                break
            await asyncio.sleep(0.05)
    assert len((env.root / "workspace" / "calls.jsonl").read_text().splitlines()) == 1


async def test_authority_grant_withdrawal_stops_running_remote_engine(execution_pair):
    env = execution_pair
    delegation = await submit_task(env, "wait-for-cancel")
    result = await wait_state(env, delegation, {"running"})
    response = await env.http.delete(
        f"/api/v1/node/peers/{env.b.s.node_id}/grants/{env.a.channel}"
    )
    assert response.status_code == 200, response.text
    controller = env.client._execution_control
    async with asyncio.timeout(10):
        while True:
            receipt = controller._manager.owned_receipt(result.execution_id)
            if receipt.outcome is not None:
                break
            await asyncio.sleep(0.05)
    assert receipt.outcome == "cancelled" and receipt.process_state == "stopped"
    # A revoked executor cannot fabricate a new authority completion. The last
    # confirmed remote state remains distinct from local stop evidence.
    async with env.a.s.sessions() as db:
        row = await db.get(Delegation, delegation)
        assert row.state != "completed"
    assert len((env.root / "workspace" / "calls.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("lost_action", ["prepare", "start"])
async def test_control_request_loss_and_restart_retire_unstarted_prepare(
    execution_pair, monkeypatch, lost_action
):
    from anygarden.federation.delegation_models import ExecutorBinding
    from anygarden.federation.execution_transport import ExecutionTransport
    from anygarden.federation.execution_worker import FederationExecutionWorker

    env = execution_pair
    broker = env.b.app.state.execution_transport
    original = broker.request
    interrupted, release = asyncio.Event(), asyncio.Event()

    async def interrupted_request(agent_id, action, payload, **kwargs):
        if action == "start" and lost_action == "start":
            interrupted.set()  # Launch intent exists, but the request never arrives.
            await release.wait()
        result = await original(agent_id, action, payload, **kwargs)
        if action == "prepare" and lost_action == "prepare":
            interrupted.set()  # Agent owns a record whose ACK the server never sees.
            await release.wait()
        return result

    monkeypatch.setattr(broker, "request", interrupted_request)
    delegation = await submit_task(env, "must remain unstarted after restart")
    await asyncio.wait_for(interrupted.wait(), 10)
    # Model an abrupt coordinator crash: no graceful runtime cleanup occurs.
    monkeypatch.setattr(
        env.b.app.state.federation_execution_worker.manager, "close", AsyncMock()
    )
    await env.b.app.state.federation_execution_worker.close()
    assert (
        env.client._execution_control.db.execute(
            "SELECT state FROM prepared"
        ).fetchone()[0]
        == "prepared"
    )
    if lost_action == "prepare":
        current = await wait_state(env, delegation, {"requested"})
        response = await env.http.post(
            env.base + f"/delegations/{delegation}/cancel",
            json={"request_id": uid(), "expected_revision": current.revision},
        )
        assert response.status_code == 200, response.text
    broker = ExecutionTransport(env.b.s.sessions, env.b.app.state.connection_manager)
    replacement = FederationExecutionWorker(env.b.c, broker, interval=0.1)
    env.b.app.state.execution_transport = broker
    env.b.app.state.federation_execution_worker = replacement
    replacement.start()
    await wait_state(
        env, delegation, {"cancelled" if lost_action == "prepare" else "unknown"}
    )
    async with asyncio.timeout(10):
        while True:
            records = env.client._execution_control.db.execute(
                "SELECT state FROM prepared"
            ).fetchall()
            async with env.b.s.sessions() as db:
                binding = await db.get(ExecutorBinding, delegation)
            if (
                records
                and records[0][0] == "revoked_prepared"
                and (lost_action == "start" or binding.local_state == "settled")
            ):
                break
            await asyncio.sleep(0.05)
    assert len(records) == 1
    assert not (env.root / "workspace" / "calls.jsonl").exists()
