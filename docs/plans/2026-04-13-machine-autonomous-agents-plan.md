# Machine Autonomous Agent Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the imperative spawn/kill protocol with a declarative desired-state sync model so machines own agent execution and self-recover without server round-trips.

**Architecture:** Server owns desired state (what should run) and pushes `sync_desired_state` frames to machines. Machines own actual state (what is running), persist manifests locally, handle crash restarts with rate limiting, and report back via `report_actual_state`. Reconnection triggers a bidirectional diff reconcile using `generation` numbers to detect config staleness.

**Tech Stack:** Python 3.12, Pydantic v2 (frame models), SQLAlchemy 2.x (server ORM), asyncio (subprocess management), structlog (logging), pytest + pytest-asyncio (testing)

**Design doc:** `docs/plans/2026-04-13-machine-autonomous-agents.md`

---

## File Structure

### doorae-machine (new files)

| File | Responsibility |
|------|---------------|
| `doorae_machine/manifest_store.py` | Read/write/list `manifest.json` under `~/.doorae/agents/<id>/` |
| `doorae_machine/crash_budget.py` | Per-agent crash rate limiter (timestamps in sliding window) |
| `tests/test_manifest_store.py` | ManifestStore unit tests |
| `tests/test_crash_budget.py` | CrashBudget unit tests |

### doorae-machine (modified files)

| File | Changes |
|------|---------|
| `doorae_machine/protocol/frames.py` | Add new frame models, update `ServerFrame`/`MachineFrame` unions, update parser |
| `doorae_machine/daemon.py` | Replace `_handle_spawn`/`_handle_kill` with `_handle_sync_desired_state`/`_handle_sync_batch`/`_handle_token_grant`; change heartbeat to `report_actual_state`; add reconnect sequence with token_request; wire ManifestStore + CrashBudget |
| `doorae_machine/spawner.py` | Add `get_running(agent_id)` accessor; no other structural changes |
| `tests/test_daemon.py` | Update tests for new frame handlers |
| `tests/test_spawner.py` | Update `spawn_msg` fixture to new frame type |

### doorae-server (modified files)

| File | Changes |
|------|---------|
| `doorae/db/models.py` | Add `generation: int` column to `Agent`, add `max_restarts`/`restart_window_seconds` columns |
| `doorae/scheduler/lifecycle.py` | Rewrite `request_start` → send `sync_desired_state`; remove `on_agent_crashed` restart logic; add `handle_token_request`, `handle_request_replacement`, `handle_report_actual_state`; add `_build_sync_frame` helper |
| `doorae/ws/machine_handler.py` | Replace heartbeat/agent_* frame handlers with `report_actual_state`, `token_request`, `request_replacement`; add `sync_batch` sending on reconnect |
| `doorae/app.py` | Remove stale-state reset in lifespan; add optional stale-machine background task |
| `doorae/api/v1/agents.py` | Increment `generation` on config-changing updates |
| `tests/test_machine_handler.py` | Rewrite for new protocol |

---

### Task 1: Protocol Frame Models (doorae-machine)

**Files:**
- Modify: `doorae-machine/doorae_machine/protocol/frames.py`
- Test: `doorae-machine/tests/test_protocol_frames.py` (create)

- [ ] **Step 1: Write failing tests for new frame models**

Create `doorae-machine/tests/test_protocol_frames.py`:

```python
"""Tests for the new declarative protocol frame models."""

from __future__ import annotations

import pytest

from doorae_machine.protocol.frames import (
    SyncDesiredStateFrame,
    SyncBatchFrame,
    TokenGrantFrame,
    ReportActualStateFrame,
    AgentActual,
    TokenRequestFrame,
    RequestReplacementFrame,
    parse_server_frame,
)


class TestSyncDesiredStateFrame:
    def test_running_state_with_full_payload(self) -> None:
        frame = SyncDesiredStateFrame(
            agent_id="agent-001",
            desired_state="running",
            generation=3,
            engine="claude-code",
            name="reviewer",
            profile_yaml="model: claude-3",
            rooms=["room-1", "room-2"],
            agents_md="# Instructions",
            files={"skills/review/SKILL.md": "# Review"},
            restart_policy="restart_anywhere",
            max_restarts=3,
            restart_window_seconds=300,
        )
        assert frame.type == "sync_desired_state"
        assert frame.agent_id == "agent-001"
        assert frame.desired_state == "running"
        assert frame.generation == 3
        assert frame.restart_policy == "restart_anywhere"

    def test_stopped_state_minimal(self) -> None:
        frame = SyncDesiredStateFrame(
            agent_id="agent-001",
            desired_state="stopped",
            generation=4,
        )
        assert frame.desired_state == "stopped"
        assert frame.engine == ""
        assert frame.rooms == []

    def test_defaults(self) -> None:
        frame = SyncDesiredStateFrame(
            agent_id="a", desired_state="running", generation=1,
        )
        assert frame.max_restarts == 3
        assert frame.restart_window_seconds == 300
        assert frame.restart_policy == "restart_anywhere"


class TestSyncBatchFrame:
    def test_batch_with_multiple_agents(self) -> None:
        agents = [
            SyncDesiredStateFrame(
                agent_id=f"agent-{i}", desired_state="running", generation=1,
                engine="claude-code",
            )
            for i in range(3)
        ]
        batch = SyncBatchFrame(agents=agents)
        assert batch.type == "sync_batch"
        assert len(batch.agents) == 3

    def test_empty_batch(self) -> None:
        batch = SyncBatchFrame(agents=[])
        assert len(batch.agents) == 0


class TestTokenGrantFrame:
    def test_token_grant(self) -> None:
        frame = TokenGrantFrame(agent_id="agent-001", agent_token="agt_abc123")
        assert frame.type == "token_grant"
        assert frame.agent_token == "agt_abc123"


class TestReportActualStateFrame:
    def test_with_agents(self) -> None:
        frame = ReportActualStateFrame(agents=[
            AgentActual(
                agent_id="agent-001",
                actual_state="running",
                pid=12345,
                engine="claude-code",
                generation=3,
                uptime_seconds=120,
            ),
        ])
        assert frame.type == "report_actual_state"
        assert frame.agents[0].pid == 12345

    def test_empty_report(self) -> None:
        frame = ReportActualStateFrame(agents=[])
        assert len(frame.agents) == 0


class TestTokenRequestFrame:
    def test_multiple_ids(self) -> None:
        frame = TokenRequestFrame(agent_ids=["a1", "a2", "a3"])
        assert frame.type == "token_request"
        assert len(frame.agent_ids) == 3


class TestRequestReplacementFrame:
    def test_replacement(self) -> None:
        frame = RequestReplacementFrame(
            agent_id="agent-001", reason="crash_budget_exhausted",
        )
        assert frame.type == "request_replacement"


class TestParseServerFrame:
    def test_parse_sync_desired_state(self) -> None:
        data = {
            "type": "sync_desired_state",
            "agent_id": "agent-001",
            "desired_state": "running",
            "generation": 1,
            "engine": "claude-code",
        }
        frame = parse_server_frame(data)
        assert isinstance(frame, SyncDesiredStateFrame)

    def test_parse_sync_batch(self) -> None:
        data = {
            "type": "sync_batch",
            "agents": [
                {"agent_id": "a1", "desired_state": "running", "generation": 1},
            ],
        }
        frame = parse_server_frame(data)
        assert isinstance(frame, SyncBatchFrame)

    def test_parse_token_grant(self) -> None:
        data = {"type": "token_grant", "agent_id": "a1", "agent_token": "tok"}
        frame = parse_server_frame(data)
        assert isinstance(frame, TokenGrantFrame)

    def test_legacy_spawn_agent_raises(self) -> None:
        """Old spawn_agent frames should fail after protocol migration."""
        with pytest.raises(ValueError, match="Unknown server frame"):
            parse_server_frame({"type": "spawn_agent", "agent_id": "a"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd doorae-machine && uv run pytest tests/test_protocol_frames.py -v`
