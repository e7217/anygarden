"""Tests for the declarative WebSocket daemon."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doorae_machine.daemon import MachineDaemon, _base_url_from_machine_url
from doorae_machine.protocol.frames import (
    RegisterFrame,
    ReportActualStateFrame,
    SyncDesiredStateFrame,
)


class TestBaseUrlFromMachineUrl:
    """The daemon derives the agent dial-back URL by trimming the
    ``/ws/machines/<id>`` endpoint suffix off its own connection URL.
    Anything else in the path is operator intent (reverse-proxy
    prefix, API version segment, ...) and must be preserved — otherwise
    agents can't reach the server through the same proxy the daemon
    uses.
    """

    def test_bare_host_port(self) -> None:
        assert (
            _base_url_from_machine_url("ws://localhost:8001/ws/machines/abc")
            == "ws://localhost:8001"
        )

    def test_preserves_reverse_proxy_prefix(self) -> None:
        assert (
            _base_url_from_machine_url(
                "wss://proxy.example.com/doorae/ws/machines/abc-123"
            )
            == "wss://proxy.example.com/doorae"
        )

    def test_preserves_multi_segment_prefix(self) -> None:
        assert (
            _base_url_from_machine_url(
                "wss://edge.example.com/api/v1/ws/machines/xyz"
            )
            == "wss://edge.example.com/api/v1"
        )

    def test_passes_through_url_without_machine_suffix(self) -> None:
        # Older/custom daemons may register with a bare origin; leave it alone.
        assert (
            _base_url_from_machine_url("ws://localhost:8001")
            == "ws://localhost:8001"
        )

    def test_empty_input_stays_empty(self) -> None:
        assert _base_url_from_machine_url("") == ""

    def test_non_url_input_stays_empty(self) -> None:
        assert _base_url_from_machine_url("not a url") == ""


@pytest.fixture
def daemon(tmp_path: Path) -> MachineDaemon:
    """Create a MachineDaemon with test configuration."""
    return MachineDaemon(
        server_url="wss://localhost:8000/ws/machines/machine-test-001",
        machine_id="machine-test-001",
        machine_token="test-machine-token",
        labels={"region": "local"},
        agent_dirs_root=tmp_path / "agents",
    )


def _capture_ws(daemon: MachineDaemon) -> list[dict]:
    """Wire up a mock WS that captures sent frames."""
    sent_frames: list[dict] = []
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(
        side_effect=lambda data: sent_frames.append(json.loads(data))
    )
    daemon._ws = mock_ws
    return sent_frames


# ── Registration ──────────────────────────────────────────────────────


class TestRegisterFrame:
    """Tests for machine registration."""

    async def test_register_sends_frame(self, daemon: MachineDaemon) -> None:
        """_register should send a RegisterFrame with capabilities."""
        sent_frames = _capture_ws(daemon)

        mock_detection = MagicMock()
        mock_detection.engines = [
            MagicMock(engine="claude-code", version="1.0.0", path="/usr/bin/claude-code"),
        ]

        with patch("doorae_machine.daemon.detect_engines", return_value=mock_detection):
            await daemon._register()

        assert len(sent_frames) == 1
        frame = sent_frames[0]
        assert frame["type"] == "register"
        assert frame["machine_id"] == "machine-test-001"
        assert len(frame["capabilities"]) == 1
        assert frame["capabilities"][0]["engine"] == "claude-code"


# ── Report actual state ──────────────────────────────────────────────


class TestReportActualState:
    """Tests for report_actual_state mechanism."""

    async def test_report_includes_running_agents(self, daemon: MachineDaemon) -> None:
        """Report should include the list of running agents."""
        sent_frames = _capture_ws(daemon)

        # Mock spawner to return some agents
        daemon._spawner.list_running = MagicMock(return_value=[
            {"agent_id": "a1", "pid": 100, "engine": "claude-code", "uptime_seconds": 60},
        ])
        daemon._running_generations["a1"] = 3

        await daemon._report_actual_state()

        assert len(sent_frames) == 1
        report = sent_frames[0]
        assert report["type"] == "report_actual_state"
        assert len(report["agents"]) == 1
        assert report["agents"][0]["agent_id"] == "a1"
        assert report["agents"][0]["actual_state"] == "running"
        assert report["agents"][0]["generation"] == 3

    async def test_report_empty_when_no_agents(self, daemon: MachineDaemon) -> None:
        """Report should send empty agents list when nothing is running."""
        sent_frames = _capture_ws(daemon)

        await daemon._report_actual_state()

        assert len(sent_frames) == 1
        report = sent_frames[0]
        assert report["type"] == "report_actual_state"
        assert report["agents"] == []


# ── Transitional states (#219) ───────────────────────────────────────
#
# `_transitional_states` holds the short-lived ``starting`` / ``stopping``
# annotations for agents whose spawn or kill is in flight. Without it
# admins only see ``running`` → (30s gap) → ``stopped`` because the
# daemon's periodic report runs on a 30s cadence and never emits the
# in-flight states.


class TestTransitionalStatesReport:
    """Transitional states feed into ``_report_actual_state`` output."""

    async def test_starting_emitted_when_spawn_in_flight(
        self, daemon: MachineDaemon
    ) -> None:
        """Agent with an in-flight spawn (not yet running) reports as starting."""
        sent_frames = _capture_ws(daemon)

        # Process hasn't come up yet, but a spawn is dispatched.
        daemon._spawner.list_running = MagicMock(return_value=[])
        daemon._transitional_states["a-new"] = "starting"

        await daemon._report_actual_state()

        report = sent_frames[0]
        assert len(report["agents"]) == 1
        assert report["agents"][0]["agent_id"] == "a-new"
        assert report["agents"][0]["actual_state"] == "starting"

    async def test_stopping_emitted_while_process_still_alive(
        self, daemon: MachineDaemon
    ) -> None:
        """Kill is dispatched but the process hasn't exited — report stopping."""
        sent_frames = _capture_ws(daemon)

        daemon._spawner.list_running = MagicMock(return_value=[
            {"agent_id": "a-dying", "pid": 42, "engine": "claude-code", "uptime_seconds": 5},
        ])
        daemon._transitional_states["a-dying"] = "stopping"

        await daemon._report_actual_state()

        report = sent_frames[0]
        states = {a["agent_id"]: a["actual_state"] for a in report["agents"]}
        assert states == {"a-dying": "stopping"}

    async def test_running_wins_when_no_transitional_entry(
        self, daemon: MachineDaemon
    ) -> None:
        """Regression guard: normal running agents still reported as running."""
        sent_frames = _capture_ws(daemon)

        daemon._spawner.list_running = MagicMock(return_value=[
            {"agent_id": "a-ok", "pid": 1, "engine": "x", "uptime_seconds": 1},
        ])
        # No transitional entry.

        await daemon._report_actual_state()

        report = sent_frames[0]
        assert report["agents"][0]["actual_state"] == "running"


