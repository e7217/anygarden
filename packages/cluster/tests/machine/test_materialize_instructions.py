"""#908 — the materializer keeps disk-sourced instructions as the source of truth."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from anygarden.machine import instructions as ins
from anygarden.machine.spawner import SpawnManifest, Spawner

ROOM_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def spawner(tmp_path: Path) -> Spawner:
    return Spawner(on_stopped=AsyncMock(), on_crashed=AsyncMock(), agent_dirs_root=tmp_path / "agents")


def _msg(revision: int | None = 1, agents_md: str | None = "server rules",
         files: dict[str, str] | None = None) -> SpawnManifest:
    return SpawnManifest(
        agent_id="agent-x", engine="codex-cli", agent_token="tok", rooms=[ROOM_ID],
        room_memories={ROOM_ID: {"room_id": ROOM_ID, "memory_md": "", "revision": 0,
            "session_epoch": 0, "generation": 0, "scope_version": "room-memory-v1",
            "ephemeral": False}},
        agents_md=agents_md, files=files or {}, instructions_revision=revision,
    )


def _agents_md(root: Path) -> str:
    return (root / "AGENTS.md").read_text()


def test_fresh_directory_is_seeded_from_the_snapshot(spawner: Spawner) -> None:
    root = spawner._materialize_agent_dir(_msg(files={"skills/a/SKILL.md": "# A skill"}))

    text = _agents_md(root)
    assert ins.split_user_area(text) == "server rules"
    assert "# A skill" in text.split(ins.MANAGED_START)[1]
    assert ins.read_receipt(root) == ins.Receipt(1, ins.collect(root).digest())


def test_disk_edits_survive_a_restart(spawner: Spawner) -> None:
    root = spawner._materialize_agent_dir(_msg())
    (root / "AGENTS.md").write_text(ins.compose("agent edited", "stale block"))
    (root / "skills" / "mine").mkdir()
    (root / "skills" / "mine" / "SKILL.md").write_text("# Mine")

    spawner._materialize_agent_dir(_msg())

    text = _agents_md(root)
    assert ins.split_user_area(text) == "agent edited"
    assert "stale block" not in text and "# Mine" in text
    # The receipt still records the last synced content, so the daemon
    # reports the local edit.
    assert ins.read_receipt(root).digest != ins.collect(root).digest()


def test_upgrade_from_a_rendered_file_keeps_disk_skills(spawner: Spawner) -> None:
    root = spawner._materialize_agent_dir(_msg(revision=None, files={"skills/a/SKILL.md": "A"}))
    (root / "skills" / "b").mkdir()
    (root / "skills" / "b" / "SKILL.md").write_text("B")
    assert ins.read_receipt(root) is None

    spawner._materialize_agent_dir(_msg(revision=0, files={"skills/a/SKILL.md": "A"}))

    assert ins.split_user_area(_agents_md(root)) == "server rules"
    assert set(ins.collect(root).skills) == {"skills/a/SKILL.md", "skills/b/SKILL.md"}
    assert any((root / ins.ARCHIVE_DIR).iterdir())
    receipt = ins.read_receipt(root)
    assert receipt.revision == 0 and receipt.digest != ins.collect(root).digest()


def test_newer_server_revision_replaces_disk_and_archives_local_edits(spawner: Spawner) -> None:
    root = spawner._materialize_agent_dir(_msg(revision=1, files={"skills/a/SKILL.md": "A"}))
    (root / "AGENTS.md").write_text(ins.compose("unsynced local edit", ""))

    spawner._materialize_agent_dir(_msg(revision=4, agents_md="restored", files={"skills/c/SKILL.md": "C"}))

    assert ins.split_user_area(_agents_md(root)) == "restored"
    assert set(ins.collect(root).skills) == {"skills/c/SKILL.md"}
    assert ins.read_receipt(root).revision == 4
    archived = list((root / ins.ARCHIVE_DIR).iterdir())
    assert any("unsynced local edit" in (path / "AGENTS.md").read_text() for path in archived)


def test_newer_server_revision_without_local_edits_archives_nothing(spawner: Spawner) -> None:
    root = spawner._materialize_agent_dir(_msg(revision=1))
    spawner._materialize_agent_dir(_msg(revision=2, agents_md="next"))
    assert ins.split_user_area(_agents_md(root)) == "next"
    assert not (root / ins.ARCHIVE_DIR).exists()


def test_legacy_manifest_keeps_overwriting(spawner: Spawner) -> None:
    root = spawner._materialize_agent_dir(_msg(revision=None, agents_md="v1"))
    (root / "AGENTS.md").write_text("local")
    spawner._materialize_agent_dir(_msg(revision=None, agents_md="v2"))
    assert _agents_md(root).startswith("v2")
    assert ins.MANAGED_START not in _agents_md(root)
