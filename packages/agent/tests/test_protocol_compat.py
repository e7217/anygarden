"""Protocol compatibility tests — verify SDK frames match server frames."""

from __future__ import annotations

import hashlib
import inspect

import pytest

from doorae_agent.protocol import frames as sdk_frames


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
            "MessageOut",
            "RoomCreatedOut",
            "JoinRoomOut",
            "TypingOut",
            "ErrorOut",
        ]
        for name in expected_classes:
            assert hasattr(sdk_frames, name), f"Missing frame class: {name}"
