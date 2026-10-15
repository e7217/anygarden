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
from structlog.testing import capture_logs

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
from anygarden.machine.detector import DetectionResult, EngineInfo
from anygarden.machine.spawner import RunningAgent, SpawnResult


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
        "anygarden.machine.daemon.detect_engines",
        AsyncMock(
            return_value=DetectionResult(
                engines=[EngineInfo(engine="echo", version="test", path="fake")]
            )
        ),
    )


@pytest.mark.req("NODE-03")
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
@pytest.mark.req("NODE-04")
def test_cli_rejects_unsupported_modes_before_creating_state(tmp_path, args):
    result = CliRunner().invoke(
        dispatch, ["start", "--data-dir", str(tmp_path / "node"), *args]
    )
    assert result.exit_code != 0
    assert not (tmp_path / "node").exists()


@pytest.mark.req("NODE-04")
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


@pytest.mark.req("NODE-11")
def test_node_refuses_an_agent_directory_a_machine_daemon_owns(tmp_path):
    """#848 — a standalone daemon on the node's agents/ would kill its agents."""
    from anygarden.machine.agent_root_lock import AgentRootInUseError, AgentRootLock

    held = AgentRootLock(tmp_path / "agents")
    held.acquire()
    try:
        with pytest.raises(AgentRootInUseError):
            with TestClient(create_app(node_config(tmp_path))):
                pytest.fail("node started on a directory another daemon owns")
    finally:
        held.release()
    # Ownership was released, so the node starts once the daemon is gone,
    # and a clean stop frees the directory again.
    with TestClient(create_app(node_config(tmp_path))) as client:
        assert client.get("/healthz").status_code == 200
        with pytest.raises(AgentRootInUseError):
            AgentRootLock(tmp_path / "agents").acquire()
    after = AgentRootLock(tmp_path / "agents")
    after.acquire()
    after.release()


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
        "anygarden.machine.daemon.connect",
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


@pytest.mark.req("NODE-09")
async def test_shutdown_waits_for_inflight_spawn_and_kills_its_process(
    tmp_path, monkeypatch
):
    from anygarden.machine.daemon import MachineDaemon
    from anygarden.machine.proc_kill import subprocess_group_kwargs

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


