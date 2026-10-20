"""#915 — work files follow an agent to its new machine (two real daemons)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from anygarden.db.models import ActivityLog, Agent, Machine, MachineEngine
from anygarden.machine import workspace_import
from anygarden.machine.managed_workspace import CAPABILITY, EDIT_CAPABILITY, FILES_CAPABILITY, supported
from anygarden.machine.spawner import SpawnManifest
from anygarden.machine.workspace_transfer import CAPABILITY as TRANSFER_CAPABILITY
from anygarden.node.execution import LocalExecutionBackend

from .test_managed_workspace_api import _AppTransport, seed
from .test_managed_workspace_api import workspace_env as _workspace_env

move_env = _workspace_env
pytestmark = pytest.mark.skipif(not supported(), reason="POSIX descriptor boundary")
CAPS = [CAPABILITY, EDIT_CAPABILITY, FILES_CAPABILITY, TRANSFER_CAPABILITY, "pi_native_auth_v1"]


async def _two_machines(env, tmp_path, target_caps=CAPS):
    env["app"].state.config.transfer_spool_dir = tmp_path / "spool"
    source_id, root = await seed(env, tmp_path)
    async with env["factory"]() as db:
        source = await db.get(Machine, source_id)
        source.control_capabilities = CAPS
        target = Machine(name="new-machine", hostname="new", owner_user_id=env["admin"].id,
                         status="online", max_agents=5, control_capabilities=target_caps)
        db.add(target)
        await db.flush()
        db.add(MachineEngine(machine_id=target.id, engine="pi-cli"))
        await db.commit()
        target_id = target.id
    bus = env["bus"]
    await bus.unregister(source_id)
    backends = []
    for machine_id, data_dir in ((source_id, tmp_path), (target_id, tmp_path / "new")):
        data_dir.mkdir(exist_ok=True)
        backend = LocalExecutionBackend(env["app"], SimpleNamespace(identity={"machine_id": machine_id}, data_dir=data_dir))
        backend.daemon._transfer_client_factory = _AppTransport(env["client"], asyncio.get_running_loop()).factory
        await bus.register_local(machine_id, backend)
        backends.append((machine_id, backend, asyncio.create_task(backend._consume())))
    return source_id, target_id, root, backends


async def _close(env, backends):
    for machine_id, backend, task in backends:
        await backend.queue.put(None)
        await task
        await env["bus"].unregister_local(machine_id, backend)


@pytest.mark.req("AGT-12")
@pytest.mark.parametrize("move_env", ["file"], indirect=True)
async def test_work_files_follow_the_agent(move_env, tmp_path):
    env = move_env
    source_id, target_id, root, backends = await _two_machines(env, tmp_path)
    (root / "report.md").write_text("result")
    (root / "AGENTS.md").write_text("instructions travel separately")
    (root / "memory").mkdir()
    (root / "memory" / "notes.md").write_text("memory travels separately")
    headers = {"Authorization": f"Bearer {env['token']}"}
    try:
        preview = (await env["client"].get("/api/v1/agents/workspace-agent/machine/move-preview",
                                           params={"machine_id": target_id}, headers=headers)).json()
        assert preview["can_move"] is True
        assert (preview["files"], preview["bytes"]) == (2, len("actual runtime output") + len("result"))

        moved = await env["client"].put("/api/v1/agents/workspace-agent/machine",
                                        json={"machine_id": target_id, "move_files": True}, headers=headers)
        assert moved.status_code == 200, moved.text
        assert moved.json()["placed_on_machine_id"] == target_id
    finally:
        await _close(env, backends)

    new_root = tmp_path / "new" / "agents" / "workspace-agent"
    assert workspace_import.import_zip_path(new_root).is_file()
    # The new machine unpacks it right before the agent starts there.
    _, target_backend, _ = backends[1]
    target_backend.daemon._spawner._materialize_agent_dir(
        SpawnManifest(agent_id="workspace-agent", engine="pi-cli", agent_token="t", rooms=[]))
    assert (new_root / "report.md").read_text() == "result"
    assert (new_root / "folder" / "result.md").read_text() == "actual runtime output"
    assert not (new_root / "memory" / "notes.md").exists()
    # The old machine keeps its copy.
    assert (root / "report.md").exists()
    assert list((tmp_path / "spool").iterdir()) == []
    async with env["factory"]() as db:
        log = (await db.scalars(select(ActivityLog).where(ActivityLog.event_type == "agent_machine_changed"))).one()
        assert log.details["moved_files"]["count"] == 2
        assert log.details["moved_files"]["skipped"] == []
        assert log.details["moved_files"]["archive_bytes"] > 0


@pytest.mark.req("AGT-12")
@pytest.mark.parametrize("move_env", ["file"], indirect=True)
async def test_a_failed_copy_keeps_the_agent_in_place(move_env, tmp_path):
    env = move_env
    source_id, target_id, root, backends = await _two_machines(
        env, tmp_path, target_caps=[c for c in CAPS if c != TRANSFER_CAPABILITY])
    headers = {"Authorization": f"Bearer {env['token']}"}
    try:
        preview = (await env["client"].get("/api/v1/agents/workspace-agent/machine/move-preview",
                                           params={"machine_id": target_id}, headers=headers)).json()
        assert preview == {"can_move": False, "reason": "target_unsupported", "files": 0, "bytes": 0,
                           "limit_bytes": 200 * 1024 * 1024}
        refused = await env["client"].put("/api/v1/agents/workspace-agent/machine",
                                          json={"machine_id": target_id, "move_files": True}, headers=headers)
        assert refused.status_code == 409
        async with env["factory"]() as db:
            assert (await db.get(Agent, "workspace-agent")).placed_on_machine_id == source_id
        # Without copying, the move itself still works.
        plain = await env["client"].put("/api/v1/agents/workspace-agent/machine",
                                        json={"machine_id": target_id}, headers=headers)
        assert plain.status_code == 200
    finally:
        await _close(env, backends)


@pytest.mark.parametrize("move_env", ["file"], indirect=True)
async def test_preview_reports_nothing_to_move_for_an_agent_that_never_ran(move_env, tmp_path):
    env = move_env
    source_id, target_id, root, backends = await _two_machines(env, tmp_path)
    (root / ".anygarden-workspace.json").unlink()
    try:
        preview = (await env["client"].get("/api/v1/agents/workspace-agent/machine/move-preview",
                                           params={"machine_id": target_id},
                                           headers={"Authorization": f"Bearer {env['token']}"})).json()
        assert (preview["can_move"], preview["reason"]) == (False, "nothing")
    finally:
        await _close(env, backends)


@pytest.mark.parametrize("move_env", ["file"], indirect=True)
async def test_existing_names_on_the_new_machine_are_kept(move_env, tmp_path):
    env = move_env
    source_id, target_id, root, backends = await _two_machines(env, tmp_path)
    (root / "report.md").write_text("from the old machine")
    new_root = tmp_path / "new" / "agents" / "workspace-agent"
    new_root.mkdir(parents=True)
    (new_root / "report.md").write_text("already on the new machine")
    try:
        moved = await env["client"].put("/api/v1/agents/workspace-agent/machine",
                                        json={"machine_id": target_id, "move_files": True},
                                        headers={"Authorization": f"Bearer {env['token']}"})
        assert moved.status_code == 200, moved.text
    finally:
        await _close(env, backends)
    backends[1][1].daemon._spawner._materialize_agent_dir(
        SpawnManifest(agent_id="workspace-agent", engine="pi-cli", agent_token="t", rooms=[]))
    assert (new_root / "report.md").read_text() == "already on the new machine"
    async with env["factory"]() as db:
        log = (await db.scalars(select(ActivityLog).where(ActivityLog.event_type == "agent_machine_changed"))).one()
        assert log.details["moved_files"]["skipped"] == ["report.md"]


@pytest.mark.parametrize("move_env", ["file"], indirect=True)
async def test_a_running_agent_is_stopped_first_or_left_in_place(move_env, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from anygarden.api.v1 import agents as agents_api

    env = move_env
    source_id, target_id, root, backends = await _two_machines(env, tmp_path)
    async with env["factory"]() as db:
        agent = await db.get(Agent, "workspace-agent")
        agent.desired_state = "running"
        agent.actual_state = "running"
        await db.commit()
    lifecycle = env["lifecycle"]
    lifecycle.request_stop = AsyncMock()  # the old machine never confirms the stop
    lifecycle.request_start = AsyncMock()
    original = agents_api._wait_until_stopped
    monkeypatch.setattr(agents_api, "_wait_until_stopped",
                        lambda request, agent_id: original(request, agent_id, timeout=0.3))
    headers = {"Authorization": f"Bearer {env['token']}"}
    try:
        refused = await env["client"].put("/api/v1/agents/workspace-agent/machine",
                                          json={"machine_id": target_id, "move_files": True}, headers=headers)
        assert refused.status_code == 409
        lifecycle.request_start.assert_awaited_once_with("workspace-agent")
        async with env["factory"]() as db:
            assert (await db.get(Agent, "workspace-agent")).placed_on_machine_id == source_id

        async def stop(agent_id):
            async with env["factory"]() as db:
                (await db.get(Agent, agent_id)).actual_state = "stopped"
                await db.commit()

        lifecycle.request_stop = AsyncMock(side_effect=stop)
        lifecycle.request_start = AsyncMock()
        moved = await env["client"].put("/api/v1/agents/workspace-agent/machine",
                                        json={"machine_id": target_id, "move_files": True}, headers=headers)
        assert moved.status_code == 200, moved.text
        lifecycle.request_start.assert_awaited_once_with("workspace-agent")
        assert workspace_import.import_zip_path(tmp_path / "new" / "agents" / "workspace-agent").is_file()
    finally:
        await _close(env, backends)