class TestTransitionalStatesLifecycle:
    """Transitional state is set on dispatch and cleared on callback."""

    async def test_stop_reconcile_emits_stopping_before_kill(
        self, daemon: MachineDaemon
    ) -> None:
        """When desired=stopped and the agent is running, the daemon must
        emit a ``stopping`` report BEFORE ``spawner.kill`` returns — so
        admins see the transition in under 2s instead of waiting for the
        next periodic report (30s)."""
        sent_frames = _capture_ws(daemon)

        # Pretend agent is running.
        mock_running = MagicMock()
        mock_running.agent_id = "a-bye"
        daemon._spawner.get_running = MagicMock(return_value=mock_running)
        daemon._spawner.list_running = MagicMock(return_value=[
            {"agent_id": "a-bye", "pid": 99, "engine": "x", "uptime_seconds": 5},
        ])

        # Capture report frames seen at the moment kill() is invoked.
        reports_at_kill: list[dict] = []

        async def record_then_succeed(agent_id: str) -> dict:
            reports_at_kill.extend(
                f for f in sent_frames if f["type"] == "report_actual_state"
            )
            return {"success": True}

        daemon._spawner.kill = AsyncMock(side_effect=record_then_succeed)

        # Save a stopped manifest and run reconcile.
        manifest = SyncDesiredStateFrame(
            agent_id="a-bye",
            desired_state="stopped",
            generation=2,
            engine="x",
        )
        daemon._manifest_store.save(manifest)
        daemon._running_generations["a-bye"] = 1

        await daemon._reconcile_agent("a-bye")

        # By the time kill ran there was already a stopping report.
        assert reports_at_kill, "no report sent before kill dispatched"
        latest = reports_at_kill[-1]
        states = {a["agent_id"]: a["actual_state"] for a in latest["agents"]}
        assert states.get("a-bye") == "stopping"

    async def test_on_agent_stopped_clears_transitional(
        self, daemon: MachineDaemon
    ) -> None:
        """After the normal-exit callback, the transitional map must drop
        the entry so the next report correctly treats the agent as absent
        (→ server converges to ``stopped`` via the absent-from-report
        branch)."""
        _capture_ws(daemon)
        daemon._transitional_states["a-done"] = "stopping"
        daemon._running_generations["a-done"] = 1

        await daemon._on_agent_stopped("a-done", 0)

        assert "a-done" not in daemon._transitional_states

    async def test_on_agent_crashed_clears_transitional(
        self, daemon: MachineDaemon
    ) -> None:
        """Crash path must also release the transitional map entry so a
        leaked ``starting`` doesn't linger across the crash-restart."""
        _capture_ws(daemon)

        manifest = SyncDesiredStateFrame(
            agent_id="a-boom",
            desired_state="running",
            generation=1,
            engine="x",
            restart_policy="stop",
        )
        daemon._manifest_store.save(manifest)
        daemon._transitional_states["a-boom"] = "starting"

        await daemon._on_agent_crashed("a-boom", 1, "segfault")

        assert "a-boom" not in daemon._transitional_states