Expected: ImportError — new frame classes don't exist yet.

- [ ] **Step 3: Implement new frame models**

Replace the contents of `doorae-machine/doorae_machine/protocol/frames.py`:

```python
"""Pydantic frame models for Machine <-> Server WebSocket protocol.

v2 — Declarative desired-state sync model.
See docs/plans/2026-04-13-machine-autonomous-agents.md
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field


# ── Server -> Machine frames ───────────────────────────────���──────────


class SyncDesiredStateFrame(BaseModel):
    """Server declares the desired state for one agent on this machine."""

    type: Literal["sync_desired_state"] = "sync_desired_state"
    agent_id: str
    desired_state: Literal["running", "stopped"]
    generation: int  # config version — bumped on every config change

    # Spawn payload (meaningful when desired_state="running")
    engine: str = ""
    name: str = ""
    profile_yaml: str = ""
    rooms: list[str] = Field(default_factory=list)
    agents_md: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    engine_secrets: dict[str, str] = Field(default_factory=dict)
    reasoning_effort: str | None = None
    sub_rooms: list[dict[str, str | None]] = Field(default_factory=list)

    # Restart policy — machine applies locally
    restart_policy: Literal[
        "stop", "restart_on_same_machine", "restart_anywhere"
    ] = "restart_anywhere"
    max_restarts: int = 3
    restart_window_seconds: int = 300


class SyncBatchFrame(BaseModel):
    """Batch of desired states sent on reconnection."""

    type: Literal["sync_batch"] = "sync_batch"
    agents: list[SyncDesiredStateFrame] = Field(default_factory=list)


class TokenGrantFrame(BaseModel):
    """Server grants a fresh agent token in response to token_request."""

    type: Literal["token_grant"] = "token_grant"
    agent_id: str
    agent_token: str


class DrainFrame(BaseModel):
    """Server instructs machine to drain (stop accepting new agents)."""

    type: Literal["drain"] = "drain"


class PingFrame(BaseModel):
    """Server ping for keepalive."""

    type: Literal["ping"] = "ping"


class RotateTokenFrame(BaseModel):
    """Server pushes a new machine token after rotation."""

    type: Literal["rotate_token"] = "rotate_token"
    new_token: str


ServerFrame = Union[
    SyncDesiredStateFrame,
    SyncBatchFrame,
    TokenGrantFrame,
    DrainFrame,
    PingFrame,
    RotateTokenFrame,
]


# ── Machine -> Server frames ─────────────────────────────────────────


class RegisterFrame(BaseModel):
    """Machine registers itself with the server on connect."""

    type: Literal["register"] = "register"
    machine_id: str
    capabilities: list[dict] = Field(default_factory=list)
    max_agents: int = 4
    labels: dict = Field(default_factory=dict)


class AgentActual(BaseModel):
    """One agent's actual state as reported by the machine."""

    agent_id: str
    actual_state: Literal["running", "stopped", "crashed", "starting"]
    pid: int | None = None
    engine: str = ""
    generation: int = 0
    uptime_seconds: int = 0
    last_crash_reason: str | None = None


class ReportActualStateFrame(BaseModel):
    """Machine reports actual state of all agents (replaces heartbeat)."""

    type: Literal["report_actual_state"] = "report_actual_state"
    agents: list[AgentActual] = Field(default_factory=list)


class TokenRequestFrame(BaseModel):
    """Machine requests fresh tokens for agents it wants to (re)start."""

    type: Literal["token_request"] = "token_request"
    agent_ids: list[str] = Field(default_factory=list)


class RequestReplacementFrame(BaseModel):
    """Machine asks server to re-place an agent on a different machine."""

    type: Literal["request_replacement"] = "request_replacement"
    agent_id: str
    reason: str = ""


MachineFrame = Union[
    RegisterFrame,
    ReportActualStateFrame,
    TokenRequestFrame,
    RequestReplacementFrame,
]


# ── Frame parsing ─────────────────────────────────────────────────────

_SERVER_FRAME_MAP: dict[str, type[BaseModel]] = {
    "sync_desired_state": SyncDesiredStateFrame,
    "sync_batch": SyncBatchFrame,
    "token_grant": TokenGrantFrame,
    "drain": DrainFrame,
    "ping": PingFrame,
    "rotate_token": RotateTokenFrame,
}


def parse_server_frame(data: dict) -> ServerFrame:
    """Parse a raw dict from server into the appropriate frame model.

    Raises ValueError if the frame type is unknown.
    """
    frame_type = data.get("type")
    if frame_type not in _SERVER_FRAME_MAP:
        raise ValueError(f"Unknown server frame type: {frame_type!r}")
    return _SERVER_FRAME_MAP[frame_type].model_validate(data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd doorae-machine && uv run pytest tests/test_protocol_frames.py -v`
Expected: All 12 tests PASS.

- [ ] **Step 5: Run existing tests to check for regressions**

Run: `cd doorae-machine && uv run pytest -v`
Expected: `test_daemon.py` and `test_spawner.py` will FAIL because they import old frames (`SpawnAgentFrame`, `AgentStartedFrame`, etc.) that no longer exist. This is expected — we'll fix them in later tasks.

- [ ] **Step 6: Commit**

```bash
git add doorae-machine/doorae_machine/protocol/frames.py doorae-machine/tests/test_protocol_frames.py
git commit -m "feat(machine): declarative protocol frame models

Replace spawn_agent/kill_agent/heartbeat frames with
sync_desired_state/sync_batch/report_actual_state/token_request."
```

---

### Task 2: ManifestStore (doorae-machine)

**Files:**
- Create: `doorae-machine/doorae_machine/manifest_store.py`
- Test: `doorae-machine/tests/test_manifest_store.py` (create)

- [ ] **Step 1: Write failing tests**

Create `doorae-machine/tests/test_manifest_store.py`:

