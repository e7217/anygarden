"""Tests for engine lifecycle WS frames (#553)."""

from __future__ import annotations

import pytest
from anygarden_machine.protocol.frames import (
    EngineCheckFrame,
    EngineCheckResultFrame,
    EngineUpdateFrame,
    EngineUpdateResultFrame,
    parse_server_frame,
)
from pydantic import ValidationError


def test_parse_engine_check():
    frame = parse_server_frame({"type": "engine_check", "engine": "codex-cli"})
    assert isinstance(frame, EngineCheckFrame)
    assert frame.engine == "codex-cli"


def test_parse_engine_update():
    frame = parse_server_frame({"type": "engine_update", "engine": "gemini-cli"})
    assert isinstance(frame, EngineUpdateFrame)
    assert frame.engine == "gemini-cli"


def test_check_result_roundtrip():
    frame = EngineCheckResultFrame(
        engine="codex-cli", current_version="0.1.0", latest_version="0.2.0"
    )
    dumped = frame.model_dump()
    assert dumped["type"] == "engine_check_result"
    again = EngineCheckResultFrame.model_validate(dumped)
    assert again.current_version == "0.1.0"
    assert again.latest_version == "0.2.0"


def test_update_result_accepts_lifecycle_statuses():
    for status in ("updating", "success", "failed"):
        frame = EngineUpdateResultFrame(engine="x", status=status)
        assert frame.status == status


def test_update_result_rejects_bad_status():
    with pytest.raises(ValidationError):
        EngineUpdateResultFrame(engine="x", status="bogus")