# ── Sync desired state ───────────────────────────────────────────────


class TestSyncDesiredState:
    """Tests for handling sync_desired_state frames."""

    async def test_sync_running_requests_token_and_spawns(
        self, daemon: MachineDaemon
    ) -> None:
        """sync_desired_state with desired=running should request token,
        then spawn the agent when token_grant arrives."""
        sent_frames = _capture_ws(daemon)

        # Mock the spawner.spawn to succeed
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.agent_id = "agent-001"
        mock_result.pid = 42
        daemon._spawner.spawn = AsyncMock(return_value=mock_result)

        sync_data = {
            "type": "sync_desired_state",
            "agent_id": "agent-001",
            "desired_state": "running",
            "generation": 1,
            "engine": "claude-code",
            "name": "test-agent",
            "profile_yaml": "name: x",
            "rooms": ["room-1"],
        }

        # Handle the sync in a task so we can inject the token grant
        async def handle_and_grant():
            # Small delay so handle starts waiting for the token
            await asyncio.sleep(0.01)
            # Now simulate the server sending a token_grant
            grant_data = {
                "type": "token_grant",
                "agent_id": "agent-001",
                "agent_token": "tok-abc",
            }
            await daemon._handle(grant_data)

        handle_task = asyncio.create_task(daemon._handle(sync_data))
        grant_task = asyncio.create_task(handle_and_grant())

        await asyncio.gather(handle_task, grant_task)

        # Should have sent token_request + report_actual_state
        token_requests = [f for f in sent_frames if f["type"] == "token_request"]
        assert len(token_requests) == 1
        assert "agent-001" in token_requests[0]["agent_ids"]

        # Spawner should have been called with the right parameters
        daemon._spawner.spawn.assert_called_once()
        spawn_arg = daemon._spawner.spawn.call_args[0][0]
        assert spawn_arg.agent_id == "agent-001"
        assert spawn_arg.agent_token == "tok-abc"
        assert spawn_arg.engine == "claude-code"

        # Generation should be tracked
        assert daemon._running_generations.get("agent-001") == 1

    async def test_sync_stopped_kills_running_agent(
        self, daemon: MachineDaemon
    ) -> None:
        """sync_desired_state with desired=stopped should kill the running agent."""
        sent_frames = _capture_ws(daemon)

        # Pretend agent is running
        mock_running = MagicMock()
        mock_running.agent_id = "agent-001"
        daemon._spawner.get_running = MagicMock(return_value=mock_running)
        daemon._spawner.kill = AsyncMock(return_value={"success": True})
        daemon._running_generations["agent-001"] = 1

        sync_data = {
            "type": "sync_desired_state",
            "agent_id": "agent-001",
            "desired_state": "stopped",
            "generation": 2,
        }
        await daemon._handle(sync_data)

        daemon._spawner.kill.assert_called_once_with("agent-001")
        assert "agent-001" not in daemon._running_generations

    async def test_sync_same_generation_is_noop(
        self, daemon: MachineDaemon
    ) -> None:
        """If agent is already running at the desired generation, do nothing."""
        sent_frames = _capture_ws(daemon)

        mock_running = MagicMock()
        mock_running.agent_id = "agent-001"
        daemon._spawner.get_running = MagicMock(return_value=mock_running)
        daemon._spawner.spawn = AsyncMock()
        daemon._running_generations["agent-001"] = 3

        sync_data = {
            "type": "sync_desired_state",
            "agent_id": "agent-001",
            "desired_state": "running",
            "generation": 3,
            "engine": "claude-code",
        }
        await daemon._handle(sync_data)

        # Spawn should NOT have been called
        daemon._spawner.spawn.assert_not_called()

    async def test_sync_newer_generation_restarts(
        self, daemon: MachineDaemon
    ) -> None:
        """If a newer generation arrives, kill the old and respawn."""
        sent_frames = _capture_ws(daemon)

        mock_running = MagicMock()
        mock_running.agent_id = "agent-001"
        daemon._spawner.get_running = MagicMock(return_value=mock_running)
        daemon._spawner.kill = AsyncMock(return_value={"success": True})

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.agent_id = "agent-001"
        mock_result.pid = 99
        daemon._spawner.spawn = AsyncMock(return_value=mock_result)

        daemon._running_generations["agent-001"] = 1

        sync_data = {
            "type": "sync_desired_state",
            "agent_id": "agent-001",
            "desired_state": "running",
            "generation": 2,
            "engine": "claude-code",
        }

        async def handle_and_grant():
            await asyncio.sleep(0.01)
            grant_data = {
                "type": "token_grant",
                "agent_id": "agent-001",
                "agent_token": "tok-new",
            }
            await daemon._handle(grant_data)

        handle_task = asyncio.create_task(daemon._handle(sync_data))
        grant_task = asyncio.create_task(handle_and_grant())

        await asyncio.gather(handle_task, grant_task)

        daemon._spawner.kill.assert_called_once_with("agent-001")
        daemon._spawner.spawn.assert_called_once()
        assert daemon._running_generations.get("agent-001") == 2