```python
"""Tests for the local manifest persistence layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from doorae_machine.manifest_store import ManifestStore
from doorae_machine.protocol.frames import SyncDesiredStateFrame


@pytest.fixture
def store(tmp_path: Path) -> ManifestStore:
    return ManifestStore(agents_root=tmp_path / "agents")


@pytest.fixture
def sync_frame() -> SyncDesiredStateFrame:
    return SyncDesiredStateFrame(
        agent_id="agent-001",
        desired_state="running",
        generation=3,
        engine="claude-code",
        name="reviewer",
        profile_yaml="model: claude-3",
        rooms=["room-1"],
        agents_md="# Instructions",
        files={"skills/review/SKILL.md": "# Review"},
        restart_policy="restart_anywhere",
        max_restarts=5,
        restart_window_seconds=600,
    )


class TestManifestStore:
    def test_save_creates_manifest_json(
        self, store: ManifestStore, sync_frame: SyncDesiredStateFrame
    ) -> None:
        store.save(sync_frame)
        path = store._agents_root / "agent-001" / "manifest.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["agent_id"] == "agent-001"
        assert data["generation"] == 3
        assert data["desired_state"] == "running"

    def test_save_excludes_sensitive_fields(
        self, store: ManifestStore, sync_frame: SyncDesiredStateFrame
    ) -> None:
        """agent_token and engine_secrets must NOT be persisted."""
        sync_frame.engine_secrets = {"API_KEY": "secret123"}
        store.save(sync_frame)
        path = store._agents_root / "agent-001" / "manifest.json"
        data = json.loads(path.read_text())
        assert "agent_token" not in data
        assert "engine_secrets" not in data

    def test_save_overwrites_existing(
        self, store: ManifestStore, sync_frame: SyncDesiredStateFrame
    ) -> None:
        store.save(sync_frame)
        sync_frame.generation = 10
        store.save(sync_frame)
        path = store._agents_root / "agent-001" / "manifest.json"
        data = json.loads(path.read_text())
        assert data["generation"] == 10

    def test_load_returns_frame(
        self, store: ManifestStore, sync_frame: SyncDesiredStateFrame
    ) -> None:
        store.save(sync_frame)
        loaded = store.load("agent-001")
        assert loaded is not None
        assert loaded.agent_id == "agent-001"
        assert loaded.generation == 3
        assert loaded.engine == "claude-code"

    def test_load_nonexistent_returns_none(self, store: ManifestStore) -> None:
        assert store.load("does-not-exist") is None

    def test_load_all_running(
        self, store: ManifestStore, sync_frame: SyncDesiredStateFrame
    ) -> None:
        store.save(sync_frame)
        stopped = SyncDesiredStateFrame(
            agent_id="agent-002", desired_state="stopped", generation=1,
        )
        store.save(stopped)
        running = store.load_all_running()
        assert len(running) == 1
        assert running[0].agent_id == "agent-001"

    def test_delete_removes_manifest(
        self, store: ManifestStore, sync_frame: SyncDesiredStateFrame
    ) -> None:
        store.save(sync_frame)
        store.delete("agent-001")
        assert store.load("agent-001") is None

    def test_update_desired_state(
        self, store: ManifestStore, sync_frame: SyncDesiredStateFrame
    ) -> None:
        store.save(sync_frame)
        store.update_desired_state("agent-001", "stopped")
        loaded = store.load("agent-001")
        assert loaded is not None
        assert loaded.desired_state == "stopped"

    def test_manifest_file_permissions(
        self, store: ManifestStore, sync_frame: SyncDesiredStateFrame
    ) -> None:
        import os, stat
        store.save(sync_frame)
        path = store._agents_root / "agent-001" / "manifest.json"
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd doorae-machine && uv run pytest tests/test_manifest_store.py -v`
Expected: ImportError — `manifest_store` module doesn't exist.

- [ ] **Step 3: Implement ManifestStore**

Create `doorae-machine/doorae_machine/manifest_store.py`:

```python
"""Local manifest persistence for agent desired-state.

Stores a ``manifest.json`` per agent under ``~/.doorae/agents/<agent_id>/``.
Sensitive fields (agent_token, engine_secrets) are excluded — the daemon
requests fresh tokens from the server on every (re)start.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import structlog

from doorae_machine.protocol.frames import SyncDesiredStateFrame

log = structlog.get_logger()

# Fields that must NOT be written to disk.
_EXCLUDED_FIELDS = {"type", "engine_secrets"}


class ManifestStore:
    """Read/write/list manifest.json files under the agent dirs root."""

    def __init__(self, agents_root: Path | None = None) -> None:
        self._agents_root = (
            agents_root
            if agents_root is not None
            else Path.home() / ".doorae" / "agents"
        )

    def save(self, frame: SyncDesiredStateFrame) -> Path:
        """Persist a sync frame as manifest.json (excluding secrets)."""
        agent_dir = self._agents_root / frame.agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)

        data = frame.model_dump(mode="json")
        for key in _EXCLUDED_FIELDS:
            data.pop(key, None)
        data["saved_at"] = datetime.now(timezone.utc).isoformat()

        manifest_path = agent_dir / "manifest.json"
        manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        os.chmod(manifest_path, 0o600)

        log.debug(
            "manifest.saved",
            agent_id=frame.agent_id,
            generation=frame.generation,
        )
        return manifest_path

    def load(self, agent_id: str) -> SyncDesiredStateFrame | None:
        """Load a manifest, returning None if absent or corrupt."""
        manifest_path = self._agents_root / agent_id / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text())
            return SyncDesiredStateFrame.model_validate(data)
        except Exception as exc:
            log.warning("manifest.load_failed", agent_id=agent_id, error=str(exc))
            return None

    def load_all_running(self) -> list[SyncDesiredStateFrame]:
        """Load all manifests with desired_state='running'."""
        result: list[SyncDesiredStateFrame] = []
        if not self._agents_root.exists():
            return result
        for agent_dir in self._agents_root.iterdir():
            if not agent_dir.is_dir():
                continue
            frame = self.load(agent_dir.name)
            if frame is not None and frame.desired_state == "running":
                result.append(frame)
        return result

    def delete(self, agent_id: str) -> None:
        """Remove the manifest.json for an agent (keeps workspace)."""
        manifest_path = self._agents_root / agent_id / "manifest.json"
        manifest_path.unlink(missing_ok=True)
        log.debug("manifest.deleted", agent_id=agent_id)

    def update_desired_state(
        self, agent_id: str, desired_state: str
    ) -> None:
        """Update only the desired_state field in an existing manifest."""
        frame = self.load(agent_id)
        if frame is None:
            return
        frame.desired_state = desired_state  # type: ignore[assignment]
        self.save(frame)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd doorae-machine && uv run pytest tests/test_manifest_store.py -v`
Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add doorae-machine/doorae_machine/manifest_store.py doorae-machine/tests/test_manifest_store.py
git commit -m "feat(machine): ManifestStore for local agent manifest persistence"
```

---

### Task 3: CrashBudget (doorae-machine)

**Files:**
- Create: `doorae-machine/doorae_machine/crash_budget.py`
- Test: `doorae-machine/tests/test_crash_budget.py` (create)

- [ ] **Step 1: Write failing tests**

Create `doorae-machine/tests/test_crash_budget.py`:

```python
"""Tests for per-agent crash rate limiting."""

from __future__ import annotations

import time

import pytest

from doorae_machine.crash_budget import CrashBudget


class TestCrashBudget:
    def test_first_crash_allows_restart(self) -> None:
        budget = CrashBudget(max_restarts=3, window_seconds=300)
        assert budget.record_crash() is True

    def test_within_budget_allows_restarts(self) -> None:
        budget = CrashBudget(max_restarts=3, window_seconds=300)
        assert budget.record_crash() is True  # 1
        assert budget.record_crash() is True  # 2
        assert budget.record_crash() is True  # 3

    def test_exceeding_budget_denies_restart(self) -> None:
        budget = CrashBudget(max_restarts=3, window_seconds=300)
        budget.record_crash()  # 1
        budget.record_crash()  # 2
        budget.record_crash()  # 3
        assert budget.record_crash() is False  # 4th — over budget

    def test_expired_crashes_are_pruned(self) -> None:
        budget = CrashBudget(max_restarts=2, window_seconds=1)
        budget.record_crash()  # 1
        budget.record_crash()  # 2
        # Manually expire the timestamps
        budget._timestamps = [t - 2.0 for t in budget._timestamps]
        # Next crash should succeed (old ones expired)
        assert budget.record_crash() is True

    def test_reset_clears_history(self) -> None:
        budget = CrashBudget(max_restarts=1, window_seconds=300)
        budget.record_crash()
        assert budget.record_crash() is False
        budget.reset()
        assert budget.record_crash() is True

    def test_crash_count_returns_active_count(self) -> None:
        budget = CrashBudget(max_restarts=5, window_seconds=300)
        budget.record_crash()
        budget.record_crash()
        assert budget.crash_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd doorae-machine && uv run pytest tests/test_crash_budget.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement CrashBudget**

Create `doorae-machine/doorae_machine/crash_budget.py`:

