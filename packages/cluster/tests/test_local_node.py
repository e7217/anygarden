"""Provider-free integrated-node ownership, lifecycle and failure regressions."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from anygarden.app import create_app
from anygarden.cli import dispatch
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, Base, Participant, Project, Room, User
from anygarden.node.execution import LocalExecutionBackend
from anygarden.node.ownership import (
    NodeOwner,
    NodeOwnershipError,
    read_object,
    stop_node,
    write_object,
)
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus
from anygarden_machine.detector import DetectionResult, EngineInfo
from anygarden_machine.spawner import RunningAgent, SpawnResult


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in (
        "SKILL_STALE_INTERVAL_HOURS",
        "ORPHAN_SWEEPER_INTERVAL_SEC",
        "TURN_RECOVERY_INTERVAL_SEC",
    ):
        monkeypatch.setenv("ANYGARDEN_" + key, "0")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setattr(
        "anygarden_machine.daemon.detect_engines",
        AsyncMock(
            return_value=DetectionResult(
                engines=[EngineInfo(engine="echo", version="test", path="fake")]
            )
        ),
    )


def test_owner_identity_is_stable_and_second_process_cannot_start(tmp_path):
    owner = NodeOwner(tmp_path)
    owner.acquire()
    identity = dict(owner.identity)
    assert identity["node_id"] != identity["machine_id"]
    command = "from anygarden.node.ownership import NodeOwner; from pathlib import Path; import sys; NodeOwner(Path(sys.argv[1])).acquire()"
    result = subprocess.run(
        [sys.executable, "-c", command, str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "already running" in result.stderr
    assert read_object(tmp_path / "node-owner.json")["nonce"] == owner.nonce
    owner.release(clean=True)
    replacement = NodeOwner(tmp_path)
    replacement.acquire()
    assert replacement.identity == identity
    replacement.release(clean=True)


def test_unclean_exit_does_not_autostart_or_overwrite_evidence(tmp_path):
    command = "from anygarden.node.ownership import NodeOwner; from pathlib import Path; import os,sys; NodeOwner(Path(sys.argv[1])).acquire(); os._exit(7)"
    result = subprocess.run([sys.executable, "-c", command, str(tmp_path)])
    assert result.returncode == 7
    evidence = (tmp_path / "node-owner.json").read_bytes()
    with pytest.raises(NodeOwnershipError, match="recovery required"):
        NodeOwner(tmp_path).acquire()
    assert (tmp_path / "node-owner.json").read_bytes() == evidence
    with pytest.raises(NodeOwnershipError, match="recovery"):
        stop_node(tmp_path, timeout=0.1)


def test_stop_request_targets_owner_nonce_not_pid(tmp_path):
    owner = NodeOwner(tmp_path)
    owner.acquire()
    write_object(tmp_path / "node-stop.json", {"nonce": "previous-owner"})
    assert not owner.stop_requested()
    write_object(tmp_path / "node-stop.json", {"nonce": owner.nonce})
    assert owner.stop_requested()
    owner.release(clean=True)
    replacement = NodeOwner(tmp_path)
    replacement.acquire()
    assert not replacement.stop_requested()
    replacement.release(clean=True)
    stop_node(tmp_path)


@pytest.mark.parametrize("args", [["--workers", "2"], ["--reload"]])
def test_cli_rejects_unsupported_modes_before_creating_state(tmp_path, args):
    result = CliRunner().invoke(
        dispatch, ["start", "--data-dir", str(tmp_path / "node"), *args]
    )
    assert result.exit_code != 0
    assert not (tmp_path / "node").exists()


def test_worker_environment_rejected_before_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    result = CliRunner().invoke(
        dispatch, ["start", "--data-dir", str(tmp_path / "node")]
    )
    assert result.exit_code != 0
    assert "one worker" in result.output
    assert not (tmp_path / "node").exists()


def node_config(tmp_path):
    return AnygardenSettings(
        local_node_data_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'node.db'}",
        room_files_dir=tmp_path / "files",
        artifact_files_dir=tmp_path / "artifacts",
    )


def test_fresh_node_registers_machine_after_real_first_user_and_protects_it(tmp_path):
    config = node_config(tmp_path)
    app = create_app(config)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.test", "password": "test-password-123"},
        )
        assert response.status_code == 201, response.text
        headers = {"Authorization": "Bearer " + response.json()["token"]}
        machines = client.get("/api/v1/machines", headers=headers).json()
        assert len(machines) == 1
        machine = machines[0]
        assert machine["owner_user_id"] == response.json()["user_id"]
        assert machine["status"] == "online"
        assert app.state.local_execution.ready
        machine_id = machine["id"]
        for labels in ({"custom": "retained"}, None):
            response = client.patch(
                f"/api/v1/machines/{machine_id}",
                headers=headers,
                json={"labels": labels},
            )
            assert response.status_code == 200, response.text
            assert response.json()["labels"] == {
                **(labels or {}),
                "anygarden.local_node_id": app.state.node_owner.identity["node_id"],
            }
        response = client.patch(
            f"/api/v1/machines/{machine_id}",
            headers=headers,
            json={"labels": {"anygarden.local_node_id": "replacement"}},
        )
        assert response.status_code == 409
        for method, suffix in [
            ("delete", ""),
            ("post", "/tokens/regenerate"),
            ("post", "/update"),
        ]:
            response = getattr(client, method)(
                f"/api/v1/machines/{machine_id}{suffix}", headers=headers
            )
            assert response.status_code == 409, response.text
        with pytest.raises(Exception) as exc:
            with client.websocket_connect(f"/ws/machines/{machine_id}"):
                pytest.fail("local machine accepted a network owner")
        assert getattr(exc.value, "code", None) == 4001
    assert read_object(tmp_path / "node-owner.json")["state"] == "stopped"
    # Restart reuses the identity and internal row, without a registration step.
    with TestClient(create_app(node_config(tmp_path))) as client:
        assert client.get("/healthz").status_code == 200
        assert client.app.state.local_machine_id == machine_id
        assert client.app.state.local_execution.ready


async def test_partial_start_failure_releases_ownership_and_closes_resources(
    tmp_path, monkeypatch
):
    import anygarden.app as module

    shutdown = AsyncMock()
    monkeypatch.setattr(
        module,
        "_startup_server",
        AsyncMock(side_effect=RuntimeError("partial startup")),
    )
    monkeypatch.setattr(module, "_shutdown_server", shutdown)
    app = create_app(node_config(tmp_path))
    with pytest.raises(RuntimeError, match="partial startup"):
        async with app.router.lifespan_context(app):
            pytest.fail("startup unexpectedly succeeded")
    shutdown.assert_awaited_once()
    replacement = NodeOwner(tmp_path)
    replacement.acquire()
    replacement.release(clean=True)


async def test_local_delivery_uses_real_lifecycle_fences_without_daemon_socket(
    tmp_path, monkeypatch
):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    owner = NodeOwner(tmp_path / "node")
    owner.acquire()
    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)
    app = SimpleNamespace(
        state=SimpleNamespace(
            config=node_config(tmp_path / "node"),
            session_factory=factory,
            machine_bus=bus,
            agent_lifecycle=lifecycle,
        )
    )
    backend = LocalExecutionBackend(app, owner)
    spawned = []
    spawner = backend.daemon._spawner

    async def fake_spawn(manifest):
        spawned.append(manifest)
        spawner._agents[manifest.agent_id] = RunningAgent(
            agent_id=manifest.agent_id,
            pid=99999999,
            engine="echo",
            started_at=1.0,
            proc=None,
        )
        return SpawnResult(success=True, agent_id=manifest.agent_id, pid=99999999)

    async def fake_kill(agent_id):
        spawner._agents.pop(agent_id, None)
        return {"success": True}

    monkeypatch.setattr(spawner, "spawn", fake_spawn)
    monkeypatch.setattr(spawner, "kill", fake_kill)
    monkeypatch.setattr(
        "anygarden_machine.daemon.connect",
        lambda *a, **kw: pytest.fail("daemon network connect"),
    )
    async with factory() as db:
        user = User(email="admin@test.local", password_hash="x", is_admin=True)
        project = Project(name="local")
        db.add_all([user, project])
        await db.flush()
        room = Room(project_id=project.id, name="local")
        agent = Agent(name="fake", engine="echo")
        db.add_all([room, agent])
        await db.flush()
        db.add(Participant(room_id=room.id, agent_id=agent.id, role="member"))
        agent_id = agent.id
        await db.commit()
    try:
        await backend.start()
        with pytest.raises(ValueError, match="cannot be replaced"):
            await bus.register(owner.identity["machine_id"], object())
        await asyncio.gather(
            lifecycle.request_start(agent_id), lifecycle.request_start(agent_id)
        )
        for _ in range(100):
            if spawned and not backend.daemon._spawn_tasks:
                break
            await asyncio.sleep(0.01)
        assert len(spawned) == 1
        async with factory() as db:
            row = await db.get(Agent, agent_id)
            assert row.actual_state == "running"
            generation = row.generation
            assert row.placed_on_machine_id == owner.identity["machine_id"]
        await lifecycle.request_stop(agent_id)
        await backend.queue.join()
        await backend.send(
            {
                "type": "sync_desired_state",
                "agent_id": agent_id,
                "desired_state": "running",
                "generation": generation,
                "engine": "echo",
            }
        )
        await backend.queue.join()
        assert len(spawned) == 1
        assert not spawner.list_running()
        await lifecycle.handle_report_actual_state(
            owner.identity["machine_id"],
            [
                {
                    "agent_id": agent_id,
                    "actual_state": "running",
                    "generation": generation,
                    "pid": 123,
                }
            ],
        )
        async with factory() as db:
            row = await db.get(Agent, agent_id)
            assert row.desired_state == "stopped"
            assert row.actual_state != "running"
    finally:
        await backend.close()
        owner.release(clean=True)
        await engine.dispose()


async def test_cleanup_failure_keeps_recovery_required(tmp_path, monkeypatch):
    import anygarden.app as module

    monkeypatch.setattr(module, "_startup_server", AsyncMock())
    monkeypatch.setattr(module, "_shutdown_server", AsyncMock())

    class Backend:
        def __init__(self, *args):
            pass

        start = AsyncMock()
        close = AsyncMock(side_effect=RuntimeError("child cleanup unconfirmed"))

    monkeypatch.setattr("anygarden.node.execution.LocalExecutionBackend", Backend)
    app = create_app(node_config(tmp_path))
    with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
        async with app.router.lifespan_context(app):
            pass
    with pytest.raises(NodeOwnershipError, match="recovery required"):
        NodeOwner(tmp_path).acquire()


async def test_shutdown_waits_for_inflight_spawn_and_kills_its_process(
    tmp_path, monkeypatch
):
    from anygarden_machine.daemon import MachineDaemon
    from anygarden_machine.proc_kill import subprocess_group_kwargs

    daemon = MachineDaemon(
        "http://localhost:8000",
        "local-machine",
        "",
        agent_dirs_root=tmp_path / "agents",
        workspace_registry_path=tmp_path / "workspaces.json",
        workspace_signing_key_path=tmp_path / "signing.key",
    )
    started = asyncio.Event()
    proceed = asyncio.Event()
    processes = []

    async def receive(data):
        if data["type"] == "token_request":
            daemon._token_futures[data["agent_ids"][0]].set_result("fake-token")

    daemon._send = receive

    async def delayed_spawn(manifest):
        started.set()
        await proceed.wait()
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            **subprocess_group_kwargs(),
        )
        processes.append(proc)
        daemon._spawner._agents[manifest.agent_id] = RunningAgent(
            agent_id=manifest.agent_id,
            pid=proc.pid,
            engine="echo",
            started_at=1,
            proc=proc,
        )
        return SpawnResult(success=True, agent_id=manifest.agent_id, pid=proc.pid)

    monkeypatch.setattr(daemon._spawner, "spawn", delayed_spawn)
    try:
        await daemon._handle(
            {
                "type": "sync_desired_state",
                "agent_id": "local-agent",
                "desired_state": "running",
                "generation": 1,
                "engine": "echo",
            }
        )
        await asyncio.wait_for(started.wait(), 2)
        close_task = asyncio.create_task(daemon.close_local_execution())
        await asyncio.sleep(0)
        assert not close_task.done()
        proceed.set()
        await asyncio.wait_for(close_task, 15)
        assert len(processes) == 1
        assert processes[0].returncode is not None
        assert not daemon._spawner.list_running()
        await daemon._handle(
            {
                "type": "sync_desired_state",
                "agent_id": "local-agent",
                "desired_state": "running",
                "generation": 2,
                "engine": "echo",
            }
        )
        assert len(processes) == 1
    finally:
        proceed.set()
        for proc in processes:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


def test_real_cli_start_stop_and_restart_without_network_daemon(tmp_path):
    import os
    import socket
    import time
    import urllib.request

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("ANYGARDEN_") and k != "WEB_CONCURRENCY"
    }
    for key in (
        "SKILL_STALE_INTERVAL_HOURS",
        "ORPHAN_SWEEPER_INTERVAL_SEC",
        "TURN_RECOVERY_INTERVAL_SEC",
    ):
        environment["ANYGARDEN_" + key] = "0"
    command = [sys.executable, "-c", "from anygarden.cli import dispatch; dispatch()"]
    identity = None
    for _ in range(2):
        with (tmp_path / "server.log").open("w") as log:
            process = subprocess.Popen(
                command + ["start", "--data-dir", str(tmp_path), "--port", str(port)],
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert process.poll() is None, (tmp_path / "server.log").read_text()
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/healthz", timeout=0.2
                        ) as response:
                            assert response.status == 200
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    pytest.fail("CLI startup timed out")
                current = read_object(tmp_path / "node-identity.json")
                if identity is not None:
                    assert current == identity
                identity = current
                # A second independent command must fail without replacing the owner.
                duplicate = subprocess.run(
                    command
                    + ["start", "--data-dir", str(tmp_path), "--port", str(port)],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert duplicate.returncode != 0
                assert "already running" in duplicate.stdout + duplicate.stderr
                stopped = subprocess.run(
                    command + ["stop", "--data-dir", str(tmp_path)],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                assert stopped.returncode == 0, stopped.stdout + stopped.stderr
                assert process.wait(timeout=10) == 0, (
                    tmp_path / "server.log"
                ).read_text()
                assert read_object(tmp_path / "node-owner.json")["state"] == "stopped"
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


def test_integrated_node_refuses_implicit_schema_upgrade(tmp_path):
    import sqlite3

    config = node_config(tmp_path)
    with TestClient(create_app(config)):
        pass
    with sqlite3.connect(tmp_path / "node.db") as db:
        db.execute("UPDATE alembic_version SET version_num = '062'")
    with pytest.raises(RuntimeError, match="explicitly migrate"):
        with TestClient(create_app(config)):
            pytest.fail("old database automatically upgraded")
    with sqlite3.connect(tmp_path / "node.db") as db:
        assert (
            db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "062"
        )


@pytest.mark.parametrize(
    "missing,reason",
    [
        ("node-owner.json", "receipt is missing"),
        ("node-identity.json", "identity is missing"),
    ],
)
def test_incomplete_ownership_metadata_requires_recovery(tmp_path, missing, reason):
    owner = NodeOwner(tmp_path)
    owner.acquire()
    owner.release(clean=True)
    (tmp_path / missing).unlink()
    with pytest.raises(NodeOwnershipError, match=reason):
        NodeOwner(tmp_path).acquire()
    assert not (tmp_path / missing).exists()