# ── Per-agent reconcile serialization (#183) ─────────────────────────


class TestReconcileSerialization:
    """#183 — generation pre-reservation and per-agent lock close the
    race window where two ``sync_desired_state`` frames arriving back
    to back dispatched two concurrent spawn tasks for the same agent.
    """

    async def test_duplicate_same_generation_spawns_once(
        self, daemon: MachineDaemon
    ) -> None:
        """Two ``sync_desired_state`` frames for the same agent at the
        same generation that arrive while the first spawn is still
        awaiting its token_grant must NOT dispatch a second spawn. The
        generation reservation happens synchronously inside the lock
        before ``create_task`` is called.
        """
        sent_frames = _capture_ws(daemon)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.agent_id = "agent-race"
        mock_result.pid = 42
        daemon._spawner.spawn = AsyncMock(return_value=mock_result)

        base = {
            "type": "sync_desired_state",
            "agent_id": "agent-race",
            "desired_state": "running",
            "engine": "claude-code",
            "name": "a",
            "profile_yaml": "",
            "rooms": [],
        }

        async def grant_after_delay():
            # Wait long enough for BOTH sync frames to have been handled
            # before we resolve the single token grant.
            await asyncio.sleep(0.05)
            grant = {
                "type": "token_grant",
                "agent_id": "agent-race",
                "agent_token": "tok-once",
            }
            await daemon._handle(grant)

        t_first = asyncio.create_task(
            daemon._handle({**base, "generation": 1})
        )
        t_second = asyncio.create_task(
            daemon._handle({**base, "generation": 1})
        )
        t_grant = asyncio.create_task(grant_after_delay())
        await asyncio.gather(t_first, t_second, t_grant)

        # Only one spawn dispatched despite two reconcile requests.
        assert daemon._spawner.spawn.call_count == 1
        # Only one token request sent.
        token_reqs = [f for f in sent_frames if f["type"] == "token_request"]
        assert len(token_reqs) == 1

    async def test_stale_generation_ignored_when_reservation_higher(
        self, daemon: MachineDaemon
    ) -> None:
        """A reconcile at generation N when generation N+k is already
        reserved (spawn in flight OR completed) must short-circuit: no
        kill, no spawn, no token request.
        """
        sent_frames = _capture_ws(daemon)
        daemon._spawner.spawn = AsyncMock()
        daemon._spawner.kill = AsyncMock()

        # Pre-reserve gen 5 as if a spawn is already in flight / done.
        daemon._running_generations["agent-stale"] = 5

        sync_stale = {
            "type": "sync_desired_state",
            "agent_id": "agent-stale",
            "desired_state": "running",
            "generation": 3,  # older
            "engine": "claude-code",
            "profile_yaml": "",
            "rooms": [],
        }
        # Save the manifest (what _handle does) — but since the test
        # mutates state directly, use save directly too.
        from doorae_machine.protocol.frames import SyncDesiredStateFrame

        daemon._manifest_store.save(
            SyncDesiredStateFrame(
                agent_id="agent-stale",
                desired_state="running",
                generation=3,
                engine="claude-code",
            )
        )

        await daemon._reconcile_agent("agent-stale")

        daemon._spawner.spawn.assert_not_called()
        daemon._spawner.kill.assert_not_called()
        # Reservation untouched.
        assert daemon._running_generations["agent-stale"] == 5

    async def test_spawn_failure_rolls_back_reservation(
        self, daemon: MachineDaemon
    ) -> None:
        """If ``Spawner.spawn`` fails, the pre-reservation in
        ``_running_generations`` must be rolled back so a subsequent
        reconcile can retry rather than seeing a phantom running agent.
        """
        sent_frames = _capture_ws(daemon)

        fail = MagicMock()
        fail.success = False
        fail.agent_id = "agent-broken"
        fail.error = "spawn refused"
        daemon._spawner.spawn = AsyncMock(return_value=fail)

        sync = {
            "type": "sync_desired_state",
            "agent_id": "agent-broken",
            "desired_state": "running",
            "generation": 7,
            "engine": "claude-code",
            "profile_yaml": "",
            "rooms": [],
        }

        async def grant():
            await asyncio.sleep(0.01)
            await daemon._handle(
                {
                    "type": "token_grant",
                    "agent_id": "agent-broken",
                    "agent_token": "tok-bad",
                }
            )

        await asyncio.gather(
            daemon._handle(sync),
            grant(),
        )

        # Spawn was attempted
        daemon._spawner.spawn.assert_called_once()
        # And the reservation was cleaned up on failure — a retry
        # (re-send of the same frame) must be able to try again.
        assert "agent-broken" not in daemon._running_generations

    async def test_parallel_reconcile_different_agents(
        self, daemon: MachineDaemon
    ) -> None:
        """Different agents must NOT block each other — each lock is
        per-agent so the daemon can reconcile agents in parallel. We
        verify by arranging the grant arrival order to match the
        expected progression and confirming both spawns happen.
        """
        daemon._spawner.spawn = AsyncMock(
            side_effect=lambda m: MagicMock(
                success=True, agent_id=m.agent_id, pid=100, error=""
            )
        )

        base = {
            "type": "sync_desired_state",
            "desired_state": "running",
            "engine": "claude-code",
            "profile_yaml": "",
            "rooms": [],
            "generation": 1,
        }

        async def grant_both():
            await asyncio.sleep(0.02)
            for aid, tok in (("agent-a", "tok-a"), ("agent-b", "tok-b")):
                await daemon._handle(
                    {
                        "type": "token_grant",
                        "agent_id": aid,
                        "agent_token": tok,
                    }
                )

        await asyncio.gather(
            daemon._handle({**base, "agent_id": "agent-a"}),
            daemon._handle({**base, "agent_id": "agent-b"}),
            grant_both(),
        )

        assert daemon._spawner.spawn.call_count == 2
        assert daemon._running_generations["agent-a"] == 1
        assert daemon._running_generations["agent-b"] == 1