```python
"""Per-agent crash rate limiter.

Tracks crash timestamps in a sliding window. When the count exceeds
``max_restarts`` within ``window_seconds``, further restarts are denied
until old timestamps expire.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class CrashBudget:
    """Sliding-window crash counter for one agent."""

    max_restarts: int = 3
    window_seconds: int = 300
    _timestamps: list[float] = field(default_factory=list)

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.window_seconds
        self._timestamps = [t for t in self._timestamps if t > cutoff]

    def record_crash(self) -> bool:
        """Record a crash. Returns True if restart is allowed."""
        self._prune()
        self._timestamps.append(time.monotonic())
        return len(self._timestamps) <= self.max_restarts

    def reset(self) -> None:
        """Clear crash history (e.g. after server resets the budget)."""
        self._timestamps.clear()

    @property
    def crash_count(self) -> int:
        """Number of crashes in the current window."""
        self._prune()
        return len(self._timestamps)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd doorae-machine && uv run pytest tests/test_crash_budget.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add doorae-machine/doorae_machine/crash_budget.py doorae-machine/tests/test_crash_budget.py
git commit -m "feat(machine): CrashBudget — per-agent crash rate limiter"
```

---

### Task 4: Daemon Frame Handlers + Reconnect Logic (doorae-machine)

**Files:**
- Modify: `doorae-machine/doorae_machine/daemon.py`
- Modify: `doorae-machine/doorae_machine/spawner.py` (minor accessor)
- Modify: `doorae-machine/tests/test_daemon.py`
- Modify: `doorae-machine/tests/test_spawner.py` (update fixture)

This is the largest machine-side task. The daemon's `_handle`, `_connect_and_serve`, heartbeat loop, and crash callbacks all change.

- [ ] **Step 1: Update spawner with `get_running` accessor**

Add to `doorae-machine/doorae_machine/spawner.py`, after the `list_running` method:

```python
    def get_running(self, agent_id: str) -> RunningAgent | None:
        """Return a specific running agent or None."""
        return self._agents.get(agent_id)
```

- [ ] **Step 2: Rewrite daemon.py**

Replace `doorae-machine/doorae_machine/daemon.py` with:

