"""Tests for agent subprocess spawner."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doorae_machine.spawner import SpawnManifest, Spawner, SpawnResult


@pytest.fixture
def spawner(tmp_path: Path) -> Spawner:
    """Create a Spawner with mock callbacks and an isolated agent dirs root.

    ``agent_dirs_root`` is redirected at ``tmp_path`` so the spawner's
    materialize step doesn't leak files into the developer's real
    ``~/.doorae/agents/`` directory when tests run.
    """
    return Spawner(
        on_stopped=AsyncMock(),
        on_crashed=AsyncMock(),
        agent_dirs_root=tmp_path / "agents",
    )


@pytest.fixture
def spawn_msg() -> SpawnManifest:
    """A valid SpawnManifest."""
    return SpawnManifest(
        agent_id="agent-test-001",
        engine="claude-code",
        agent_token="secret-token-xyz",
        profile_yaml="name: test-agent\nmodel: claude-3",
        rooms=["room-alpha"],
        server_url="wss://localhost:8000/ws/agent",
    )


class TestSpawn:
    """Tests for spawning agent subprocesses."""

    async def test_spawn_success(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """Should spawn a subprocess and return success with pid."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value="/usr/local/bin/doorae-agent",
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert result.pid == 42
        assert result.agent_id == "agent-test-001"

    async def test_spawn_falls_back_to_uvx(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """When doorae-agent is not in PATH, spawner should use uvx."""
        mock_proc = MagicMock()
        mock_proc.pid = 50
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        captured_cmd = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            return mock_proc

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value=None,  # doorae-agent NOT found
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert "uvx" in captured_cmd[0]

    async def test_spawn_logs_agent_binary_path_source(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """PATH hit path: the spawn must emit
        ``agent_binary_resolved`` with source=path and the discovered
        binary path. This is the forensic trail operators rely on when
        debugging which ``doorae-agent`` actually ran."""
        mock_proc = MagicMock()
        mock_proc.pid = 60
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value="/usr/local/bin/doorae-agent",
        ), patch("doorae_machine.spawner.log") as mock_log:
            await spawner.spawn(spawn_msg)
            calls = [
                c
                for c in mock_log.info.call_args_list
                if c.args and c.args[0] == "agent_binary_resolved"
            ]
            assert len(calls) == 1
            assert calls[0].kwargs["source"] == "path"
            assert calls[0].kwargs["path"] == "/usr/local/bin/doorae-agent"

    async def test_spawn_logs_agent_binary_uvx_source(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """uvx fallback path: same event key, ``source=uvx`` and
        ``path=None`` (the binary is fetched on demand so no stable
        filesystem path exists at spawn time)."""
        mock_proc = MagicMock()
        mock_proc.pid = 61
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value=None,
        ), patch("doorae_machine.spawner.log") as mock_log:
            await spawner.spawn(spawn_msg)
            calls = [
                c
                for c in mock_log.info.call_args_list
                if c.args and c.args[0] == "agent_binary_resolved"
            ]
            assert len(calls) == 1
            assert calls[0].kwargs["source"] == "uvx"
            assert calls[0].kwargs["path"] is None

    async def test_spawn_duplicate_agent_kills_old_and_respawns(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Duplicate spawn should kill the old process and succeed."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.send_signal = MagicMock()

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value="/usr/local/bin/doorae-agent",
        ):
            first = await spawner.spawn(spawn_msg)
            assert first.success is True
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        # The old process should have received SIGTERM
        mock_proc.send_signal.assert_called_with(signal.SIGTERM)

    async def test_spawn_passes_token_via_env(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """Agent token must be passed via DOORAE_TOKEN env var, not argv."""
        captured_env = {}

        async def mock_exec(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            proc = MagicMock()
            proc.pid = 99
            proc.wait = AsyncMock(return_value=0)
            proc.stderr = None
            return proc

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=mock_exec,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value="/usr/local/bin/doorae-agent",
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert captured_env.get("DOORAE_TOKEN") == "secret-token-xyz"

    async def test_spawn_typescript_runtime_uses_doorae_agent_ts_when_on_path(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Issue #73 — ``runtime='typescript'`` resolves to the
        ``doorae-agent-ts`` binary when present on PATH. The ``which``
        call must target the TS binary name, not the Python one.
        """
        captured_cmd: list[str] = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = MagicMock()
            proc.pid = 71
            proc.wait = AsyncMock(return_value=0)
            proc.stderr = None
            return proc

        def fake_which(name: str):
            if name == "doorae-agent-ts":
                return "/usr/local/bin/doorae-agent-ts"
            return None

        spawn_msg.runtime = "typescript"

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch("doorae_machine.spawner.shutil.which", side_effect=fake_which):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert captured_cmd[0] == "/usr/local/bin/doorae-agent-ts"
        # Same --engine/--name/--server contract as the Python arm.
        assert "--engine" in captured_cmd
        assert "--server" in captured_cmd

    async def test_spawn_typescript_runtime_falls_back_to_npx(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Issue #73 — when ``doorae-agent-ts`` is not installed,
        spawner falls back to ``npx -y @doorae/agent-ts``. This is the
        "no local install" path on fresh machines."""
        captured_cmd: list[str] = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = MagicMock()
            proc.pid = 72
            proc.wait = AsyncMock(return_value=0)
            proc.stderr = None
            return proc

        spawn_msg.runtime = "typescript"

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value=None,  # Nothing installed
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert captured_cmd[0] == "npx"
        assert captured_cmd[1] == "-y"
        assert captured_cmd[2] == "@doorae/agent-ts"

    async def test_spawn_typescript_runtime_logs_binary_resolution(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Issue #73 — the ``agent_binary_resolved`` log line is
        emitted with ``runtime='typescript'`` + source=path|npx so
        operators can tell which runtime and which binary ran."""
        mock_proc = MagicMock()
        mock_proc.pid = 73
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None

        spawn_msg.runtime = "typescript"

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value="/usr/local/bin/doorae-agent-ts",
        ), patch("doorae_machine.spawner.log") as mock_log:
            await spawner.spawn(spawn_msg)
            calls = [
                c
                for c in mock_log.info.call_args_list
                if c.args and c.args[0] == "agent_binary_resolved"
            ]
            assert len(calls) == 1
            assert calls[0].kwargs["runtime"] == "typescript"
            assert calls[0].kwargs["source"] == "path"
            assert calls[0].kwargs["path"] == "/usr/local/bin/doorae-agent-ts"

    async def test_spawn_python_runtime_still_default(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Issue #73 regression guard — a manifest with ``runtime``
        unset (or explicitly ``"python"``) must still pick the Python
        binary. No TS binary lookup happens on the default path."""
        captured_cmd: list[str] = []
        which_calls: list[str] = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = MagicMock()
            proc.pid = 74
            proc.wait = AsyncMock(return_value=0)
            proc.stderr = None
            return proc

        def fake_which(name: str):
            which_calls.append(name)
            if name == "doorae-agent":
                return "/usr/local/bin/doorae-agent"
            return None

        # Leave ``spawn_msg.runtime`` at its default.
        assert spawn_msg.runtime == "python"

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch("doorae_machine.spawner.shutil.which", side_effect=fake_which):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert captured_cmd[0] == "/usr/local/bin/doorae-agent"
        # The Python path must not probe for the TS binary.
        assert "doorae-agent-ts" not in which_calls

    async def test_spawn_profile_chmod(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """Profile temp file should be created with chmod 600."""
        chmod_calls = []
        original_chmod = os.chmod

        def track_chmod(path, mode):
            chmod_calls.append((str(path), oct(mode)))
            return original_chmod(path, mode)

        mock_proc = MagicMock()
        mock_proc.pid = 55
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None

        with (
            patch("doorae_machine.spawner.os.chmod", side_effect=track_chmod),
            patch(
                "doorae_machine.spawner.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch(
                "doorae_machine.spawner.shutil.which",
                return_value="/usr/local/bin/doorae-agent",
            ),
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        # Check that chmod 600 was called for a doorae-agent profile file
        chmod_for_profile = [c for c in chmod_calls if "doorae-agent-" in c[0]]
        assert len(chmod_for_profile) == 1
        assert chmod_for_profile[0][1] == "0o600"


class TestGetRunning:
    """Tests for get_running accessor."""

    async def test_get_running_returns_agent(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """get_running should return the RunningAgent for a spawned agent."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value="/usr/local/bin/doorae-agent",
        ):
            await spawner.spawn(spawn_msg)

        agent = spawner.get_running("agent-test-001")
        assert agent is not None
        assert agent.agent_id == "agent-test-001"
        assert agent.pid == 42

    def test_get_running_returns_none_for_unknown(self, spawner: Spawner) -> None:
        """get_running should return None for an agent not being tracked."""
        assert spawner.get_running("nonexistent") is None


class TestKill:
    """Tests for killing agent processes."""

    async def test_kill_sigterm(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """Should send SIGTERM and wait for process to exit."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.send_signal = MagicMock()
        mock_proc.stderr = None

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value="/usr/local/bin/doorae-agent",
        ):
            await spawner.spawn(spawn_msg)

        result = await spawner.kill("agent-test-001")
        assert result["success"] is True
        mock_proc.send_signal.assert_called_once_with(signal.SIGTERM)

    async def test_kill_nonexistent_agent(self, spawner: Spawner) -> None:
        """Should return error for unknown agent_id."""
        result = await spawner.kill("no-such-agent")
        assert result["success"] is False
        assert "not found" in result["error"]


class TestCleanup:
    """Tests for cleanup after agent exit."""

    async def test_cleanup_removes_profile(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """Cleanup should delete the temp profile file."""
        mock_proc = MagicMock()
        mock_proc.pid = 77
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None

        with patch(
            "doorae_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "doorae_machine.spawner.shutil.which",
            return_value="/usr/local/bin/doorae-agent",
        ):
            await spawner.spawn(spawn_msg)

        # Retrieve actual profile path from the running agent
        agent = spawner._agents[spawn_msg.agent_id]
        profile_path = agent.profile_path
        assert profile_path is not None
        assert profile_path.exists()

        # Cleanup
        spawner._cleanup(spawn_msg.agent_id)

        # Profile should be deleted
        assert not profile_path.exists()
        # Agent should be removed from running list
        assert len(spawner.list_running()) == 0
