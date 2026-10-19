"""#908 — the daemon syncs disk-sourced instructions with the server."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from anygarden.machine import instructions as ins
from anygarden.machine.daemon import MachineDaemon
from anygarden.machine.protocol.frames import (
    AgentInstructionsAckFrame,
    AgentInstructionsRequestFrame,
    SyncDesiredStateFrame,
)
from anygarden.machine.spawner import SpawnManifest

AGENT = "agent-ins"
ROOM_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def daemon(tmp_path: Path) -> MachineDaemon:
    daemon = MachineDaemon(
        server_url="wss://localhost:8000/ws/machines/m1",
        machine_id="m1",
        machine_token="t",
        agent_dirs_root=tmp_path / "agents",
        workspace_registry_path=tmp_path / "workspaces.json",
        workspace_signing_key_path=tmp_path / "workspace-signing.key",
    )
    daemon._manifest_store.save_if_authoritative(SyncDesiredStateFrame(
        agent_id=AGENT, desired_state="running", generation=2, engine="codex-cli",
        rooms=[ROOM_ID], agents_md="server rules", instructions_revision=1,
    ))
    daemon._spawner._materialize_agent_dir(SpawnManifest(
        agent_id=AGENT, engine="codex-cli", agent_token="tok", rooms=[ROOM_ID],
        agents_md="server rules", files={"skills/a/SKILL.md": "A"}, instructions_revision=1,
    ))
    daemon._spawner.list_running = lambda: [{"agent_id": AGENT}]  # type: ignore[method-assign]
    return daemon


def _sent(daemon: MachineDaemon) -> list[dict]:
    frames: list[dict] = []
    ws = AsyncMock()
    ws.send = AsyncMock(side_effect=lambda data: frames.append(json.loads(data)))
    daemon._ws = ws
    return frames


def _root(daemon: MachineDaemon) -> Path:
    return daemon._spawner.get_agent_root(AGENT)


@pytest.mark.req("AGT-11")
@pytest.mark.asyncio
async def test_agent_edit_is_reported_once_and_recorded_on_ack(daemon: MachineDaemon) -> None:
    frames = _sent(daemon)
    await daemon._flush_instruction_updates()
    assert frames == []

    (_root(daemon) / "AGENTS.md").write_text(ins.compose("agent rule", "block"))
    await daemon._flush_instruction_updates()
    await daemon._flush_instruction_updates()

    assert [f["type"] for f in frames] == ["agent_instructions_update"]
    update = frames[0]
    assert update["base_revision"] == 1 and update["generation"] == 2
    assert update["snapshot"] == {"agents_md": "agent rule", "skills": {"skills/a/SKILL.md": "A"}}

    await daemon._handle_instructions_ack(AgentInstructionsAckFrame(
        agent_id=AGENT, status="accepted", revision=2, digest=update["digest"]))
    assert ins.read_receipt(_root(daemon)) == ins.Receipt(2, update["digest"])
    await daemon._flush_instruction_updates()
    assert len(frames) == 1


@pytest.mark.asyncio
async def test_rejected_ack_installs_the_server_copy(daemon: MachineDaemon) -> None:
    (_root(daemon) / "AGENTS.md").write_text(ins.compose("local", ""))
    await daemon._handle_instructions_ack(AgentInstructionsAckFrame(
        agent_id=AGENT, status="rejected", revision=5, digest="x",
        snapshot={"agents_md": "server", "skills": {"skills/z/SKILL.md": "Z"}}))

    root = _root(daemon)
    assert ins.collect(root) == ins.InstructionSnapshot.of("server", {"skills/z/SKILL.md": "Z"})
    assert "Z" in (root / "AGENTS.md").read_text().split(ins.MANAGED_START)[1]
    assert ins.read_receipt(root).revision == 5
    assert any("local" in (p / "AGENTS.md").read_text() for p in (root / ins.ARCHIVE_DIR).iterdir())


def _request(**values) -> AgentInstructionsRequestFrame:
    return AgentInstructionsRequestFrame(request_id="r1", agent_id=AGENT, base_revision=1, **values)


@pytest.mark.req("AGT-11")
@pytest.mark.asyncio
async def test_person_edit_writes_disk_and_is_not_reported_twice(daemon: MachineDaemon) -> None:
    frames = _sent(daemon)
    result = await daemon._apply_instructions_request(_request(operation="write_agents_md", text="person rule"))

    assert result.status == "ok" and result.base_revision == 1
    assert result.snapshot.agents_md == "person rule"
    assert ins.split_user_area((_root(daemon) / "AGENTS.md").read_text()) == "person rule"
    await daemon._flush_instruction_updates()
    assert frames == []


@pytest.mark.asyncio
async def test_skill_write_and_delete(daemon: MachineDaemon) -> None:
    written = await daemon._apply_instructions_request(
        _request(operation="write_skill", path="skills/b/SKILL.md", text="# B"))
    assert written.status == "ok"
    assert set(written.snapshot.skills) == {"skills/a/SKILL.md", "skills/b/SKILL.md"}
    assert "# B" in (_root(daemon) / "AGENTS.md").read_text()

    daemon._instructions_pending.clear()
    ins.write_receipt(_root(daemon), ins.Receipt(1, written.digest))
    deleted = await daemon._apply_instructions_request(
        _request(operation="delete_skill", path="skills/b/SKILL.md"))
    assert deleted.status == "ok"
    assert set(deleted.snapshot.skills) == {"skills/a/SKILL.md"}
    assert not (_root(daemon) / "skills" / "b").exists()


@pytest.mark.asyncio
async def test_request_refusals(daemon: MachineDaemon) -> None:
    outside = await daemon._apply_instructions_request(
        _request(operation="write_skill", path=".codex/config.toml", text="x"))
    assert outside.status == "invalid"
    traversal = await daemon._apply_instructions_request(
        _request(operation="write_skill", path="skills/../AGENTS.md", text="x"))
    assert traversal.status == "invalid"

    (_root(daemon) / "AGENTS.md").write_text(ins.compose("unsynced agent edit", ""))
    conflict = await daemon._apply_instructions_request(_request(operation="write_agents_md", text="p"))
    assert conflict.status == "conflict"
    assert ins.split_user_area((_root(daemon) / "AGENTS.md").read_text()) == "unsynced agent edit"

    stale = await daemon._apply_instructions_request(
        AgentInstructionsRequestFrame(request_id="r2", agent_id=AGENT, base_revision=0,
                                      operation="write_agents_md", text="p"))
    assert stale.status == "conflict"

    missing = await daemon._apply_instructions_request(
        AgentInstructionsRequestFrame(request_id="r3", agent_id="never-spawned", base_revision=0,
                                      operation="write_agents_md", text="p"))
    assert missing.status == "no_receipt"


@pytest.mark.asyncio
async def test_register_advertises_the_capability(daemon: MachineDaemon) -> None:
    frames = _sent(daemon)
    await daemon._register()
    register = next(f for f in frames if f["type"] == "register")
    assert ins.CAPABILITY in register["control_capabilities"]