```python
"""WebSocket daemon: declarative desired-state sync with local autonomy.

v2 — replaces imperative spawn/kill with sync_desired_state/report_actual_state.
See docs/plans/2026-04-13-machine-autonomous-agents.md
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import structlog
import websockets
from websockets.asyncio.client import connect

from doorae_machine.config import save_token
from doorae_machine.crash_budget import CrashBudget
from doorae_machine.detector import detect_engines
from doorae_machine.manifest_store import ManifestStore
from doorae_machine.protocol.frames import (
    AgentActual,
    RegisterFrame,
    ReportActualStateFrame,
    RequestReplacementFrame,
    SyncDesiredStateFrame,
    TokenRequestFrame,
    parse_server_frame,
)
from doorae_machine.spawner import Spawner

log = structlog.get_logger()

HEARTBEAT_INTERVAL = 30  # seconds
RECONNECT_BASE = 1
RECONNECT_MAX = 60


def _base_url_from_machine_url(machine_ws_url: str) -> str:
    """Trim the ``/ws/machines/<id>`` endpoint suffix off the daemon URL."""
    if not machine_ws_url:
        return ""
    parsed = urlparse(machine_ws_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = parsed.path
    marker = "/ws/machines"
    idx = path.rfind(marker)
    if idx != -1:
        path = path[:idx]
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


class MachineDaemon:
    """Machine daemon with declarative desired-state sync."""

    def __init__(
        self,
        server_url: str,
        machine_id: str,
        machine_token: str,
        max_agents: int = 4,
        labels: dict | None = None,
        token_path: Any = None,
        agent_dirs_root: Path | None = None,
    ) -> None:
        self.server_url = server_url
        self.machine_id = machine_id
        self.machine_token = machine_token
        self.max_agents = max_agents
        self.labels = labels or {}
        self._token_path = token_path
        self._draining = False
        self._ws: Any = None

        self._manifest_store = ManifestStore(agents_root=agent_dirs_root)
        self._crash_budgets: dict[str, CrashBudget] = {}
        # Pending token requests: agent_id -> Future[str]
        self._token_futures: dict[str, asyncio.Future[str]] = {}

        self._spawner = Spawner(
            on_stopped=self._on_agent_stopped,
            on_crashed=self._on_agent_crashed,
            agent_server_url=_base_url_from_machine_url(server_url),
            agent_dirs_root=agent_dirs_root,
        )

    # ── Main loop ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main WebSocket reconnection loop with exponential backoff."""
        backoff = RECONNECT_BASE
        while True:
            try:
                await self._connect_and_serve()
                backoff = RECONNECT_BASE
            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                OSError,
            ) as exc:
                log.warning("ws_disconnected", error=str(exc), reconnect_in=backoff)
            except asyncio.CancelledError:
                log.info("daemon_cancelled")
                await self._spawner.drain()
                return

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _connect_and_serve(self) -> None:
        """Establish WS connection, run reconnect sequence + message loop."""
        subprotocols = [
            websockets.Subprotocol("doorae.v1"),
            websockets.Subprotocol(f"bearer.{self.machine_token}"),
        ]

        async with connect(self.server_url, subprotocols=subprotocols) as ws:
            self._ws = ws
            log.info("ws_connected", url=self.server_url)

            # Reconnect sequence: register → report_actual_state
            await self._register()
            await self._report_actual_state()

            # Run report loop and message handler concurrently
            report_task = asyncio.create_task(self._report_loop())
            try:
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        await self._handle(data)
                    except json.JSONDecodeError:
                        log.warning("invalid_json", raw=str(raw_msg)[:200])
                    except Exception as exc:
                        log.error("handle_error", error=str(exc))
            finally:
                report_task.cancel()
                try:
                    await report_task
                except asyncio.CancelledError:
                    pass
                self._ws = None

    # ── Registration & reporting ───────────────────────────────────────

    async def _register(self) -> None:
        detection = await detect_engines()
        capabilities = [
            {"engine": e.engine, "version": e.version, "path": e.path}
            for e in detection.engines
        ]
        frame = RegisterFrame(
            machine_id=self.machine_id,
            capabilities=capabilities,
            max_agents=self.max_agents,
            labels=self.labels,
        )
        await self._send(frame.model_dump())

    async def _report_actual_state(self) -> None:
        """Build and send report_actual_state from spawner + manifest store."""
        agents: list[AgentActual] = []
        import time

        for info in self._spawner.list_running():
            manifest = self._manifest_store.load(info["agent_id"])
            agents.append(AgentActual(
                agent_id=info["agent_id"],
                actual_state="running",
                pid=info["pid"],
                engine=info["engine"],
                generation=manifest.generation if manifest else 0,
                uptime_seconds=info["uptime_seconds"],
            ))

        frame = ReportActualStateFrame(agents=agents)
        await self._send(frame.model_dump())

    async def _report_loop(self) -> None:
        """Periodically send report_actual_state (replaces heartbeat)."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._report_actual_state()

    # ── Frame dispatch ─────────────────────────────────────────────────

    async def _handle(self, data: dict) -> None:
        frame = parse_server_frame(data)
        match frame.type:
            case "sync_desired_state":
                await self._handle_sync_desired_state(frame)
            case "sync_batch":
                await self._handle_sync_batch(frame)
            case "token_grant":
                self._handle_token_grant(frame)
            case "drain":
                await self._handle_drain()
            case "ping":
                await self._report_actual_state()
            case "rotate_token":
                await self._handle_rotate_token(frame)

    # ── sync_desired_state handling ────────────────────────────────────

    async def _handle_sync_desired_state(
        self, frame: SyncDesiredStateFrame
    ) -> None:
        """Reconcile one agent: save manifest, then converge actual → desired."""
        self._manifest_store.save(frame)
        await self._reconcile_agent(frame.agent_id)

    async def _handle_sync_batch(self, frame: Any) -> None:
        """Reconcile all agents in batch, kill orphans."""
        desired_ids = set()
        for agent_frame in frame.agents:
            desired_ids.add(agent_frame.agent_id)
            self._manifest_store.save(agent_frame)

        # Kill orphans: running locally but not in server's desired set
        for info in self._spawner.list_running():
            if info["agent_id"] not in desired_ids:
                log.warning("orphan_agent_killed", agent_id=info["agent_id"])
                await self._spawner.kill(info["agent_id"])
                self._manifest_store.delete(info["agent_id"])

        # Reconcile all desired agents
        for agent_frame in frame.agents:
            await self._reconcile_agent(agent_frame.agent_id)

    async def _reconcile_agent(self, agent_id: str) -> None:
        """Converge actual state toward desired state for one agent."""
        manifest = self._manifest_store.load(agent_id)
        if manifest is None:
            return

        running = self._spawner.get_running(agent_id)

        if manifest.desired_state == "stopped":
            if running:
                await self._spawner.kill(agent_id)
            return

        # desired_state == "running"
        if running:
            # Check generation — if changed, restart with new config
            current_gen = self._manifest_store.load(agent_id)
            stored_gen = manifest.generation
            running_manifest = self._manifest_store.load(agent_id)
            # Compare by checking if running agent's generation matches
            # We track generation in the manifest; spawner doesn't know it.
            # If generation changed (sync_desired_state with new gen),
            # we need to kill and respawn.
            # For sync_batch, the manifest was just saved with the new gen.
            # The running agent was started with an older gen.
            # We store the "running generation" in _running_generations.
            running_gen = self._running_generations.get(agent_id, 0)
            if running_gen < manifest.generation:
                log.info(
                    "generation_changed",
                    agent_id=agent_id,
                    old=running_gen,
                    new=manifest.generation,
                )
                await self._spawner.kill(agent_id)
                # Fall through to spawn below
            else:
                return  # Already running with correct generation

        # Need to spawn — request token from server
        await self._request_token_and_spawn(agent_id, manifest)

    async def _request_token_and_spawn(
        self, agent_id: str, manifest: SyncDesiredStateFrame
    ) -> None:
        """Request a fresh token from the server, then spawn."""
        loop = asyncio.get_event_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._token_futures[agent_id] = future

        req = TokenRequestFrame(agent_ids=[agent_id])
        await self._send(req.model_dump())

        try:
            token = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            log.error("token_request_timeout", agent_id=agent_id)
            self._token_futures.pop(agent_id, None)
            return
        finally:
            self._token_futures.pop(agent_id, None)

        # Build a SpawnAgentFrame-compatible message for the spawner.
        # The spawner still uses SpawnAgentFrame internally for
        # materialization — we adapt the manifest into that shape.
        from doorae_machine.protocol.frames import SyncDesiredStateFrame

        spawn_data = _manifest_to_spawn_kwargs(manifest, token, self.server_url)
        result = await self._spawner.spawn_from_manifest(spawn_data)

        if result.success:
            self._running_generations[agent_id] = manifest.generation
            log.info("agent_spawned", agent_id=agent_id, pid=result.pid)
        else:
            log.error("agent_spawn_failed", agent_id=agent_id, error=result.error)

        await self._report_actual_state()

    def _handle_token_grant(self, frame: Any) -> None:
        """Resolve the pending token future for the given agent."""
        future = self._token_futures.get(frame.agent_id)
        if future and not future.done():
            future.set_result(frame.agent_token)
        else:
            log.warning("token_grant_unexpected", agent_id=frame.agent_id)

    # ── Crash handling (local autonomy) ────────────────────────────────

    async def _on_agent_stopped(self, agent_id: str, exit_code: int) -> None:
        """Agent exited normally — just report."""
        self._running_generations.pop(agent_id, None)
        await self._report_actual_state()

    async def _on_agent_crashed(
        self, agent_id: str, exit_code: int, stderr_tail: str
    ) -> None:
        """Agent crashed — apply restart policy locally."""
        self._running_generations.pop(agent_id, None)
        manifest = self._manifest_store.load(agent_id)

        if manifest is None or manifest.desired_state != "running":
            await self._report_actual_state()
            return

        if manifest.restart_policy == "stop":
            self._manifest_store.update_desired_state(agent_id, "stopped")
            await self._report_actual_state()
            return

        # Get or create crash budget
        budget = self._crash_budgets.get(agent_id)
        if budget is None:
            budget = CrashBudget(
                max_restarts=manifest.max_restarts,
                window_seconds=manifest.restart_window_seconds,
            )
            self._crash_budgets[agent_id] = budget

        if budget.record_crash():
            # Budget allows restart
            log.info(
                "crash_local_restart",
                agent_id=agent_id,
                crash_count=budget.crash_count,
                max=manifest.max_restarts,
            )
            await self._request_token_and_spawn(agent_id, manifest)
        else:
            # Budget exhausted
            if manifest.restart_policy == "restart_anywhere":
                log.warning("crash_budget_exhausted_requesting_replacement",
                            agent_id=agent_id)
                req = RequestReplacementFrame(
                    agent_id=agent_id, reason="crash_budget_exhausted",
                )
                await self._send(req.model_dump())
            else:
                log.warning("crash_budget_exhausted_stopping", agent_id=agent_id)
                self._manifest_store.update_desired_state(agent_id, "stopped")

            await self._report_actual_state()

    # ── Drain & token rotation ─────────────────────────────────────────

    async def _handle_drain(self) -> None:
        self._draining = True
        log.info("drain_started")
        await self._spawner.drain()

    async def _handle_rotate_token(self, frame: Any) -> None:
        try:
            save_token(frame.new_token, path=self._token_path)
        except Exception as exc:
            log.error("rotate_token_save_failed", error=str(exc))
            return
        self.machine_token = frame.new_token
        log.info("rotate_token_applied")

    # ── Helpers ────────────────────────────────────────────────────────

    async def _send(self, data: dict) -> None:
        if self._ws is None:
            log.warning("send_no_ws", frame_type=data.get("type"))
            return
        try:
            await self._ws.send(json.dumps(data))
        except Exception as exc:
            log.error("send_error", error=str(exc), frame_type=data.get("type"))


def _manifest_to_spawn_kwargs(
    manifest: SyncDesiredStateFrame,
    agent_token: str,
    server_url: str,
) -> dict:
    """Convert a manifest frame + token into kwargs for spawner."""
    return {
        "agent_id": manifest.agent_id,
        "engine": manifest.engine,
        "agent_token": agent_token,
        "profile_yaml": manifest.profile_yaml,
        "rooms": manifest.rooms,
        "server_url": server_url,
        "name": manifest.name,
        "agents_md": manifest.agents_md,
        "files": manifest.files,
        "engine_secrets": manifest.engine_secrets,
        "reasoning_effort": manifest.reasoning_effort,
        "sub_rooms": manifest.sub_rooms,
    }
```

**Important:** The daemon now needs `_running_generations: dict[str, int]` to track which generation each running agent was spawned with. Add to `__init__`:

```python
self._running_generations: dict[str, int] = {}
```

And the `Spawner` needs a `spawn_from_manifest(kwargs: dict)` method that creates a `SpawnAgentFrame`-compatible object internally to drive materialization + subprocess launch. This avoids importing the old frame type. Add to `spawner.py`:

```python
    async def spawn_from_manifest(self, kwargs: dict) -> SpawnResult:
        """Spawn from a dict of manifest fields + token.

        This is the v2 entry point — the daemon passes a dict built from
        SyncDesiredStateFrame + fresh token, and we wrap it in a
        SpawnAgentFrame for backward-compatible materialization.
        """
        from doorae_machine.protocol.frames import SyncDesiredStateFrame
        # We still need the old-style frame for _materialize_agent_dir.
        # Rather than duplicating, we create a lightweight adapter.
        msg = _SpawnManifest(**kwargs)
        return await self._spawn_internal(msg)
```

