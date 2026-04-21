"""Tests for the declarative protocol frame models (Task 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from doorae_machine.protocol.frames import (
    AgentActual,
    ReportActualStateFrame,
    RequestReplacementFrame,
    SyncBatchFrame,
    SyncDesiredStateFrame,
    TokenGrantFrame,
    TokenRequestFrame,
    parse_server_frame,
)


# ── SyncDesiredStateFrame ─────────────────────────────────────────────


class TestSyncDesiredStateFrame:
    def test_running_with_full_payload(self):
        frame = SyncDesiredStateFrame(
            agent_id="agent-123",
            desired_state="running",
            generation=5,
            engine="claude",
            name="My Agent",
            profile_yaml="model: claude-3-5-sonnet",
            rooms=["room-a", "room-b"],
            agents_md="# Agents",
            files={"config.yaml": "key: value"},
            engine_secrets={"OPENAI_API_KEY": "sk-xxx"},
            reasoning_effort="high",
            sub_rooms=[{"name": "sub1", "description": "Sub room 1"}],
            restart_policy="restart_on_same_machine",
            max_restarts=5,
            restart_window_seconds=600,
        )
        assert frame.type == "sync_desired_state"
        assert frame.agent_id == "agent-123"
        assert frame.desired_state == "running"
        assert frame.generation == 5
        assert frame.engine == "claude"
        assert frame.name == "My Agent"
        assert frame.rooms == ["room-a", "room-b"]
        assert frame.agents_md == "# Agents"
        assert frame.files == {"config.yaml": "key: value"}
        assert frame.engine_secrets == {"OPENAI_API_KEY": "sk-xxx"}
        assert frame.reasoning_effort == "high"
        assert frame.sub_rooms == [{"name": "sub1", "description": "Sub room 1"}]
        assert frame.restart_policy == "restart_on_same_machine"
        assert frame.max_restarts == 5
        assert frame.restart_window_seconds == 600

    def test_stopped_minimal(self):
        frame = SyncDesiredStateFrame(
            agent_id="agent-456",
            desired_state="stopped",
            generation=1,
        )
        assert frame.type == "sync_desired_state"
        assert frame.agent_id == "agent-456"
        assert frame.desired_state == "stopped"
        assert frame.generation == 1

    def test_defaults(self):
        frame = SyncDesiredStateFrame(
            agent_id="agent-789",
            desired_state="running",
            generation=2,
        )
        assert frame.engine == ""
        assert frame.name == ""
        assert frame.profile_yaml == ""
        assert frame.rooms == []
        assert frame.agents_md is None
        assert frame.files == {}
        assert frame.engine_secrets == {}
        assert frame.reasoning_effort is None
        assert frame.sub_rooms == []
        assert frame.restart_policy == "restart_anywhere"
        assert frame.max_restarts == 3
        assert frame.restart_window_seconds == 300

    def test_type_literal_is_fixed(self):
        frame = SyncDesiredStateFrame(
            agent_id="agent-x",
            desired_state="running",
            generation=0,
        )
        assert frame.type == "sync_desired_state"

    def test_invalid_desired_state_raises(self):
        with pytest.raises(ValidationError):
            SyncDesiredStateFrame(
                agent_id="agent-x",
                desired_state="invalid_state",
                generation=1,
            )

    def test_invalid_restart_policy_raises(self):
        with pytest.raises(ValidationError):
            SyncDesiredStateFrame(
                agent_id="agent-x",
                desired_state="running",
                generation=1,
                restart_policy="never",
            )

    def test_serialization_roundtrip(self):
        frame = SyncDesiredStateFrame(
            agent_id="agent-rt",
            desired_state="running",
            generation=3,
            engine="gpt-4o",
        )
        data = frame.model_dump()
        restored = SyncDesiredStateFrame.model_validate(data)
        assert restored == frame


# ── SyncBatchFrame ────────────────────────────────────────────────────


class TestSyncBatchFrame:
    def test_multiple_agents(self):
        agents = [
            SyncDesiredStateFrame(
                agent_id=f"agent-{i}",
                desired_state="running",
                generation=i,
            )
            for i in range(3)
        ]
        batch = SyncBatchFrame(agents=agents)
        assert batch.type == "sync_batch"
        assert len(batch.agents) == 3
        assert batch.agents[0].agent_id == "agent-0"
        assert batch.agents[2].generation == 2

    def test_empty_batch(self):
        batch = SyncBatchFrame()
        assert batch.type == "sync_batch"
        assert batch.agents == []

    def test_empty_batch_explicit(self):
        batch = SyncBatchFrame(agents=[])
        assert batch.type == "sync_batch"
        assert len(batch.agents) == 0

    def test_serialization_roundtrip(self):
        batch = SyncBatchFrame(
            agents=[
                SyncDesiredStateFrame(
                    agent_id="agent-a",
                    desired_state="stopped",
                    generation=10,
                )
            ]
        )
        data = batch.model_dump()
        restored = SyncBatchFrame.model_validate(data)
        assert restored == batch

    # ── is_full_snapshot (#185) ─────────────────────────────────────

    def test_is_full_snapshot_defaults_true_for_backward_compat(self):
        """Pre-#185 servers don't emit the flag. The machine must treat
        those frames as full snapshots (the historical behaviour) so
        the upgrade is backwards-compatible: roll out the flag-aware
        machine first, then the flag-aware server, in either order.
        """
        batch = SyncBatchFrame(agents=[])
        assert batch.is_full_snapshot is True

    def test_is_full_snapshot_explicit_false(self):
        batch = SyncBatchFrame(agents=[], is_full_snapshot=False)
        assert batch.is_full_snapshot is False

    def test_is_full_snapshot_parses_from_dict_without_flag(self):
        """``parse_server_frame`` on a dict missing ``is_full_snapshot``
        must return a frame with the default True — guarantees mixed-
        version clusters never silently flip to partial-snapshot mode.
        """
        raw = {"type": "sync_batch", "agents": []}
        frame = parse_server_frame(raw)
        assert isinstance(frame, SyncBatchFrame)
        assert frame.is_full_snapshot is True

    def test_is_full_snapshot_roundtrip(self):
        batch = SyncBatchFrame(agents=[], is_full_snapshot=False)
        data = batch.model_dump()
        assert data["is_full_snapshot"] is False
        restored = SyncBatchFrame.model_validate(data)
        assert restored.is_full_snapshot is False


# ── TokenGrantFrame ───────────────────────────────────────────────────


class TestTokenGrantFrame:
    def test_basic(self):
        frame = TokenGrantFrame(agent_id="agent-abc", agent_token="tok-xyz")
        assert frame.type == "token_grant"
        assert frame.agent_id == "agent-abc"
        assert frame.agent_token == "tok-xyz"

    def test_serialization(self):
        frame = TokenGrantFrame(agent_id="a1", agent_token="t1")
        data = frame.model_dump()
        assert data["type"] == "token_grant"
        assert data["agent_id"] == "a1"
        assert data["agent_token"] == "t1"

    def test_roundtrip(self):
        frame = TokenGrantFrame(agent_id="a2", agent_token="t2")
        restored = TokenGrantFrame.model_validate(frame.model_dump())
        assert restored == frame


# ── AgentActual ───────────────────────────────────────────────────────


class TestAgentActual:
    def test_running_with_all_fields(self):
        agent = AgentActual(
            agent_id="agent-r1",
            actual_state="running",
            pid=12345,
            engine="claude",
            generation=7,
            uptime_seconds=3600,
        )
        assert agent.agent_id == "agent-r1"
        assert agent.actual_state == "running"
        assert agent.pid == 12345
        assert agent.engine == "claude"
        assert agent.generation == 7
        assert agent.uptime_seconds == 3600
        assert agent.last_crash_reason is None

    def test_crashed_with_reason(self):
        agent = AgentActual(
            agent_id="agent-c1",
            actual_state="crashed",
            last_crash_reason="OOM killed",
        )
        assert agent.actual_state == "crashed"
        assert agent.last_crash_reason == "OOM killed"
        assert agent.pid is None

    def test_defaults(self):
        agent = AgentActual(agent_id="agent-d1", actual_state="stopped")
        assert agent.pid is None
        assert agent.engine == ""
        assert agent.generation == 0
        assert agent.uptime_seconds == 0
        assert agent.last_crash_reason is None

    def test_starting_state(self):
        agent = AgentActual(agent_id="agent-s1", actual_state="starting")
        assert agent.actual_state == "starting"

    def test_stopping_state(self):
        # #219 — transitional state emitted by the daemon between a
        # kill dispatch and ``_on_agent_stopped`` so admins see a
        # "stopping" badge instead of the 30s gap where the prior
        # actual_state="running" lingers.
        agent = AgentActual(agent_id="agent-sp1", actual_state="stopping")
        assert agent.actual_state == "stopping"

    def test_invalid_state_raises(self):
        with pytest.raises(ValidationError):
            AgentActual(agent_id="agent-x", actual_state="unknown_state")


# ── ReportActualStateFrame ────────────────────────────────────────────


class TestReportActualStateFrame:
    def test_with_agents(self):
        agents = [
            AgentActual(agent_id="a1", actual_state="running", pid=100),
            AgentActual(agent_id="a2", actual_state="stopped"),
            AgentActual(agent_id="a3", actual_state="crashed", last_crash_reason="segfault"),
        ]
        frame = ReportActualStateFrame(agents=agents)
        assert frame.type == "report_actual_state"
        assert len(frame.agents) == 3
        assert frame.agents[0].pid == 100
        assert frame.agents[2].last_crash_reason == "segfault"

    def test_empty(self):
        frame = ReportActualStateFrame()
        assert frame.type == "report_actual_state"
        assert frame.agents == []

    def test_empty_explicit(self):
        frame = ReportActualStateFrame(agents=[])
        assert frame.type == "report_actual_state"
        assert len(frame.agents) == 0

    def test_serialization_roundtrip(self):
        frame = ReportActualStateFrame(
            agents=[AgentActual(agent_id="a1", actual_state="running", pid=999)]
        )
        data = frame.model_dump()
        restored = ReportActualStateFrame.model_validate(data)
        assert restored == frame


# ── TokenRequestFrame ─────────────────────────────────────────────────


class TestTokenRequestFrame:
    def test_basic(self):
        frame = TokenRequestFrame(agent_ids=["agent-1", "agent-2"])
        assert frame.type == "token_request"
        assert frame.agent_ids == ["agent-1", "agent-2"]

    def test_empty_default(self):
        frame = TokenRequestFrame()
        assert frame.type == "token_request"
        assert frame.agent_ids == []

    def test_single_agent(self):
        frame = TokenRequestFrame(agent_ids=["only-agent"])
        assert len(frame.agent_ids) == 1

    def test_roundtrip(self):
        frame = TokenRequestFrame(agent_ids=["a", "b", "c"])
        restored = TokenRequestFrame.model_validate(frame.model_dump())
        assert restored == frame


# ── RequestReplacementFrame ───────────────────────────────────────────


class TestRequestReplacementFrame:
    def test_basic(self):
        frame = RequestReplacementFrame(agent_id="agent-x", reason="OOM killed repeatedly")
        assert frame.type == "request_replacement"
        assert frame.agent_id == "agent-x"
        assert frame.reason == "OOM killed repeatedly"

    def test_default_reason(self):
        frame = RequestReplacementFrame(agent_id="agent-y")
        assert frame.reason == ""

    def test_roundtrip(self):
        frame = RequestReplacementFrame(agent_id="agent-z", reason="crash loop")
        restored = RequestReplacementFrame.model_validate(frame.model_dump())
        assert restored == frame


# ── parse_server_frame ────────────────────────────────────────────────


class TestParseServerFrame:
    def test_sync_desired_state(self):
        data = {
            "type": "sync_desired_state",
            "agent_id": "agent-p1",
            "desired_state": "running",
            "generation": 1,
        }
        frame = parse_server_frame(data)
        assert isinstance(frame, SyncDesiredStateFrame)
        assert frame.agent_id == "agent-p1"

    def test_sync_batch(self):
        data = {
            "type": "sync_batch",
            "agents": [
                {
                    "type": "sync_desired_state",
                    "agent_id": "agent-b1",
                    "desired_state": "stopped",
                    "generation": 2,
                }
            ],
        }
        frame = parse_server_frame(data)
        assert isinstance(frame, SyncBatchFrame)
        assert len(frame.agents) == 1
        assert frame.agents[0].agent_id == "agent-b1"

    def test_token_grant(self):
        data = {
            "type": "token_grant",
            "agent_id": "agent-tg1",
            "agent_token": "tok-abc",
        }
        frame = parse_server_frame(data)
        assert isinstance(frame, TokenGrantFrame)
        assert frame.agent_token == "tok-abc"

    def test_drain(self):
        from doorae_machine.protocol.frames import DrainFrame

        data = {"type": "drain"}
        frame = parse_server_frame(data)
        assert isinstance(frame, DrainFrame)

    def test_ping(self):
        from doorae_machine.protocol.frames import PingFrame

        data = {"type": "ping"}
        frame = parse_server_frame(data)
        assert isinstance(frame, PingFrame)

    def test_rotate_token(self):
        from doorae_machine.protocol.frames import RotateTokenFrame

        data = {"type": "rotate_token", "new_token": "new-tok-xyz"}
        frame = parse_server_frame(data)
        assert isinstance(frame, RotateTokenFrame)
        assert frame.new_token == "new-tok-xyz"

    def test_old_spawn_agent_raises_value_error(self):
        data = {
            "type": "spawn_agent",
            "agent_id": "agent-old",
            "engine": "claude",
            "agent_token": "tok-old",
            "profile_yaml": "model: claude",
            "server_url": "http://localhost:8000",
        }
        with pytest.raises(ValueError, match="Unknown server frame type"):
            parse_server_frame(data)

    def test_old_kill_agent_raises_value_error(self):
        data = {"type": "kill_agent", "agent_id": "agent-old"}
        with pytest.raises(ValueError, match="Unknown server frame type"):
            parse_server_frame(data)

    def test_unknown_type_raises_value_error(self):
        data = {"type": "completely_unknown", "foo": "bar"}
        with pytest.raises(ValueError, match="Unknown server frame type"):
            parse_server_frame(data)

    def test_missing_type_raises_value_error(self):
        data = {"agent_id": "agent-no-type"}
        with pytest.raises(ValueError, match="Unknown server frame type"):
            parse_server_frame(data)