# ── Sync batch ───────────────────────────────────────────────────────


class TestSyncBatch:
    """Tests for handling sync_batch frames."""

    async def test_batch_kills_orphans(self, daemon: MachineDaemon) -> None:
        """Agents running locally but not in the batch should be killed."""
        sent_frames = _capture_ws(daemon)

        # Pretend two agents are running locally
        daemon._spawner.list_running = MagicMock(return_value=[
            {"agent_id": "agent-keep", "pid": 100, "engine": "claude-code", "uptime_seconds": 60},
            {"agent_id": "agent-orphan", "pid": 200, "engine": "codex", "uptime_seconds": 30},
        ])
        daemon._spawner.kill = AsyncMock(return_value={"success": True})
        daemon._spawner.get_running = MagicMock(return_value=None)
        daemon._running_generations["agent-keep"] = 1
        daemon._running_generations["agent-orphan"] = 1

        # Mock spawner.spawn (won't be called since agents are not running after kill)
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.agent_id = "agent-keep"
        mock_result.pid = 100
        daemon._spawner.spawn = AsyncMock(return_value=mock_result)

        batch_data = {
            "type": "sync_batch",
            "agents": [
                {
                    "type": "sync_desired_state",
                    "agent_id": "agent-keep",
                    "desired_state": "running",
                    "generation": 1,
                    "engine": "claude-code",
                },
            ],
        }

        async def feed_token():
            await asyncio.sleep(0.01)
            grant_data = {
                "type": "token_grant",
                "agent_id": "agent-keep",
                "agent_token": "tok-keep",
            }
            await daemon._handle(grant_data)

        handle_task = asyncio.create_task(daemon._handle(batch_data))
        token_task = asyncio.create_task(feed_token())

        await asyncio.gather(handle_task, token_task)

        # agent-orphan should have been killed
        kill_calls = [
            call.args[0] for call in daemon._spawner.kill.call_args_list
        ]
        assert "agent-orphan" in kill_calls
        assert "agent-orphan" not in daemon._running_generations

    async def test_partial_batch_does_not_kill_orphans(
        self, daemon: MachineDaemon
    ) -> None:
        """#185: A ``sync_batch`` with ``is_full_snapshot=False`` must
        NOT kill agents missing from the batch. The server only listed
        the agents it's updating — everything else should keep running.
        A server bug that sent a bogus partial batch (e.g. a failed
        query) previously caused mass kill of all local agents.
        """
        daemon._spawner.list_running = MagicMock(return_value=[
            {"agent_id": "agent-keep", "pid": 100, "engine": "claude-code", "uptime_seconds": 60},
            {"agent_id": "agent-untouched", "pid": 200, "engine": "codex", "uptime_seconds": 30},
        ])
        daemon._spawner.kill = AsyncMock()
        daemon._spawner.get_running = MagicMock(return_value=None)
        daemon._running_generations["agent-keep"] = 1
        daemon._running_generations["agent-untouched"] = 1

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.pid = 100
        daemon._spawner.spawn = AsyncMock(return_value=mock_result)

        batch_data = {
            "type": "sync_batch",
            "is_full_snapshot": False,
            "agents": [
                {
                    "type": "sync_desired_state",
                    "agent_id": "agent-keep",
                    "desired_state": "running",
                    "generation": 2,
                    "engine": "claude-code",
                },
            ],
        }

        async def feed_token():
            await asyncio.sleep(0.01)
            await daemon._handle(
                {
                    "type": "token_grant",
                    "agent_id": "agent-keep",
                    "agent_token": "tok-keep",
                }
            )

        await asyncio.gather(
            daemon._handle(batch_data),
            feed_token(),
        )

        # Untouched agent must NOT be killed — it's outside the partial
        # batch's scope, not an orphan.
        daemon._spawner.kill.assert_not_called()
        assert daemon._running_generations["agent-untouched"] == 1

    async def test_empty_partial_batch_kills_nothing(
        self, daemon: MachineDaemon
    ) -> None:
        """#185: The core regression guard — an empty
        ``is_full_snapshot=False`` batch from a server-side bug (empty
        result set, failed filter, etc.) used to mass-kill every agent
        on the machine. With the flag, it's now a no-op.
        """
        daemon._spawner.list_running = MagicMock(return_value=[
            {"agent_id": "agent-a", "pid": 100, "engine": "claude-code", "uptime_seconds": 60},
            {"agent_id": "agent-b", "pid": 200, "engine": "codex", "uptime_seconds": 30},
        ])
        daemon._spawner.kill = AsyncMock()
        daemon._running_generations["agent-a"] = 1
        daemon._running_generations["agent-b"] = 1

        batch_data = {
            "type": "sync_batch",
            "is_full_snapshot": False,
            "agents": [],
        }
        await daemon._handle(batch_data)

        daemon._spawner.kill.assert_not_called()
        assert daemon._running_generations == {"agent-a": 1, "agent-b": 1}

    async def test_empty_full_snapshot_kills_all(
        self, daemon: MachineDaemon
    ) -> None:
        """Sanity: the original behaviour must remain for
        ``is_full_snapshot=True`` empty batches — used when the server
        drops a machine's entire agent set (depopulation, reassignment).
        """
        daemon._spawner.list_running = MagicMock(return_value=[
            {"agent_id": "agent-a", "pid": 100, "engine": "claude-code", "uptime_seconds": 60},
        ])
        daemon._spawner.kill = AsyncMock(return_value={"success": True})
        daemon._running_generations["agent-a"] = 1

        batch_data = {
            "type": "sync_batch",
            "is_full_snapshot": True,
            "agents": [],
        }
        await daemon._handle(batch_data)

        daemon._spawner.kill.assert_called_once_with("agent-a")
        assert "agent-a" not in daemon._running_generations