Actually, this is getting complex. A cleaner approach: refactor `spawner.spawn()` to accept a `SpawnManifest` dataclass instead of the protocol frame. This keeps spawner decoupled from the protocol.

- [ ] **Step 3: Add SpawnManifest dataclass to spawner.py**

Add at the top of `doorae-machine/doorae_machine/spawner.py`, after existing imports:

```python
@dataclass
class SpawnManifest:
    """Engine-agnostic spawn parameters (decoupled from WS protocol frames)."""
    agent_id: str
    engine: str
    agent_token: str
    profile_yaml: str = ""
    rooms: list[str] = field(default_factory=list)
    server_url: str = ""
    name: str = ""
    agents_md: str | None = None
    files: dict[str, str] = field(default_factory=dict)
    engine_secrets: dict[str, str] = field(default_factory=dict)
    reasoning_effort: str | None = None
    sub_rooms: list[dict] = field(default_factory=list)
```

Then change `spawner.spawn(msg: SpawnAgentFrame)` signature to `spawner.spawn(msg: SpawnManifest)` and update the type references. The internal `_materialize_agent_dir` uses the same field names so it works unchanged.

- [ ] **Step 4: Update test_spawner.py fixture**

Change `spawn_msg` fixture in `tests/test_spawner.py`:

```python
from doorae_machine.spawner import Spawner, SpawnResult, SpawnManifest

@pytest.fixture
def spawn_msg() -> SpawnManifest:
    return SpawnManifest(
        agent_id="agent-test-001",
        engine="claude-code",
        agent_token="secret-token-xyz",
        profile_yaml="name: test-agent\nmodel: claude-3",
        rooms=["room-alpha"],
        server_url="wss://localhost:8000/ws/agent",
    )
```

- [ ] **Step 5: Update test_daemon.py for new protocol**

Rewrite tests to use new frame types. Key changes:
- Replace `SpawnAgentFrame` → `SyncDesiredStateFrame` in imports
- Replace `HeartbeatFrame` → `ReportActualStateFrame`
- Replace `AgentStartedFrame` → removed
- Update `_handle_spawn` test to test `_handle_sync_desired_state`
- Add tests for `_handle_sync_batch`, `_handle_token_grant`, crash budget behavior

- [ ] **Step 6: Run all doorae-machine tests**

Run: `cd doorae-machine && uv run pytest -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add doorae-machine/
git commit -m "feat(machine): declarative daemon with local crash restart

- Daemon handles sync_desired_state/sync_batch/token_grant frames
- Crash restart with CrashBudget rate limiting (local autonomy)
- ManifestStore persists agent config (excluding tokens)
- report_actual_state replaces heartbeat
- SpawnManifest decouples spawner from protocol frames"
```

---

### Task 5: Server — Agent Model Migration (doorae-server)

**Files:**
- Modify: `doorae-server/doorae/db/models.py`
- Test: verify migration with existing test suite

- [ ] **Step 1: Add generation, max_restarts, restart_window_seconds to Agent model**

In `doorae-server/doorae/db/models.py`, add to the `Agent` class after `restart_policy`:

```python
    generation: Mapped[int] = mapped_column(Integer, default=0)
    max_restarts: Mapped[int] = mapped_column(Integer, default=3)
    restart_window_seconds: Mapped[int] = mapped_column(Integer, default=300)
```

- [ ] **Step 2: Run existing server tests to verify no regressions**

Run: `cd doorae-server && uv run pytest -v`
Expected: Tests pass (SQLite test DB auto-creates tables with new columns).

- [ ] **Step 3: Commit**

```bash
git add doorae-server/doorae/db/models.py
git commit -m "feat(server): add generation/max_restarts/restart_window_seconds to Agent model"
```

---

### Task 6: Server — Lifecycle Declarative Rewrite (doorae-server)

**Files:**
- Modify: `doorae-server/doorae/scheduler/lifecycle.py`

- [ ] **Step 1: Rewrite lifecycle.py**

Key changes:
- `request_start()` → build `SyncDesiredStateFrame` dict, send via machine_bus. No token in frame.
- `request_stop()` → send `sync_desired_state` with `desired_state="stopped"`.
- Remove `on_agent_started()`, `on_agent_crashed()`, `on_agent_stopped()` — replaced by `handle_report_actual_state()`.
- Add `handle_token_request()` — issue tokens and send `token_grant`.
- Add `handle_request_replacement()` — re-place agent on different machine.
- Add `_build_sync_frame()` helper — builds complete sync frame from DB.
- Add `send_sync_batch()` — sends all agents for a machine.
- Increment `generation` inside `request_start` when agent is newly started, and expose `bump_generation()` for config changes.