@pytest.mark.parametrize("cleanup_failure", [None, "backend", "server"])
@pytest.mark.req("NODE-03")
def test_real_cli_start_stop_and_restart_without_network_daemon(
    tmp_path, cleanup_failure
):
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
    start_command = command
    if cleanup_failure:
        failure_patch = (
            "LocalExecutionBackend.close = fail_cleanup"
            if cleanup_failure == "backend"
            else "app_module._shutdown_server = fail_cleanup"
        )
        start_command = [
            sys.executable,
            "-c",
            """
from anygarden.node.execution import LocalExecutionBackend
import anygarden.app as app_module
from anygarden.cli import dispatch
async def fail_cleanup(*args):
    raise RuntimeError("injected unconfirmed cleanup")
"""
            + failure_patch
            + "\ndispatch()\n",
        ]
    identity = None
    for _ in range(1 if cleanup_failure else 2):
        with (tmp_path / "server.log").open("w") as log:
            process = subprocess.Popen(
                start_command
                + ["start", "--data-dir", str(tmp_path), "--port", str(port)],
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
                exited = process.wait(timeout=10)
                owner_state = read_object(tmp_path / "node-owner.json")["state"]
                if cleanup_failure:
                    assert stopped.returncode != 0
                    assert exited != 0
                    assert owner_state == "running"
                    assert (
                        "Node shutdown failed" in (tmp_path / "server.log").read_text()
                    )
                    restarted = subprocess.run(
                        command
                        + ["start", "--data-dir", str(tmp_path), "--port", str(port)],
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    assert restarted.returncode != 0
                    assert "recovery required" in restarted.stdout + restarted.stderr
                else:
                    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
                    assert exited == 0, (tmp_path / "server.log").read_text()
                    assert owner_state == "stopped"
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


def test_real_cli_sigint_exits_zero_after_confirmed_cleanup(tmp_path):
    """#594 QA finding: SIGINT after full graceful shutdown must not exit 1.

    Uvicorn re-raises the captured SIGINT after capture_signals() restores
    the default handler, so the KeyboardInterrupt surfaces in the CLI frame
    once the lifespan already confirmed cleanup (drain complete, owner
    released, state stopped). Letting it escape made click print "Aborted!"
    and exit 1 — a supervisor would misread that as a crash to restart.
    """
    import os
    import signal
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
    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command + ["start", "--data-dir", str(tmp_path), "--port", str(port)],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert process.poll() is None, log_path.read_text()
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
            process.send_signal(signal.SIGINT)
            exited = process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    log = log_path.read_text()
    assert exited == 0, log
    assert "drain_complete" in log
    assert read_object(tmp_path / "node-owner.json")["state"] == "stopped"
    assert "Aborted!" not in log


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


async def test_integrated_agent_process_inherits_only_explicit_environment(
    tmp_path, monkeypatch
):
    import json
    import os
    from uuid import uuid4

    from anygarden.machine.spawner import SpawnManifest

    for key in (
        "ANYGARDEN_JWT_SECRET",
        "ANYGARDEN_MCP_SECRETS_KEY",
        "ANYGARDEN_DB_URL",
        "OPENAI_API_KEY",
        "UNLISTED_FUTURE_SERVER_SECRET",
        "PYTHONPATH",
    ):
        monkeypatch.setenv(key, "synthetic-server-value")
    owner = NodeOwner(tmp_path / "node")
    owner.acquire()
    app = SimpleNamespace(state=SimpleNamespace(config=node_config(owner.data_dir)))
    backend = LocalExecutionBackend(app, owner)
    spawner = backend.daemon._spawner
    spawner._on_stopped = AsyncMock()
    spawner._on_crashed = AsyncMock()
    recorded = tmp_path / "child.json"
    create_process = asyncio.create_subprocess_exec

    async def substitute_fake_agent(*args, **kwargs):
        # Exercise the real spawner's final env, stdin and process ownership,
        # replacing only the installed agent binary with a provider-free child.
        return await create_process(
            sys.executable,
            "-c",
            "import json,os,sys; from pathlib import Path; "
            "Path(sys.argv[1]).write_text(json.dumps({'env':dict(os.environ),'stdin':sys.stdin.read()}))",
            str(recorded),
            **kwargs,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", substitute_fake_agent)
    try:
        agent_id = str(uuid4())
        result = await spawner.spawn(
            SpawnManifest(
                agent_id=agent_id,
                engine="echo",
                agent_token="scoped-agent-token",
                anygarden_mcp_token="scoped-mcp-token",
                engine_secrets={"OPENAI_API_KEY": "explicit-agent-secret"},
            )
        )
        assert result.success
        running = spawner._agents[agent_id]
        assert await asyncio.wait_for(running.proc.wait(), timeout=10) == 0
        # The legacy watcher cancels itself as part of its completed cleanup.
        await asyncio.gather(running.watch_task, return_exceptions=True)
        child = json.loads(recorded.read_text())
        for key in (
            "ANYGARDEN_JWT_SECRET",
            "ANYGARDEN_MCP_SECRETS_KEY",
            "ANYGARDEN_DB_URL",
            "OPENAI_API_KEY",
            "UNLISTED_FUTURE_SERVER_SECRET",
            "PYTHONPATH",
        ):
            assert key not in child["env"]
        assert child["env"]["PATH"] == os.environ["PATH"]
        assert child["env"]["ANYGARDEN_TOKEN"] == "scoped-agent-token"
        assert child["env"]["ANYGARDEN_AGENT_TOKEN"] == "scoped-mcp-token"
        assert json.loads(child["stdin"]) == {"OPENAI_API_KEY": "explicit-agent-secret"}
    finally:
        await spawner.drain()
        owner.release(clean=True)


@pytest.mark.req("FED-06")
def test_peer_port_without_credentials_is_refused_before_startup(tmp_path):
    """--peer-port fails closed when the configured credential dir is empty.

    The default ``<data-dir>/peer`` gets a first-run pair instead (#839,
    ``test_federation_credentials_setup``); an explicit directory that is
    missing is most likely an unmounted volume and must not get a new identity.
    """
    from click.testing import CliRunner

    from anygarden.cli import dispatch

    result = CliRunner().invoke(
        dispatch,
        ["start", "--data-dir", str(tmp_path), "--peer-port", "8451"],
        env={"ANYGARDEN_PEER_CREDENTIALS_DIR": str(tmp_path / "unmounted")},
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "No peer credentials" in result.output
    assert not (tmp_path / "unmounted").exists()


def test_real_cli_peer_listener_lifecycle_with_stop(tmp_path):
    """--peer-port composes the listener; stop closes it and exits 0."""
    import os
    import socket
    import time
    import urllib.request
    from uuid import uuid4

    from anygarden.federation.certificates import create_credentials

    create_credentials(tmp_path / "peer", str(uuid4()))
    with socket.socket() as api_reservation:
        api_reservation.bind(("127.0.0.1", 0))
        api_port = api_reservation.getsockname()[1]
    with socket.socket() as peer_reservation:
        peer_reservation.bind(("127.0.0.1", 0))
        peer_port = peer_reservation.getsockname()[1]
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
    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command
            + [
                "start",
                "--data-dir",
                str(tmp_path),
                "--port",
                str(api_port),
                "--peer-port",
                str(peer_port),
                "--peer-host",
                "127.0.0.1",
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{api_port}/healthz", timeout=0.2
                    ) as response:
                        assert response.status == 200
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("CLI startup timed out")
            # The mTLS listener is actually accepting connections.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(
                        ("127.0.0.1", peer_port), timeout=0.5
                    ):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("peer listener never accepted connections")
            stopped = subprocess.run(
                command + ["stop", "--data-dir", str(tmp_path)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert stopped.returncode == 0, stopped.stdout + stopped.stderr
            exited = process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert exited == 0, log_path.read_text()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", peer_port), timeout=0.5)


@pytest.mark.req("FED-06")
def test_invalid_credentials_with_peer_port_fail_startup(tmp_path):
    """Requested listener + unusable credentials aborts boot, not silence."""
    import os
    import socket
    import time
    import urllib.request

    peer_dir = tmp_path / "peer"
    peer_dir.mkdir(parents=True)
    (peer_dir / "peer-cert.pem").write_text("not a certificate")
    (peer_dir / "peer-key.pem").write_text("not a key")
    with socket.socket() as api_reservation:
        api_reservation.bind(("127.0.0.1", 0))
        api_port = api_reservation.getsockname()[1]
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("ANYGARDEN_") and k != "WEB_CONCURRENCY"
    }
    command = [sys.executable, "-c", "from anygarden.cli import dispatch; dispatch()"]
    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command
            + [
                "start",
                "--data-dir",
                str(tmp_path),
                "--port",
                str(api_port),
                "--peer-port",
                "8451",
                "--peer-host",
                "127.0.0.1",
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        exited = process.wait(timeout=30)
    assert exited != 0
    log = log_path.read_text()
    assert "are unusable" in log
    with pytest.raises(OSError):
        # Health endpoint must not be serving either.
        urllib.request.urlopen(f"http://127.0.0.1:{api_port}/healthz", timeout=0.5)


# --------------------------------------------------------------------------
# #649 — server-only boots announce that local execution is off


def _server_only_config(tmp_path):
    """Same shape as ``node_config`` minus the integrated data directory."""
    return AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}",
        jwt_secret="synthetic-local-test-secret-not-a-real-credential",
        room_files_dir=tmp_path / "files",
        artifact_files_dir=tmp_path / "artifacts",
    )


def _silence_logging_setup(monkeypatch):
    """Keep ``capture_logs`` installed across startup.

    ``_startup_server`` calls ``configure_logging``, which re-runs
    ``structlog.configure`` and would replace the capturing processor
    chain. The real ordering — the event is emitted *after*
    ``_startup_server``, so structlog is configured by then — is asserted
    by the two tests below only for presence/absence; rendering is covered
    by the live boot.
    """
    import anygarden.app as module

    monkeypatch.setattr(module, "configure_logging", lambda *a, **k: None)


async def test_local_execution_disabled_is_logged_outside_integrated_mode(
    tmp_path, monkeypatch
):
    """A ``make dev``-style boot says agents will not run here."""
    _silence_logging_setup(monkeypatch)
    app = create_app(_server_only_config(tmp_path))
    with capture_logs() as entries:
        async with app.router.lifespan_context(app):
            assert getattr(app.state, "local_execution", None) is None
    emitted = [
        entry
        for entry in entries
        if entry["event"] == "startup.local_execution_disabled"
    ]
    assert len(emitted) == 1
    # info, not a warning: for a multi-host deployment this is normal.
    assert emitted[0]["log_level"] == "info"
    assert "anygarden start" in emitted[0]["reason"]


async def test_integrated_mode_stays_silent_about_disabled_local_execution(
    tmp_path, monkeypatch
):
    """``anygarden start`` runs agents, so the notice must not appear."""
    _silence_logging_setup(monkeypatch)
    app = create_app(node_config(tmp_path))
    with capture_logs() as entries:
        async with app.router.lifespan_context(app):
            # Readiness additionally needs the first signup; the branch under
            # test only needs the backend to have been constructed at all.
            assert app.state.local_execution is not None
    assert not [
        entry
        for entry in entries
        if entry["event"] == "startup.local_execution_disabled"
    ]


def _bare_backend(tmp_path):
    """Backend whose loops can run without a database or registration."""
    owner = NodeOwner(tmp_path / "node")
    owner.acquire()
    shutdown = []
    app = SimpleNamespace(
        state=SimpleNamespace(
            config=node_config(tmp_path / "node"),
            node_shutdown_callback=lambda: shutdown.append(True),
        )
    )
    return LocalExecutionBackend(app, owner), owner, shutdown


@pytest.mark.asyncio
async def test_transient_frame_error_does_not_shut_down_node(tmp_path, monkeypatch):
    """#772: one frame failing on a locked DB must not stop the whole node."""
    from sqlalchemy.exc import OperationalError

    backend, owner, shutdown = _bare_backend(tmp_path)
    handled = []

    async def handle(frame):
        if frame["n"] == 1:
            raise OperationalError("UPDATE agents", {}, Exception("database is locked"))
        handled.append(frame["n"])

    monkeypatch.setattr(backend.daemon, "_handle", handle)
    task = asyncio.create_task(backend._consume())
    try:
        for n in (1, 2):
            backend.queue.put_nowait({"type": "sync_desired_state", "n": n})
        await asyncio.wait_for(backend.queue.join(), 2)
        assert handled == [2]
        assert backend.failed is False
        assert shutdown == []
        assert not task.done()
    finally:
        backend.queue.put_nowait(None)
        await asyncio.wait_for(task, 2)
        owner.release(clean=True)


@pytest.mark.asyncio
async def test_ownership_error_in_frame_still_shuts_down_node(tmp_path, monkeypatch):
    backend, owner, shutdown = _bare_backend(tmp_path)

    async def handle(frame):
        raise NodeOwnershipError("mapping mismatch")

    monkeypatch.setattr(backend.daemon, "_handle", handle)
    task = asyncio.create_task(backend._consume())
    backend.queue.put_nowait({"type": "sync_desired_state"})
    with pytest.raises(NodeOwnershipError):
        await asyncio.wait_for(task, 2)
    assert backend.failed is True
    assert shutdown == [True]
    owner.release(clean=True)


@pytest.mark.asyncio
async def test_maintenance_survives_transient_errors(tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    backend, owner, shutdown = _bare_backend(tmp_path)
    calls = []

    async def ensure_registered():
        calls.append(True)
        if len(calls) <= 2:
            raise OperationalError("SELECT", {}, Exception("database is locked"))

    monkeypatch.setattr(backend, "ensure_registered", ensure_registered)
    monkeypatch.setattr(backend, "MAX_MAINTENANCE_FAILURES", 3)
    task = asyncio.create_task(backend._maintain())
    try:
        while len(calls) < 4:
            await asyncio.sleep(0.05)
        assert backend.failed is False
        assert shutdown == []
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        owner.release(clean=True)


@pytest.mark.asyncio
async def test_maintenance_gives_up_after_consecutive_failures(tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    backend, owner, shutdown = _bare_backend(tmp_path)

    async def ensure_registered():
        raise OperationalError("SELECT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(backend, "ensure_registered", ensure_registered)
    monkeypatch.setattr(backend, "MAX_MAINTENANCE_FAILURES", 3)
    with pytest.raises(OperationalError):
        await asyncio.wait_for(backend._maintain(), 5)
    assert backend.failed is True
    assert shutdown == [True]
    owner.release(clean=True)


@pytest.mark.req("NODE-09")
async def test_node_stops_running_agents_and_shuts_down_without_lock_contention(
    tmp_path, monkeypatch
):
    """#772: on a file DB, stopping running agents and then shutting the node
    down cleans every agent up; a lock error never ends the node early."""
    from sqlalchemy import select

    from anygarden.db.models import Machine

    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'node.db'}")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    owner = NodeOwner(tmp_path / "node")
    owner.acquire()
    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)
    shutdown = []
    app = SimpleNamespace(
        state=SimpleNamespace(
            config=node_config(tmp_path / "node"),
            session_factory=factory,
            machine_bus=bus,
            agent_lifecycle=lifecycle,
            node_shutdown_callback=lambda: shutdown.append(True),
        )
    )
    backend = LocalExecutionBackend(app, owner)
    spawner = backend.daemon._spawner
    spawned, killed = [], []

    async def fake_spawn(manifest):
        spawned.append(manifest.agent_id)
        spawner._agents[manifest.agent_id] = RunningAgent(
            agent_id=manifest.agent_id, pid=99999999, engine="echo",
            started_at=1.0, proc=None,
        )
        return SpawnResult(success=True, agent_id=manifest.agent_id, pid=99999999)

    async def fake_kill(agent_id):
        killed.append(agent_id)
        spawner._agents.pop(agent_id, None)
        return {"success": True}

    monkeypatch.setattr(spawner, "spawn", fake_spawn)
    monkeypatch.setattr(spawner, "kill", fake_kill)
    monkeypatch.setattr(
        "anygarden.machine.daemon.connect",
        lambda *a, **kw: pytest.fail("daemon network connect"),
    )
    async with factory() as db:
        user = User(email="admin@test.local", password_hash="x", is_admin=True)
        project = Project(name="local")
        db.add_all([user, project])
        await db.flush()
        room = Room(project_id=project.id, name="local")
        agents = [Agent(name=f"fake-{n}", engine="echo") for n in range(3)]
        db.add_all([room, *agents])
        await db.flush()
        db.add_all(
            Participant(room_id=room.id, agent_id=a.id, role="member") for a in agents
        )
        agent_ids = [a.id for a in agents]
        await db.commit()
    closed = False
    try:
        await backend.start()
        await asyncio.gather(*(lifecycle.request_start(a) for a in agent_ids))
        for _ in range(200):
            if len(spawned) == 3 and not backend.daemon._spawn_tasks:
                break
            await asyncio.sleep(0.01)
        assert sorted(spawned) == sorted(agent_ids)

        # Stop two running agents at once while the node keeps writing state.
        await asyncio.gather(*(lifecycle.request_stop(a) for a in agent_ids[:2]))
        await asyncio.wait_for(backend.queue.join(), 5)
        assert sorted(killed) == sorted(agent_ids[:2])
        assert backend.failed is False and shutdown == []

        # Shutting the node down cleans up the agent still running.
        await asyncio.wait_for(backend.close(), 10)
        closed = True
        assert sorted(killed) == sorted(agent_ids)
        assert not spawner.list_running()
        assert backend.failed is False and shutdown == []
        async with factory() as db:
            machine = await db.get(Machine, owner.identity["machine_id"])
            assert machine.status == "offline"
            stopped = await db.scalars(select(Agent).where(Agent.id.in_(agent_ids[:2])))
            assert {a.desired_state for a in stopped} == {"stopped"}
    finally:
        if not closed:
            await backend.close()
        owner.release(clean=True)
        await engine.dispose()


# ── NODE-10 — transient I/O must not leave the node half-dead ─────────────
#
# 2026-10-13 benchmark: the host disk filled for a few seconds. structlog's
# PrintLogger writes with ``print(..., flush=True)``, so the *log call inside
# the except handlers* raised ``OSError(ENOSPC)``. It escaped ``_handle_frame``
# and ``_maintain``'s tolerance, ``_failed`` then raised again from its own
# log call before reaching the shutdown callback: execution was dead forever
# while the API kept serving and ``/healthz`` stayed ``ok``.


class _FullDiskLog:
    """Stand-in for the structlog logger whose stdout write hits ENOSPC."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def emit(event, **kwargs):
            self.calls.append((name, event))
            raise OSError(28, "No space left on device")

        return emit


def _disk_full():
    return OSError(28, "No space left on device")


@pytest.mark.req("NODE-10")
async def test_frame_and_log_disk_full_errors_are_survived(tmp_path, monkeypatch):
    from anygarden.node import execution

    backend, owner, shutdown = _bare_backend(tmp_path)
    full_log = _FullDiskLog()
    monkeypatch.setattr(execution, "log", full_log)
    handled = []

    async def handle(frame):
        if frame["n"] == 1:
            raise _disk_full()
        handled.append(frame["type"])

    monkeypatch.setattr(backend.daemon, "_handle", handle)
    task = asyncio.create_task(backend._consume())
    try:
        backend.queue.put_nowait({"type": "sync_desired_state", "n": 1})
        backend.queue.put_nowait({"type": "spawn_agent", "n": 2})
        await asyncio.wait_for(backend.queue.join(), 2)
        # The failing frame was reported (attempted), and the later spawn ran.
        assert ("exception", "local_execution.frame_failed") in full_log.calls
        assert handled == ["spawn_agent"]
        assert backend.failed is False
        assert shutdown == []
        assert not task.done()
        assert await backend.send({"type": "spawn_agent", "n": 3}) is True
    finally:
        backend.queue.put_nowait(None)
        await asyncio.wait_for(task, 2)
        owner.release(clean=True)


@pytest.mark.req("NODE-10")
async def test_stop_request_check_disk_error_is_survived(tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from anygarden.node import execution

    backend, owner, shutdown = _bare_backend(tmp_path)
    monkeypatch.setattr(execution, "log", _FullDiskLog())
    checks = []
    registrations = []

    def stop_requested():
        checks.append(True)
        if len(checks) <= 2:
            raise _disk_full()
        return False

    async def ensure_registered():
        registrations.append(True)
        if len(registrations) == 1:
            raise OperationalError(
                "INSERT", {}, Exception("database or disk is full")
            )

    monkeypatch.setattr(owner, "stop_requested", stop_requested)
    monkeypatch.setattr(backend, "ensure_registered", ensure_registered)
    # Three consecutive failures (two checks + one registration) stay
    # below the bound.
    monkeypatch.setattr(backend, "MAX_MAINTENANCE_FAILURES", 4)
    task = asyncio.create_task(backend._maintain())
    try:
        while len(registrations) < 3:
            assert not task.done(), task
            await asyncio.sleep(0.05)
        assert backend.failed is False
        assert shutdown == []
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        owner.release(clean=True)


@pytest.mark.req("NODE-10")
async def test_persistent_stop_request_disk_error_ends_node(tmp_path, monkeypatch):
    from anygarden.node import execution

    backend, owner, shutdown = _bare_backend(tmp_path)
    monkeypatch.setattr(execution, "log", _FullDiskLog())

    def stop_requested():
        raise _disk_full()

    monkeypatch.setattr(owner, "stop_requested", stop_requested)
    monkeypatch.setattr(backend, "ensure_registered", AsyncMock())
    monkeypatch.setattr(backend, "MAX_MAINTENANCE_FAILURES", 3)
    with pytest.raises(OSError):
        await asyncio.wait_for(backend._maintain(), 5)
    assert backend.failed is True
    # Even though every log write fails, the shutdown is still requested.
    assert shutdown == [True]
    owner.release(clean=True)


@pytest.mark.req("NODE-10")
async def test_persistent_frame_failures_end_node(tmp_path, monkeypatch):
    from anygarden.node import execution

    backend, owner, shutdown = _bare_backend(tmp_path)
    monkeypatch.setattr(execution, "log", _FullDiskLog())

    async def handle(frame):
        raise _disk_full()

    monkeypatch.setattr(backend.daemon, "_handle", handle)
    monkeypatch.setattr(backend, "MAX_FRAME_FAILURES", 3)
    task = asyncio.create_task(backend._consume())
    for n in range(3):
        backend.queue.put_nowait({"type": "sync_desired_state", "n": n})
    with pytest.raises(OSError):
        await asyncio.wait_for(task, 2)
    assert backend.failed is True
    assert shutdown == [True]
    assert await backend.send({"type": "spawn_agent"}) is False
    owner.release(clean=True)


@pytest.mark.req("NODE-10")
async def test_frame_failure_count_resets_after_success(tmp_path, monkeypatch):
    backend, owner, shutdown = _bare_backend(tmp_path)

    async def handle(frame):
        if frame["n"] % 3 != 2:
            raise _disk_full()

    monkeypatch.setattr(backend.daemon, "_handle", handle)
    monkeypatch.setattr(backend, "MAX_FRAME_FAILURES", 3)
    task = asyncio.create_task(backend._consume())
    try:
        # fail, fail, ok, fail, fail, ok — never three in a row.
        for n in range(6):
            backend.queue.put_nowait({"type": "sync_desired_state", "n": n})
        await asyncio.wait_for(backend.queue.join(), 2)
        assert backend.failed is False and shutdown == []
    finally:
        backend.queue.put_nowait(None)
        await asyncio.wait_for(task, 2)
        owner.release(clean=True)


@pytest.mark.req("NODE-10")
async def test_shutdown_requested_even_when_failure_log_raises(tmp_path, monkeypatch):
    from anygarden.node import execution

    backend, owner, shutdown = _bare_backend(tmp_path)
    monkeypatch.setattr(execution, "log", _FullDiskLog())
    backend._failed(NodeOwnershipError("mapping mismatch"))
    assert backend.failed is True
    assert shutdown == [True]
    owner.release(clean=True)


@pytest.mark.req("NODE-10")
@pytest.mark.parametrize("failed", [False, True])
async def test_healthz_reports_failed_local_execution(tmp_path, failed):
    import secrets

    import httpx

    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="WARNING",
    )
    app = create_app(config)
    engine = build_engine(config.db_url)
    app.state.session_factory = build_session_factory(engine)
    app.state.local_execution = SimpleNamespace(failed=failed)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://node"
        ) as client:
            response = await client.get("/healthz")
        body = response.json()
        if failed:
            assert response.status_code == 503
            assert body["status"] == "unhealthy"
            assert body["components"]["local_execution"] == "unhealthy"
        else:
            assert response.status_code == 200
            assert body["components"]["local_execution"] == "ok"
    finally:
        await engine.dispose()


@pytest.mark.req("NODE-10")
def test_real_cli_exits_on_its_own_when_local_execution_fails(tmp_path):
    """The shutdown callback must really stop the process so a supervisor
    restarts it — not leave the API serving with execution gone. Mirrors the
    benchmark: disk full for both the stop-request check and stdout logging."""
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
    marker = tmp_path / "fail-now"
    script = f"""
import errno, pathlib
from anygarden.node.execution import LocalExecutionBackend
from anygarden.node.ownership import NodeOwner
from anygarden.cli import dispatch

original = NodeOwner.stop_requested
def stop_requested(self):
    if pathlib.Path({str(marker)!r}).exists():
        raise OSError(errno.ENOSPC, "No space left on device")
    return original(self)
NodeOwner.stop_requested = stop_requested

# The disk is briefly full for stdout too: structlog's print(..., flush=True)
# raises for the first writes after the outage starts (the maintenance
# warnings and the failure report), then space is back for shutdown.
import structlog
original_msg = structlog.PrintLogger.msg
failed_writes = []
def msg(self, message):
    if pathlib.Path({str(marker)!r}).exists() and len(failed_writes) < 3:
        failed_writes.append(message)
        raise OSError(errno.ENOSPC, "No space left on device")
    return original_msg(self, message)
for name in ("msg", "log", "debug", "info", "warn", "warning", "error", "err",
             "fatal", "exception", "failure", "critical"):
    if hasattr(structlog.PrintLogger, name):
        setattr(structlog.PrintLogger, name, msg)
LocalExecutionBackend.MAX_MAINTENANCE_FAILURES = 3
dispatch()
"""
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", script, "start", "--data-dir",
             str(tmp_path / "node"), "--port", str(port)],
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
            marker.touch()
            exited = process.wait(timeout=20)
            output = (tmp_path / "server.log").read_text()
            assert exited != 0, output
            assert "Local execution failed" in output
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.req("NODE-10")
def test_real_cli_shuts_down_cleanly_and_restarts_while_disk_stays_full(tmp_path):
    """A full disk that outlasts the shutdown must not make it recovery-required.

    #821 made the execution backend's error-path logging best effort, but
    log calls on the cleanup path (e.g. the spawner's ``drain_complete``)
    still raised ``OSError(ENOSPC)``; the lifespan then saw an unconfirmed
    cleanup and refused the next supervisor restart until manual recovery.
    Here every structlog stdout write fails from just before ``stop`` until
    the process exits.
    """
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
    marker = tmp_path / "disk-full"
    data_dir = tmp_path / "node"
    full_disk_script = f"""
import errno, pathlib
import structlog
from anygarden.cli import dispatch

marker = pathlib.Path({str(marker)!r})
original_msg = structlog.PrintLogger.msg
writes = []
def msg(self, message):
    if marker.exists():
        writes.append(message)
        pathlib.Path({str(tmp_path / "failed-writes")!r}).write_text(str(len(writes)))
        raise OSError(errno.ENOSPC, "No space left on device")
    return original_msg(self, message)
for name in ("msg", "log", "debug", "info", "warn", "warning", "error", "err",
             "fatal", "exception", "failure", "critical"):
    if hasattr(structlog.PrintLogger, name):
        setattr(structlog.PrintLogger, name, msg)
dispatch()
"""
    command = [sys.executable, "-c", "from anygarden.cli import dispatch; dispatch()"]

    def wait_healthy(process, log_path):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            assert process.poll() is None, log_path.read_text()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/healthz", timeout=0.2
                ) as response:
                    assert response.status == 200
                    return
            except OSError:
                time.sleep(0.05)
        pytest.fail("CLI startup timed out")

    first_log = tmp_path / "first.log"
    with first_log.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", full_disk_script, "start", "--data-dir",
             str(data_dir), "--port", str(port)],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_healthy(process, first_log)
            marker.touch()
            stopped = subprocess.run(
                command + ["stop", "--data-dir", str(data_dir)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            exited = process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    output = first_log.read_text()
    # The disk really was full for the shutdown's log writes.
    assert int((tmp_path / "failed-writes").read_text()) > 0
    assert "Node shutdown failed" not in output, output
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr + output
    assert exited == 0, output
    assert read_object(data_dir / "node-owner.json")["state"] == "stopped"

    # The next (supervisor) start on the same data dir succeeds.
    second_log = tmp_path / "second.log"
    with second_log.open("w") as log:
        process = subprocess.Popen(
            command + ["start", "--data-dir", str(data_dir), "--port", str(port)],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_healthy(process, second_log)
            stopped = subprocess.run(
                command + ["stop", "--data-dir", str(data_dir)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert stopped.returncode == 0, stopped.stdout + stopped.stderr
            assert process.wait(timeout=15) == 0, second_log.read_text()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.req("NODE-10")
def test_configured_structlog_writes_are_best_effort():
    import errno

    from anygarden.observability.logging import BestEffortPrintLoggerFactory

    class FullDisk:
        def write(self, _text):
            raise OSError(errno.ENOSPC, "No space left on device")

        def flush(self):
            raise OSError(errno.ENOSPC, "No space left on device")

    logger = BestEffortPrintLoggerFactory(FullDisk())()
    for method in ("info", "warning", "error", "exception", "critical", "msg"):
        getattr(logger, method)("drain_complete")  # must not raise


@pytest.mark.req("NODE-10")
@pytest.mark.parametrize("cleanup_fails", [False, True], ids=["clean", "cleanup-fails"])
def test_real_cli_exit_status_survives_unflushable_stdout(tmp_path, cleanup_fails):
    """A full disk at exit must not turn a clean shutdown into exit code 120.

    With stdout/stderr redirected to a file on a full disk, writes that failed
    (best effort since #822) stay in the stream buffers. Python's final
    flush at interpreter exit then fails and the process exits 120 although
    shutdown succeeded. The disk filling is modelled by pointing fds 1/2 at
    ``/dev/full`` (every write fails with ENOSPC) just before ``stop``, with
    bytes still buffered. The exit status must reflect the real shutdown:
    0 when cleanup was confirmed, non-zero when it genuinely failed.
    """
    import os
    import socket
    import time
    import urllib.request

    if not os.path.exists("/dev/full"):
        pytest.skip("needs /dev/full to model a full disk")
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
    marker = tmp_path / "disk-full"
    filled = tmp_path / "disk-filled"
    data_dir = tmp_path / "node"
    script = f"""
import os, pathlib, sys, threading, time
from anygarden.cli import dispatch

marker = pathlib.Path({str(marker)!r})
filled = pathlib.Path({str(filled)!r})

def fill_disk():
    while not marker.exists():
        time.sleep(0.01)
    full = os.open("/dev/full", os.O_WRONLY)
    os.dup2(full, 1)
    os.dup2(full, 2)
    os.close(full)
    # Bytes the process still holds when the disk is full: block-buffered
    # stdout keeps them until the interpreter's final flush.
    sys.stdout.write("pending log tail\\n")
    filled.touch()

threading.Thread(target=fill_disk, daemon=True).start()
if {cleanup_fails!r}:
    from anygarden.node.execution import LocalExecutionBackend
    original_close = LocalExecutionBackend.close
    async def close(self):
        await original_close(self)
        raise RuntimeError("child cleanup unconfirmed")
    LocalExecutionBackend.close = close
dispatch()
"""
    command = [sys.executable, "-c", "from anygarden.cli import dispatch; dispatch()"]
    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", script, "start", "--data-dir",
             str(data_dir), "--port", str(port)],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert process.poll() is None, log_path.read_text()
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
            marker.touch()
            deadline = time.monotonic() + 5
            while not filled.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert filled.exists(), "disk-full injection did not run"
            stopped = subprocess.run(
                command + ["stop", "--data-dir", str(data_dir), "--timeout", "15"],
                check=False,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            exited = process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    output = log_path.read_text()
    if cleanup_fails:
        # The click failure status, not the interpreter's flush-failure 120.
        assert exited == 1, output
        assert read_object(data_dir / "node-owner.json")["state"] != "stopped"
    else:
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr + output
        assert exited == 0, output
        assert read_object(data_dir / "node-owner.json")["state"] == "stopped"


@pytest.mark.req("NODE-10")
def test_machine_daemon_clean_stop_exits_zero_with_unflushable_stdout(tmp_path):
    """The standalone machine daemon has the same exit shape as the node:
    a clean stop on a full disk must exit 0, not the flush-failure 120."""
    import os

    if not os.path.exists("/dev/full"):
        pytest.skip("needs /dev/full to model a full disk")
    script = """
import os, sys
from anygarden.machine import cli
from anygarden.machine.daemon import MachineDaemon

async def run(self):
    # The disk fills while the daemon runs; its last log bytes stay buffered.
    print("daemon still running")
    full = os.open("/dev/full", os.O_WRONLY)
    os.dup2(full, 1)
    os.dup2(full, 2)
    os.close(full)
    sys.stdout.write("pending log tail\\n")
MachineDaemon.run = run
cli.main()
"""
    environment = {**os.environ, "HOME": str(tmp_path)}
    with (tmp_path / "daemon.log").open("w") as log:
        exited = subprocess.run(
            [sys.executable, "-c", script, "run", "--server", "http://127.0.0.1:9",
             "--token", "t", "--machine-id", "m"],
            check=False,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=30,
        ).returncode
    assert exited == 0, (tmp_path / "daemon.log").read_text()
