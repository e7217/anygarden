"""Shared fixtures for anygarden-machine tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from anygarden_machine.protocol.frames import SyncDesiredStateFrame


@pytest.fixture(autouse=True)
def _isolated_managed_engines(tmp_path_factory, monkeypatch):
    """#688 — never read or create ``~/.anygarden/engines`` from tests."""
    monkeypatch.setenv(
        "ANYGARDEN_MANAGED_ENGINES_DIR", str(tmp_path_factory.mktemp("managed-engines"))
    )
    monkeypatch.delenv("ANYGARDEN_PI_USE_PATH", raising=False)
    monkeypatch.delenv("ANYGARDEN_MANAGED_PI", raising=False)
    monkeypatch.delenv("ANYGARDEN_PI_EXECUTABLE", raising=False)


@pytest.fixture
def spawn_agent_frame() -> SyncDesiredStateFrame:
    """A SyncDesiredStateFrame representing a running agent (replaces old SpawnAgentFrame)."""
    return SyncDesiredStateFrame(
        agent_id="agent-001",
        desired_state="running",
        generation=1,
        engine="claude-code",
        profile_yaml="name: test-agent\nmodel: claude-3",
        rooms=["room-1", "room-2"],
    )


@pytest.fixture
def mock_process() -> MagicMock:
    """A mock asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None
    proc.wait = AsyncMock(return_value=0)
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.send_signal = MagicMock()
    proc.kill = MagicMock()
    proc.stderr = MagicMock()
    proc.stderr.read = AsyncMock(return_value=b"some error output")
    return proc