```python
"""Agent lifecycle: declarative desired-state model.

Server owns desired state and pushes sync frames to machines.
Machines own actual state and report back.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from doorae.auth.token import generate_token, hash_agent_token
from doorae.db.models import ActivityLog, Agent, AgentFile, AgentToken, Participant, Room
from doorae.scheduler.machine_bus import MachineBus
from doorae.scheduler.placement import NoSuitableMachineError, select_machine_for

logger = structlog.get_logger(__name__)


class AgentLifecycle:
    """Declarative agent lifecycle — pushes desired state to machines."""

    def __init__(self, db_factory, machine_bus: MachineBus, server_url: str = "") -> None:
        self._db_factory = db_factory
        self._machine_bus = machine_bus
        self._server_url = server_url

    async def request_start(self, agent_id: str) -> None:
        """Place agent on a machine and push sync_desired_state."""
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                logger.error("lifecycle.agent_not_found", agent_id=agent_id)
                return

            rooms = await self._get_agent_rooms(db, agent_id)
            if not rooms:
                agent.actual_state = "pending"
                agent.desired_state = "running"
                agent.last_crash_reason = (
                    "no rooms assigned — add the agent to at least one room "
                    "before starting"
                )
                await db.commit()
                return

            try:
                machine = await select_machine_for(agent.engine, db, self._machine_bus)
            except NoSuitableMachineError:
                logger.warning("lifecycle.no_machine", agent_id=agent_id, engine=agent.engine)
                agent.actual_state = "pending"
                await db.commit()
                return

            agent.placed_on_machine_id = machine.id
            agent.desired_state = "running"
            agent.actual_state = "pending"
            agent.generation += 1
            agent.started_at = datetime.now(timezone.utc)
            await db.commit()

            sync_frame = await self._build_sync_frame(db, agent, rooms)
            await db.commit()

        await self._machine_bus.send(machine.id, sync_frame)

    async def request_stop(self, agent_id: str) -> None:
        """Push sync_desired_state with desired_state=stopped."""
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                return

            agent.desired_state = "stopped"
            agent.actual_state = "stopping"
            db.add(ActivityLog(agent_id=agent_id, event_type="stop_requested"))
            await db.commit()

            if agent.placed_on_machine_id:
                sync_frame = {
                    "type": "sync_desired_state",
                    "agent_id": agent.id,
                    "desired_state": "stopped",
                    "generation": agent.generation,
                }
                await self._machine_bus.send(agent.placed_on_machine_id, sync_frame)

    async def handle_report_actual_state(
        self, machine_id: str, agents_data: list[dict],
    ) -> None:
        """Update DB with actual states reported by machine."""
        async with self._db_factory() as db:
            for agent_data in agents_data:
                aid = agent_data.get("agent_id")
                if not aid:
                    continue
                agent = await self._get_agent(db, aid)
                if agent is None:
                    continue
                agent.actual_state = agent_data.get("actual_state", agent.actual_state)
                agent.pid = agent_data.get("pid")
                if agent_data.get("last_crash_reason"):
                    agent.last_crash_reason = agent_data["last_crash_reason"]
                agent.last_heartbeat_at = datetime.now(timezone.utc)
            await db.commit()

    async def handle_token_request(
        self, machine_id: str, agent_ids: list[str],
    ) -> list[dict]:
        """Issue fresh tokens for requested agents. Returns token_grant frames."""
        grants: list[dict] = []
        async with self._db_factory() as db:
            for agent_id in agent_ids:
                agent = await self._get_agent(db, agent_id)
                if agent is None:
                    continue
                # Verify agent is actually placed on this machine
                if agent.placed_on_machine_id != machine_id:
                    logger.warning(
                        "lifecycle.token_request_wrong_machine",
                        agent_id=agent_id,
                        expected=agent.placed_on_machine_id,
                        actual=machine_id,
                    )
                    continue

                token_plain = generate_token()
                token_hash, lookup_hint = hash_agent_token(token_plain)
                db.add(AgentToken(
                    agent_id=agent.id,
                    token_hash=token_hash,
                    lookup_hint=lookup_hint,
                ))
                grants.append({
                    "type": "token_grant",
                    "agent_id": agent.id,
                    "agent_token": token_plain,
                })
            await db.commit()
        return grants

    async def handle_request_replacement(
        self, machine_id: str, agent_id: str, reason: str,
    ) -> None:
        """Re-place an agent on a different machine (crash budget exhausted)."""
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                return
            agent.placed_on_machine_id = None
            agent.actual_state = "pending"
            agent.last_crash_reason = f"replacement requested: {reason}"
            db.add(ActivityLog(
                agent_id=agent_id,
                event_type="replacement_requested",
                details={"reason": reason, "from_machine": machine_id},
            ))
            await db.commit()

        # Re-place on any available machine (may end up back on the same one
        # if it's the only suitable machine)
        await self.request_start(agent_id)

    async def send_sync_batch(self, machine_id: str) -> None:
        """Send sync_batch with all agents placed on this machine."""
        async with self._db_factory() as db:
            result = await db.execute(
                select(Agent).where(Agent.placed_on_machine_id == machine_id)
            )
            agents = result.scalars().all()

            batch_agents = []
            for agent in agents:
                rooms = await self._get_agent_rooms(db, agent.id)
                sync_frame = await self._build_sync_frame(db, agent, rooms)
                batch_agents.append(sync_frame)

        batch = {"type": "sync_batch", "agents": batch_agents}
        await self._machine_bus.send(machine_id, batch)

    async def bump_generation(self, agent_id: str) -> None:
        """Increment generation and push updated sync frame if running."""
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                return
            agent.generation += 1
            await db.commit()

            if agent.desired_state == "running" and agent.placed_on_machine_id:
                rooms = await self._get_agent_rooms(db, agent.id)
                sync_frame = await self._build_sync_frame(db, agent, rooms)
                await self._machine_bus.send(agent.placed_on_machine_id, sync_frame)

    # ── Internal helpers ──────────────────────────────────────────────

    async def _get_agent(self, db: AsyncSession, agent_id: str) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()

    async def _get_agent_rooms(self, db: AsyncSession, agent_id: str) -> list[str]:
        result = await db.execute(
            select(Participant.room_id).where(Participant.agent_id == agent_id)
        )
        return [row[0] for row in result.all()]

    async def _build_sync_frame(
        self, db: AsyncSession, agent: Agent, rooms: list[str],
    ) -> dict:
        """Build a sync_desired_state dict from DB agent + rooms."""
        file_rows = (
            await db.execute(
                select(AgentFile).where(AgentFile.agent_id == agent.id)
            )
        ).scalars().all()
        files_map = {row.path: row.content for row in file_rows}

        sub_rooms: list[dict] = []
        if rooms:
            sub_result = await db.execute(
                select(Room.name, Room.description)
                .where(Room.parent_room_id.in_(rooms))
                .order_by(Room.name)
            )
            for name, desc in sub_result.all():
                sub_rooms.append({"name": name, "description": desc})

        return {
            "type": "sync_desired_state",
            "agent_id": agent.id,
            "desired_state": agent.desired_state,
            "generation": agent.generation,
            "engine": agent.engine,
            "name": agent.name,
            "profile_yaml": agent.profile_yaml or "",
            "rooms": rooms,
            "agents_md": agent.agents_md,
            "files": files_map,
            "engine_secrets": {},
            "reasoning_effort": agent.reasoning_effort,
            "sub_rooms": sub_rooms,
            "restart_policy": agent.restart_policy,
            "max_restarts": agent.max_restarts,
            "restart_window_seconds": agent.restart_window_seconds,
        }
```

- [ ] **Step 2: Run server tests**

Run: `cd doorae-server && uv run pytest -v`
Expected: `test_machine_handler.py` tests will fail (they reference old lifecycle methods). This is expected — fixed in next task.

- [ ] **Step 3: Commit**

```bash
git add doorae-server/doorae/scheduler/lifecycle.py
git commit -m "feat(server): rewrite lifecycle.py for declarative desired-state model

- request_start sends sync_desired_state instead of spawn_agent
- request_stop sends sync_desired_state(stopped)
- handle_report_actual_state replaces on_agent_started/crashed/stopped
- handle_token_request issues tokens on demand
- handle_request_replacement for crash-budget-exhausted re-placement
- send_sync_batch for reconnection reconcile
- bump_generation for config change propagation"
```

---

### Task 7: Server — Machine Handler Rewrite (doorae-server)

**Files:**
- Modify: `doorae-server/doorae/ws/machine_handler.py`
- Modify: `doorae-server/tests/test_machine_handler.py`

- [ ] **Step 1: Rewrite machine_handler.py frame dispatch**

Replace the frame dispatch section in `ws_machine()` (the `while True` loop body):

```python
            if frame_type == "register":
                await _handle_register(session_factory, machine_id, data)

            elif frame_type == "report_actual_state":
                agents_data = data.get("agents", [])
                await lifecycle.handle_report_actual_state(machine_id, agents_data)
                # After first report, send sync_batch for reconciliation
                await lifecycle.send_sync_batch(machine_id)

            elif frame_type == "token_request":
                agent_ids = data.get("agent_ids", [])
                grants = await lifecycle.handle_token_request(machine_id, agent_ids)
                for grant in grants:
                    await machine_bus.send(machine_id, grant)

            elif frame_type == "request_replacement":
                agent_id = data.get("agent_id", "")
                reason = data.get("reason", "")
                await lifecycle.handle_request_replacement(
                    machine_id, agent_id, reason,
                )

            else:
                logger.warning(
                    "machine_ws.unknown_frame",
                    machine_id=machine_id,
                    frame_type=frame_type,
                )
```

Remove the old `_handle_heartbeat` function entirely.

- [ ] **Step 2: Update machine_handler.py — remove _handle_heartbeat import in tests**

Update the import in `tests/test_machine_handler.py`:

```python
from doorae.ws.machine_handler import _authenticate_machine, _handle_register
```

Remove all tests that reference `_handle_heartbeat`, `on_agent_started`, `on_agent_crashed`, `on_agent_stopped`. Add new tests for:
- `handle_report_actual_state` — updates DB actual_state
- `handle_token_request` — returns token_grant frames
- `send_sync_batch` — sends correct agents for machine
- `handle_request_replacement` — triggers re-placement

- [ ] **Step 3: Run all server tests**

Run: `cd doorae-server && uv run pytest -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add doorae-server/doorae/ws/machine_handler.py doorae-server/tests/test_machine_handler.py
git commit -m "feat(server): rewrite machine_handler for declarative protocol

- report_actual_state replaces heartbeat + agent_started/crashed/stopped
- token_request/token_grant flow for on-demand token issuance
- request_replacement for crash-budget-exhausted re-placement
- sync_batch sent on every report_actual_state for reconciliation"
```