# ── Token grant ──────────────────────────────────────────────────────


class TestTokenGrant:
    """Tests for token_grant handling."""

    async def test_token_grant_resolves_future(self, daemon: MachineDaemon) -> None:
        """token_grant should resolve the pending future for that agent."""
        _capture_ws(daemon)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        daemon._token_futures["agent-001"] = future

        grant_data = {
            "type": "token_grant",
            "agent_id": "agent-001",
            "agent_token": "tok-123",
        }
        await daemon._handle(grant_data)

        assert future.done()
        assert future.result() == "tok-123"

    async def test_unexpected_token_grant_is_ignored(
        self, daemon: MachineDaemon
    ) -> None:
        """token_grant for an agent we didn't request should be harmless."""
        _capture_ws(daemon)

        grant_data = {
            "type": "token_grant",
            "agent_id": "unknown-agent",
            "agent_token": "tok-xyz",
        }
        # Should not raise
        await daemon._handle(grant_data)


# ── Crash handling ───────────────────────────────────────────────────


class TestCrashHandling:
    """Tests for crash restart logic."""

    async def test_crash_restart_within_budget(self, daemon: MachineDaemon) -> None:
        """Agent crash with budget remaining should trigger restart."""
        sent_frames = _capture_ws(daemon)

        # Save a manifest that wants the agent running
        manifest = SyncDesiredStateFrame(
            agent_id="agent-crash",
            desired_state="running",
            generation=1,
            engine="claude-code",
            restart_policy="restart_on_same_machine",
            max_restarts=3,
            restart_window_seconds=300,
        )
        daemon._manifest_store.save(manifest)

        # Mock spawner for the restart
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.agent_id = "agent-crash"
        mock_result.pid = 99
        daemon._spawner.spawn = AsyncMock(return_value=mock_result)

        # Trigger crash callback
        async def crash_and_grant():
            task = asyncio.create_task(
                daemon._on_agent_crashed("agent-crash", 1, "segfault")
            )
            await asyncio.sleep(0.01)
            # Feed the token for the restart
            grant_data = {
                "type": "token_grant",
                "agent_id": "agent-crash",
                "agent_token": "tok-restart",
            }
            await daemon._handle(grant_data)
            await task

        await crash_and_grant()

        # Should have spawned a restart
        daemon._spawner.spawn.assert_called_once()
        spawn_arg = daemon._spawner.spawn.call_args[0][0]
        assert spawn_arg.agent_token == "tok-restart"

    async def test_crash_budget_exhausted_restart_anywhere(
        self, daemon: MachineDaemon
    ) -> None:
        """When crash budget is exhausted and policy is restart_anywhere,
        should send RequestReplacementFrame."""
        sent_frames = _capture_ws(daemon)

        manifest = SyncDesiredStateFrame(
            agent_id="agent-crash",
            desired_state="running",
            generation=1,
            engine="claude-code",
            restart_policy="restart_anywhere",
            max_restarts=1,
            restart_window_seconds=300,
        )
        daemon._manifest_store.save(manifest)

        # First crash — allowed
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.agent_id = "agent-crash"
        mock_result.pid = 100
        daemon._spawner.spawn = AsyncMock(return_value=mock_result)

        async def crash_and_grant():
            task = asyncio.create_task(
                daemon._on_agent_crashed("agent-crash", 1, "error1")
            )
            await asyncio.sleep(0.01)
            grant = {
                "type": "token_grant",
                "agent_id": "agent-crash",
                "agent_token": "tok-1",
            }
            await daemon._handle(grant)
            await task

        await crash_and_grant()
        assert daemon._spawner.spawn.call_count == 1

        # Second crash — budget exhausted
        daemon._spawner.spawn.reset_mock()
        await daemon._on_agent_crashed("agent-crash", 1, "error2")

        # Should NOT have spawned again
        daemon._spawner.spawn.assert_not_called()

        # Should have sent a request_replacement frame
        replacement_frames = [
            f for f in sent_frames if f["type"] == "request_replacement"
        ]
        assert len(replacement_frames) == 1
        assert replacement_frames[0]["agent_id"] == "agent-crash"

        # Manifest should be marked stopped so daemon restart does not
        # re-spawn this agent behind the server's back (#182).
        reloaded = daemon._manifest_store.load("agent-crash")
        assert reloaded is not None
        assert reloaded.desired_state == "stopped"

    async def test_request_replacement_survives_missing_manifest(
        self, daemon: MachineDaemon
    ) -> None:
        """If the manifest was already deleted when replacement fires,
        the daemon must not crash on the FileNotFoundError path (#182).
        """
        sent_frames = _capture_ws(daemon)

        manifest = SyncDesiredStateFrame(
            agent_id="agent-ghost",
            desired_state="running",
            generation=1,
            engine="claude-code",
            restart_policy="restart_anywhere",
            max_restarts=0,  # budget exhausted on first crash
            restart_window_seconds=300,
        )
        daemon._manifest_store.save(manifest)
        # Simulate the manifest being removed between save and crash —
        # e.g. operator clean-up or a prior stop_agent flow.
        daemon._manifest_store.delete("agent-ghost")

        # Re-inject an in-memory manifest so the crash path can still load
        # its restart_policy. We bypass the file and re-save then delete
        # the desired_state field mid-flight by patching load() to return
        # the known manifest once, then the real (absent) file afterwards.
        real_load = daemon._manifest_store.load
        calls = {"n": 0}

        def _patched_load(agent_id: str):
            calls["n"] += 1
            if calls["n"] == 1:
                return manifest
            return real_load(agent_id)

        daemon._manifest_store.load = _patched_load  # type: ignore[assignment]

        await daemon._on_agent_crashed("agent-ghost", 1, "boom")

        # request_replacement still fires even though update_desired_state
        # raises FileNotFoundError internally (caught by the fix).
        replacement_frames = [
            f for f in sent_frames if f["type"] == "request_replacement"
        ]
        assert len(replacement_frames) == 1

    async def test_crash_with_stop_policy_does_not_restart(
        self, daemon: MachineDaemon
    ) -> None:
        """When restart_policy is stop, crashes should just report state."""
        sent_frames = _capture_ws(daemon)

        manifest = SyncDesiredStateFrame(
            agent_id="agent-stop",
            desired_state="running",
            generation=1,
            engine="claude-code",
            restart_policy="stop",
        )
        daemon._manifest_store.save(manifest)

        daemon._spawner.spawn = AsyncMock()
        await daemon._on_agent_crashed("agent-stop", 1, "error")

        daemon._spawner.spawn.assert_not_called()

        # Should still have reported state
        report_frames = [
            f for f in sent_frames if f["type"] == "report_actual_state"
        ]
        assert len(report_frames) >= 1

    async def test_normal_stop_reports_state(self, daemon: MachineDaemon) -> None:
        """Normal agent stop should report state."""
        sent_frames = _capture_ws(daemon)

        daemon._running_generations["agent-done"] = 1
        await daemon._on_agent_stopped("agent-done", 0)

        assert "agent-done" not in daemon._running_generations

        report_frames = [
            f for f in sent_frames if f["type"] == "report_actual_state"
        ]
        assert len(report_frames) >= 1


