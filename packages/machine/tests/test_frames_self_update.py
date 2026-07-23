"""Tests for the self_update protocol frames (#550)."""

from __future__ import annotations

import pytest

from anygarden_machine.protocol.frames import (
    SelfUpdateFrame,
    SelfUpdateResultFrame,
    parse_server_frame,
)


def test_parse_self_update_frame_default_latest() -> None:
    frame = parse_server_frame({"type": "self_update"})
    assert isinstance(frame, SelfUpdateFrame)
    assert frame.target_version is None


def test_parse_self_update_frame_with_target() -> None:
    frame = parse_server_frame({"type": "self_update", "target_version": "0.13.0"})
    assert isinstance(frame, SelfUpdateFrame)
    assert frame.target_version == "0.13.0"


def test_self_update_result_frame_roundtrip() -> None:
    frame = SelfUpdateResultFrame(
        status="failed",
        from_version="0.12.0",
        to_version=None,
        error="pip exited 1",
    )
    dumped = frame.model_dump()
    assert dumped["type"] == "self_update_result"
    assert dumped["status"] == "failed"
    assert dumped["from_version"] == "0.12.0"
    assert dumped["error"] == "pip exited 1"


def test_self_update_result_status_validated() -> None:
    with pytest.raises(Exception):
        SelfUpdateResultFrame(status="bogus", from_version="0.12.0", to_version=None, error=None)
