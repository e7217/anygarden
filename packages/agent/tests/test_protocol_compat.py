"""Protocol compatibility tests — verify SDK frames match server frames."""

from __future__ import annotations

import pytest

from anygarden_agent.protocol import frames as sdk_frames


class TestProtocolCompat:
    def test_send_frame_fields(self) -> None:
        """SendFrame has the expected fields."""
        f = sdk_frames.SendFrame(content="test")
        assert f.type == "send"
        assert f.content == "test"
        assert f.metadata is None

    def test_message_out_fields(self) -> None:
        """MessageOut has the expected fields."""
        from datetime import datetime, timezone

        f = sdk_frames.MessageOut(
            room_id="r1",
            participant_id="p1",
            content="hello",
            seq=1,
            created_at=datetime.now(timezone.utc),
        )
        assert f.type == "message"
        assert f.seq == 1

    def test_parse_incoming_send(self) -> None:
        """parse_incoming correctly dispatches a send frame."""
        f = sdk_frames.parse_incoming({"type": "send", "content": "test"})
        assert isinstance(f, sdk_frames.SendFrame)

    def test_parse_incoming_typing(self) -> None:
        """parse_incoming correctly dispatches a typing frame."""
        f = sdk_frames.parse_incoming({"type": "typing", "is_typing": True})
        assert isinstance(f, sdk_frames.TypingFrame)

    def test_parse_incoming_unknown_raises(self) -> None:
        """parse_incoming raises ValueError for unknown types."""
        with pytest.raises(ValueError, match="Unknown frame type"):
            sdk_frames.parse_incoming({"type": "bogus"})

    def test_sdk_frames_source_matches_intent(self) -> None:
        """SDK frames module has all required frame classes."""
        expected_classes = [
            "SendFrame",
            "TypingFrame",
            "CreateRoomFrame",
            "JoinRoomFrame",
            "LifecycleFrame",
            "MessageOut",
            "RoomCreatedOut",
            "JoinRoomOut",
            "TypingOut",
            "ErrorOut",
        ]
        for name in expected_classes:
            assert hasattr(sdk_frames, name), f"Missing frame class: {name}"