# ── Ping ─────────────────────────────────────────────────────────────


class TestPing:
    """Tests for ping handling."""

    async def test_ping_triggers_report(self, daemon: MachineDaemon) -> None:
        """Ping should respond with report_actual_state."""
        sent_frames = _capture_ws(daemon)

        ping_data = {"type": "ping"}
        await daemon._handle(ping_data)

        assert len(sent_frames) == 1
        assert sent_frames[0]["type"] == "report_actual_state"


# ── Rotate token ─────────────────────────────────────────────────────


class TestRotateToken:
    """Tests for rotate_token frame handling."""

    async def test_handle_rotate_token_persists_and_updates(
        self, daemon: MachineDaemon, tmp_path
    ) -> None:
        """rotate_token should write the new token to disk and update memory."""
        token_file = tmp_path / "machine.token"
        daemon._token_path = token_file

        rotate_data = {
            "type": "rotate_token",
            "new_token": "mch_new_token_xyz",
        }
        await daemon._handle(rotate_data)

        assert daemon.machine_token == "mch_new_token_xyz"
        assert token_file.exists()
        assert token_file.read_text().strip() == "mch_new_token_xyz"
        # File must be chmod 600
        import stat
        mode = token_file.stat().st_mode & 0o777
        assert mode == 0o600

    async def test_handle_rotate_token_save_failure_keeps_old_token(
        self, daemon: MachineDaemon
    ) -> None:
        """If save_token fails, the in-memory token must NOT be updated."""
        daemon._token_path = Path("/this/path/does/not/exist/machine.token")
        daemon.machine_token = "original_token"

        rotate_data = {
            "type": "rotate_token",
            "new_token": "mch_new_token_xyz",
        }
        await daemon._handle(rotate_data)

        # In-memory token should still be the original
        assert daemon.machine_token == "original_token"


# ── Reconnection ─────────────────────────────────────────────────────


class TestReconnection:
    """Tests for WebSocket reconnection behavior."""

    async def test_reconnect_on_disconnect(self, daemon: MachineDaemon) -> None:
        """Daemon should attempt reconnection after disconnect."""
        connect_count = 0

        async def mock_connect_and_serve():
            nonlocal connect_count
            connect_count += 1
            if connect_count < 3:
                raise OSError("Connection refused")
            # On 3rd attempt, cancel to stop the loop
            raise asyncio.CancelledError()

        daemon._connect_and_serve = mock_connect_and_serve

        with patch("doorae_machine.daemon.asyncio.sleep", new_callable=AsyncMock):
            # CancelledError is caught inside run() which drains and returns cleanly
            await daemon.run()

        # Should have attempted to connect 3 times (2 OSError + 1 CancelledError)
        assert connect_count == 3
