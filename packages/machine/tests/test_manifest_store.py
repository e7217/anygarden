"""Tests for ManifestStore — local manifest persistence layer.

Each agent's manifest is stored under:
    <agents_root>/<agent_id>/manifest.json

The file contains all SyncDesiredStateFrame fields EXCEPT type and
engine_secrets. A saved_at ISO timestamp is added.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from doorae_machine.manifest_store import ManifestStore
from doorae_machine.protocol.frames import SyncDesiredStateFrame


# ── Helpers ───────────────────────────────────────────────────────────

def make_frame(
    agent_id: str = "agent-001",
    desired_state: str = "running",
    generation: int = 1,
    engine: str = "claude-code",
    engine_secrets: dict | None = None,
) -> SyncDesiredStateFrame:
    return SyncDesiredStateFrame(
        agent_id=agent_id,
        desired_state=desired_state,  # type: ignore[arg-type]
        generation=generation,
        engine=engine,
        name="test-agent",
        profile_yaml="name: test-agent\nmodel: claude-3",
        rooms=["room-1", "room-2"],
        agents_md="# Agents\nHello",
        files={"skills/tool/SKILL.md": "content"},
        engine_secrets=engine_secrets or {"API_KEY": "secret-value"},
        reasoning_effort="medium",
        sub_rooms=[{"name": "sub-room", "description": "a sub room"}],
        restart_policy="restart_on_same_machine",
        max_restarts=5,
        restart_window_seconds=600,
    )


# ── Test class ────────────────────────────────────────────────────────

class TestManifestStoreSave:
    """save() creates manifest.json with correct content."""

    def test_save_creates_manifest_json(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        frame = make_frame()

        path = store.save(frame)

        assert path.exists()
        assert path.name == "manifest.json"
        assert path.parent == tmp_path / "agent-001"

    def test_save_excludes_engine_secrets(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        frame = make_frame(engine_secrets={"MY_KEY": "do-not-store"})

        store.save(frame)

        data = json.loads((tmp_path / "agent-001" / "manifest.json").read_text())
        assert "engine_secrets" not in data

    def test_save_excludes_type_field(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        frame = make_frame()

        store.save(frame)

        data = json.loads((tmp_path / "agent-001" / "manifest.json").read_text())
        assert "type" not in data

    def test_save_adds_saved_at_timestamp(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        frame = make_frame()

        store.save(frame)

        data = json.loads((tmp_path / "agent-001" / "manifest.json").read_text())
        assert "saved_at" in data
        # Should be a valid ISO 8601 string
        from datetime import datetime
        dt = datetime.fromisoformat(data["saved_at"])
        assert dt is not None

    def test_save_preserves_all_other_fields(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        frame = make_frame()

        store.save(frame)

        data = json.loads((tmp_path / "agent-001" / "manifest.json").read_text())
        assert data["agent_id"] == "agent-001"
        assert data["desired_state"] == "running"
        assert data["generation"] == 1
        assert data["engine"] == "claude-code"
        assert data["name"] == "test-agent"
        assert data["rooms"] == ["room-1", "room-2"]
        assert data["agents_md"] == "# Agents\nHello"
        assert data["files"] == {"skills/tool/SKILL.md": "content"}
        assert data["reasoning_effort"] == "medium"
        assert data["max_restarts"] == 5
        assert data["restart_window_seconds"] == 600
        assert data["restart_policy"] == "restart_on_same_machine"

    def test_save_overwrites_existing_manifest(self, tmp_path: Path) -> None:
        """Saving again with updated generation replaces the file."""
        store = ManifestStore(agents_root=tmp_path)
        frame_v1 = make_frame(generation=1)
        frame_v2 = make_frame(generation=2)

        store.save(frame_v1)
        store.save(frame_v2)

        data = json.loads((tmp_path / "agent-001" / "manifest.json").read_text())
        assert data["generation"] == 2

    def test_save_file_permissions_0o600(self, tmp_path: Path) -> None:
        """manifest.json must be owner-readable only (mode 0o600)."""
        store = ManifestStore(agents_root=tmp_path)
        frame = make_frame()

        path = store.save(frame)

        file_mode = stat.S_IMODE(path.stat().st_mode)
        assert file_mode == 0o600


class TestManifestStoreLoad:
    """load() reads a saved manifest back as SyncDesiredStateFrame."""

    def test_load_returns_correct_frame(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        frame = make_frame()
        store.save(frame)

        loaded = store.load("agent-001")

        assert loaded is not None
        assert loaded.agent_id == "agent-001"
        assert loaded.desired_state == "running"
        assert loaded.generation == 1
        assert loaded.engine == "claude-code"
        assert loaded.rooms == ["room-1", "room-2"]

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)

        result = store.load("no-such-agent")

        assert result is None

    def test_load_corrupt_returns_none(self, tmp_path: Path) -> None:
        """A corrupt JSON file should be handled gracefully."""
        agent_dir = tmp_path / "agent-bad"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.json"
        manifest.write_text("{ not valid json }", encoding="utf-8")

        store = ManifestStore(agents_root=tmp_path)
        result = store.load("agent-bad")

        assert result is None

    def test_load_invalid_schema_returns_none(self, tmp_path: Path) -> None:
        """Valid JSON but invalid schema should return None."""
        agent_dir = tmp_path / "agent-bad"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.json"
        manifest.write_text(
            json.dumps({"agent_id": "agent-bad", "missing_required_fields": True}),
            encoding="utf-8",
        )

        store = ManifestStore(agents_root=tmp_path)
        result = store.load("agent-bad")

        assert result is None


class TestManifestStoreLoadAllRunning:
    """load_all_running() returns only desired_state="running" manifests."""

    def test_load_all_running_returns_running_only(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        store.save(make_frame("agent-run-1", desired_state="running"))
        store.save(make_frame("agent-run-2", desired_state="running"))
        store.save(make_frame("agent-stop", desired_state="stopped"))

        running = store.load_all_running()

        ids = {f.agent_id for f in running}
        assert ids == {"agent-run-1", "agent-run-2"}

    def test_load_all_running_empty_when_none_running(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        store.save(make_frame("agent-stopped", desired_state="stopped"))

        running = store.load_all_running()

        assert running == []

    def test_load_all_running_empty_when_no_agents(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)

        running = store.load_all_running()

        assert running == []

    def test_load_all_running_skips_corrupt_manifests(self, tmp_path: Path) -> None:
        """Corrupt manifests should be skipped, not crash the call."""
        store = ManifestStore(agents_root=tmp_path)
        store.save(make_frame("agent-good", desired_state="running"))

        # Inject a corrupt manifest
        bad_dir = tmp_path / "agent-corrupt"
        bad_dir.mkdir()
        (bad_dir / "manifest.json").write_text("!!!bad json!!!", encoding="utf-8")

        running = store.load_all_running()

        assert len(running) == 1
        assert running[0].agent_id == "agent-good"


class TestManifestStoreDelete:
    """delete() removes manifest.json but leaves workspace/ intact."""

    def test_delete_removes_manifest(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        store.save(make_frame())
        manifest_path = tmp_path / "agent-001" / "manifest.json"
        assert manifest_path.exists()

        store.delete("agent-001")

        assert not manifest_path.exists()

    def test_delete_nonexistent_is_noop(self, tmp_path: Path) -> None:
        """Deleting an agent with no manifest should not raise."""
        store = ManifestStore(agents_root=tmp_path)

        # Must not raise
        store.delete("no-such-agent")

    def test_delete_leaves_workspace_directory(self, tmp_path: Path) -> None:
        """workspace/ is runtime-only and must survive a manifest delete."""
        store = ManifestStore(agents_root=tmp_path)
        store.save(make_frame())

        workspace = tmp_path / "agent-001" / "workspace"
        workspace.mkdir()
        (workspace / "state.json").write_text("{}", encoding="utf-8")

        store.delete("agent-001")

        assert workspace.exists()
        assert (workspace / "state.json").exists()


class TestManifestStoreUpdateDesiredState:
    """update_desired_state() changes only the desired_state field."""

    def test_update_desired_state_changes_value(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        store.save(make_frame(desired_state="running"))

        store.update_desired_state("agent-001", "stopped")

        loaded = store.load("agent-001")
        assert loaded is not None
        assert loaded.desired_state == "stopped"

    def test_update_desired_state_preserves_other_fields(self, tmp_path: Path) -> None:
        store = ManifestStore(agents_root=tmp_path)
        store.save(make_frame(generation=42, engine="gemini"))

        store.update_desired_state("agent-001", "stopped")

        loaded = store.load("agent-001")
        assert loaded is not None
        assert loaded.generation == 42
        assert loaded.engine == "gemini"

    def test_update_desired_state_nonexistent_raises(self, tmp_path: Path) -> None:
        """Updating a non-existent agent should raise FileNotFoundError."""
        store = ManifestStore(agents_root=tmp_path)

        with pytest.raises(FileNotFoundError):
            store.update_desired_state("no-such-agent", "stopped")


class TestManifestStoreDefaultRoot:
    """ManifestStore uses ~/.doorae/agents by default."""

    def test_default_root_is_home_dotdoorae_agents(self) -> None:
        store = ManifestStore()
        expected = Path.home() / ".doorae" / "agents"
        assert store.agents_root == expected