---

### Task 8: Server — Remove Lifespan Reset + Config Generation Bump (doorae-server)

**Files:**
- Modify: `doorae-server/doorae/app.py`
- Modify: `doorae-server/doorae/api/v1/agents.py`

- [ ] **Step 1: Remove stale-state reset in app.py lifespan**

In `doorae-server/doorae/app.py`, remove lines 254-274 (the block that resets agents to pending and machines to offline). Replace with:

```python
    # v2: No stale-state reset. Machines reconnect and report actual state.
    # Server reconciles via sync_batch. Machines that don't reconnect
    # within 5 minutes have their agents flagged for re-placement
    # (handled by the stale-machine background task if enabled).
    if not engine_provided:
        from doorae.db.models import Machine as _Machine
        async with app.state.session_factory() as db:
            from sqlalchemy import update
            # Only reset machines to offline — agents are NOT reset.
            # Machines will re-register and report actual state.
            await db.execute(
                update(_Machine)
                .where(_Machine.status == "online")
                .values(status="offline")
            )
            await db.commit()
            import structlog
            structlog.get_logger().info("startup.machines_reset_offline")
```

- [ ] **Step 2: Add generation bump to agents API on config-changing updates**

In `doorae-server/doorae/api/v1/agents.py`, find the PUT/PATCH endpoint that updates agent config fields (`agents_md`, `profile_yaml`, `engine`, `reasoning_effort`). After saving changes, add:

```python
    # Bump generation and push updated config to machine
    lifecycle = request.app.state.agent_lifecycle
    await lifecycle.bump_generation(agent_id)
```

- [ ] **Step 3: Run all server tests**

Run: `cd doorae-server && uv run pytest -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add doorae-server/doorae/app.py doorae-server/doorae/api/v1/agents.py
git commit -m "feat(server): remove lifespan agent reset, add generation bump on config change

- Server no longer resets all agents to pending on startup
- Machines reset to offline only (they re-register on reconnect)
- Config-changing agent updates bump generation and push sync frame"
```

---

### Task 9: Integration Test — Full Reconcile Cycle

**Files:**
- Create: `doorae-server/tests/test_declarative_reconcile.py`

- [ ] **Step 1: Write integration test**

```python
"""Integration test: declarative desired-state reconcile cycle.

Tests the full flow:
1. Server sends sync_desired_state
2. Machine saves manifest, requests token
3. Server sends token_grant
4. Machine spawns agent, reports actual_state
5. Server updates DB
"""

from __future__ import annotations

import secrets
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, Base, Machine, MachineToken, Participant, Project, Room, User
from doorae.auth.machine_token import generate_machine_token, hash_machine_token
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus


@pytest_asyncio.fixture()
async def env():
    """Set up DB with machine, agent, room for reconcile testing."""
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus, server_url="ws://localhost:8001")

    async with factory() as db:
        user = User(email="test@test.com", password_hash="x", is_admin=True)
        db.add(user)
        await db.flush()

        machine = Machine(name="m1", hostname="localhost", owner_user_id=user.id, status="online", max_agents=4)
        db.add(machine)
        await db.flush()

        project = Project(name="test-project")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="general")
        db.add(room)
        await db.flush()

        agent = Agent(name="test-agent", engine="claude-code", desired_state="idle", actual_state="idle")
        db.add(agent)
        await db.flush()

        db.add(Participant(room_id=room.id, agent_id=agent.id, display_name="test-agent"))
        await db.commit()

        yield {
            "factory": factory,
            "bus": bus,
            "lifecycle": lifecycle,
            "machine": machine,
            "agent": agent,
            "room": room,
        }


class TestDeclarativeReconcile:
    async def test_request_start_sends_sync_desired_state(self, env) -> None:
        """request_start should send sync_desired_state (not spawn_agent)."""
        bus = env["bus"]
        ws_mock = AsyncMock()
        await bus.register(env["machine"].id, ws_mock)

        await env["lifecycle"].request_start(env["agent"].id)

        # Verify sync_desired_state was sent
        ws_mock.send_text.assert_called_once()
        import json
        sent = json.loads(ws_mock.send_text.call_args[0][0])
        assert sent["type"] == "sync_desired_state"
        assert sent["desired_state"] == "running"
        assert sent["generation"] == 1
        assert "agent_token" not in sent

    async def test_request_stop_sends_sync_stopped(self, env) -> None:
        bus = env["bus"]
        ws_mock = AsyncMock()
        await bus.register(env["machine"].id, ws_mock)

        # First start the agent
        await env["lifecycle"].request_start(env["agent"].id)
        ws_mock.reset_mock()

        await env["lifecycle"].request_stop(env["agent"].id)

        ws_mock.send_text.assert_called_once()
        import json
        sent = json.loads(ws_mock.send_text.call_args[0][0])
        assert sent["type"] == "sync_desired_state"
        assert sent["desired_state"] == "stopped"

    async def test_handle_token_request_issues_token(self, env) -> None:
        bus = env["bus"]
        ws_mock = AsyncMock()
        await bus.register(env["machine"].id, ws_mock)

        # Place agent on machine first
        await env["lifecycle"].request_start(env["agent"].id)

        grants = await env["lifecycle"].handle_token_request(
            env["machine"].id, [env["agent"].id],
        )
        assert len(grants) == 1
        assert grants[0]["type"] == "token_grant"
        assert grants[0]["agent_id"] == env["agent"].id
        assert grants[0]["agent_token"].startswith("agt_")

    async def test_handle_report_actual_state_updates_db(self, env) -> None:
        await env["lifecycle"].handle_report_actual_state(
            env["machine"].id,
            [{"agent_id": env["agent"].id, "actual_state": "running", "pid": 12345}],
        )

        async with env["factory"]() as db:
            result = await db.execute(select(Agent).where(Agent.id == env["agent"].id))
            agent = result.scalar_one()
            assert agent.actual_state == "running"
            assert agent.pid == 12345

    async def test_bump_generation_pushes_sync(self, env) -> None:
        bus = env["bus"]
        ws_mock = AsyncMock()
        await bus.register(env["machine"].id, ws_mock)

        # Start agent (gen=1)
        await env["lifecycle"].request_start(env["agent"].id)
        ws_mock.reset_mock()

        # Bump generation
        await env["lifecycle"].bump_generation(env["agent"].id)

        ws_mock.send_text.assert_called_once()
        import json
        sent = json.loads(ws_mock.send_text.call_args[0][0])
        assert sent["type"] == "sync_desired_state"
        assert sent["generation"] == 2
```

- [ ] **Step 2: Run integration tests**

Run: `cd doorae-server && uv run pytest tests/test_declarative_reconcile.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 3: Run full test suite for both packages**

Run: `cd doorae-machine && uv run pytest -v && cd ../doorae-server && uv run pytest -v`
Expected: All tests PASS across both packages.

- [ ] **Step 4: Commit**

```bash
git add doorae-server/tests/test_declarative_reconcile.py
git commit -m "test: integration tests for declarative desired-state reconcile cycle"
```

---

## Verification Checklist

After all tasks are complete, verify these scenarios manually or with the E2E script:

- [ ] Server restart → agents continue running on machine (no restart)
- [ ] Daemon restart → agents respawn from local manifest after token grant
- [ ] Agent crash → machine restarts locally (no server round-trip)
- [ ] Crash budget exceeded → `request_replacement` sent to server
- [ ] Config change on server → generation bump → agent auto-restarts on machine
- [ ] Orphan agent (server deleted, machine still running) → killed on sync_batch
- [ ] Network reconnect → bidirectional diff reconcile converges