class TestLifecycleFrame:
    def test_parse_handler_started(self) -> None:
        f = sdk_frames.parse_incoming({
            "type": "lifecycle",
            "request_id": "req-1",
            "room_id": "room-1",
            "event": "handler_started",
        })
        assert isinstance(f, sdk_frames.LifecycleFrame)
        assert f.event == "handler_started"
        assert f.request_id == "req-1"
        assert f.room_id == "room-1"

    def test_parse_engine_call_finished_with_outcome(self) -> None:
        f = sdk_frames.parse_incoming({
            "type": "lifecycle",
            "request_id": "req-2",
            "room_id": "room-1",
            "event": "engine_call_finished",
            "engine": "codex",
            "outcome": "ok",
            "duration_ms": 1234,
        })
        assert isinstance(f, sdk_frames.LifecycleFrame)
        assert f.outcome == "ok"
        assert f.duration_ms == 1234
        assert f.engine == "codex"

    def test_outcome_enum_accepts_all_designed_values(self) -> None:
        # #457 Wave 2b adds queued/retrying/retry_exhausted to the set.
        for outcome in (
            "ok",
            "failed",
            "timeout",
            "cancelled",
            "rejected",
            "queued",
            "retrying",
            "retry_exhausted",
        ):
            f = sdk_frames.LifecycleFrame(
                request_id="r",
                room_id="room-1",
                event="handler_finished",
                outcome=outcome,
            )
            assert f.outcome == outcome

    def test_new_outcomes_round_trip_agent_to_cluster(self) -> None:
        # #457 Wave 2b — parity guard: the cluster's mirror LifecycleFrame
        # must accept the three new outcomes identically, or a queued/
        # retrying/retry_exhausted frame would be rejected at the cluster.
        import json

        from anygarden.ws.protocol import LifecycleFrame as ClusterLifecycleFrame

        for outcome in ("queued", "retrying", "retry_exhausted"):
            agent_frame = sdk_frames.LifecycleFrame(
                request_id="r-new",
                room_id="room-1",
                event="handler_finished",
                outcome=outcome,
            )
            wire = json.loads(agent_frame.model_dump_json(exclude_none=True))
            cluster_frame = ClusterLifecycleFrame(**wire)
            assert cluster_frame.outcome == outcome

    def test_dump_excludes_none(self) -> None:
        f = sdk_frames.LifecycleFrame(
            request_id="req-3",
            room_id="room-1",
            event="handler_started",
        )
        payload = f.model_dump(exclude_none=True)
        assert "outcome" not in payload
        assert "duration_ms" not in payload
        assert "engine" not in payload
        assert "error" not in payload
        assert "prompt" not in payload
        assert "completion" not in payload
        assert payload["event"] == "handler_started"

    def test_engine_call_finished_carries_prompt_and_completion(self) -> None:
        # #433 — gateway-free turn I/O rides on engine_call_finished:
        # the augmented input the adapter handed the engine + the reply.
        f = sdk_frames.parse_incoming({
            "type": "lifecycle",
            "request_id": "req-4",
            "room_id": "room-1",
            "event": "engine_call_finished",
            "engine": "codex",
            "outcome": "ok",
            "duration_ms": 10,
            "prompt": "augmented user turn",
            "completion": "the engine reply",
        })
        assert isinstance(f, sdk_frames.LifecycleFrame)
        assert f.prompt == "augmented user turn"
        assert f.completion == "the engine reply"
        # round-trips on the wire dump
        payload = f.model_dump(exclude_none=True)
        assert payload["prompt"] == "augmented user turn"
        assert payload["completion"] == "the engine reply"

    def test_engine_call_finished_carries_usage(self) -> None:
        # #461 (Wave 2d) — gateway-free LLM usage rides on
        # engine_call_finished: model + input/output tokens + cost_usd.
        f = sdk_frames.parse_incoming({
            "type": "lifecycle",
            "request_id": "req-u",
            "room_id": "room-1",
            "event": "engine_call_finished",
            "engine": "claude-code",
            "outcome": "ok",
            "duration_ms": 42,
            "model": "claude-sonnet-4-5",
            "input_tokens": 1200,
            "output_tokens": 350,
            "cost_usd": 0.0123,
        })
        assert isinstance(f, sdk_frames.LifecycleFrame)
        assert f.model == "claude-sonnet-4-5"
        assert f.input_tokens == 1200
        assert f.output_tokens == 350
        assert f.cost_usd == 0.0123
        payload = f.model_dump(exclude_none=True)
        assert payload["input_tokens"] == 1200
        assert payload["output_tokens"] == 350
        assert payload["cost_usd"] == 0.0123
        assert payload["model"] == "claude-sonnet-4-5"

    def test_usage_fields_default_none_and_excluded(self) -> None:
        # #461 — a frame without usage omits all four fields on the wire,
        # so a bare-str engine return / openhands writes no usage row.
        f = sdk_frames.LifecycleFrame(
            request_id="req-n",
            room_id="room-1",
            event="engine_call_finished",
            outcome="ok",
        )
        assert f.model is None
        assert f.input_tokens is None
        assert f.output_tokens is None
        assert f.cost_usd is None
        payload = f.model_dump(exclude_none=True)
        for k in ("model", "input_tokens", "output_tokens", "cost_usd"):
            assert k not in payload

    def test_usage_wire_round_trip_agent_to_cluster(self) -> None:
        # #461 — cross-package parity: the cluster's mirror LifecycleFrame
        # reconstructs the four usage fields field-identical, or the WS
        # handler couldn't read them off the wire (test_protocol_compat
        # parity guard for Wave 2d).
        import json

        from anygarden.ws.protocol import LifecycleFrame as ClusterLifecycleFrame

        agent_frame = sdk_frames.LifecycleFrame(
            request_id="req-uw",
            room_id="room-1",
            event="engine_call_finished",
            engine="claude-code",
            outcome="ok",
            duration_ms=7,
            model="claude-sonnet-4-5",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.5,
        )
        wire = json.loads(agent_frame.model_dump_json(exclude_none=True))
        cluster_frame = ClusterLifecycleFrame(**wire)
        assert cluster_frame.model == "claude-sonnet-4-5"
        assert cluster_frame.input_tokens == 10
        assert cluster_frame.output_tokens == 5
        assert cluster_frame.cost_usd == 0.5

    def test_lifecycle_frame_field_parity_agent_cluster(self) -> None:
        # #461 — strong parity: both LifecycleFrame definitions must expose
        # the exact same field set (names) so the two stay in lockstep.
        from anygarden.ws.protocol import LifecycleFrame as ClusterLifecycleFrame

        assert (
            set(sdk_frames.LifecycleFrame.model_fields)
            == set(ClusterLifecycleFrame.model_fields)
        )

    def test_turn_io_wire_round_trip_agent_to_cluster(self) -> None:
        # #433 — a true cross-package wire round-trip: the agent dumps a
        # frame with prompt/completion and the CLUSTER's LifecycleFrame
        # reconstructs it field-identical (the two definitions must stay
        # compatible — same names/types/defaults).
        import json

        from anygarden.ws.protocol import LifecycleFrame as ClusterLifecycleFrame

        agent_frame = sdk_frames.LifecycleFrame(
            request_id="req-w",
            room_id="room-1",
            event="engine_call_finished",
            engine="codex",
            outcome="ok",
            duration_ms=5,
            prompt="the input",
            completion="the reply",
        )
        wire = json.loads(agent_frame.model_dump_json(exclude_none=True))
        cluster_frame = ClusterLifecycleFrame(**wire)
        assert cluster_frame.prompt == "the input"
        assert cluster_frame.completion == "the reply"
        assert cluster_frame.event == "engine_call_finished"
        assert cluster_frame.engine == "codex"
