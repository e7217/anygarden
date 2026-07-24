"""Tests for engine check/update daemon handlers (#553)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from anygarden_machine.daemon import MachineDaemon
from anygarden_machine.detector import DetectionResult, EngineInfo
from anygarden_machine.engines.updater import EngineUpdateResult
from anygarden_machine.protocol.frames import EngineCheckFrame, EngineUpdateFrame


@pytest.fixture
def daemon(tmp_path: Path) -> MachineDaemon:
    return MachineDaemon(
        server_url="wss://localhost:8000/ws/machines/machine-test-001",
        machine_id="machine-test-001",
        machine_token="test-machine-token",
        labels={"region": "local"},
        agent_dirs_root=tmp_path / "agents",
    )


def _capture_ws(daemon: MachineDaemon) -> list[dict]:
    """Wire up a mock WS that captures sent frames (mirrors test_daemon)."""
    sent: list[dict] = []
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=lambda data: sent.append(json.loads(data)))
    daemon._ws = mock_ws
    return sent


class TestEngineCheckHandler:
    async def test_reports_current_and_latest(self, daemon: MachineDaemon):
        sent = _capture_ws(daemon)
        det = DetectionResult(
            engines=[EngineInfo(engine="codex-cli", version="codex 0.1.0", path="/x")]
        )
        with (
            patch(
                "anygarden_machine.daemon.detect_engines",
                AsyncMock(return_value=det),
            ),
            patch(
                "anygarden_machine.engines.channels.NpmGlobal.latest_version",
                AsyncMock(return_value="0.2.0"),
            ),
        ):
            await daemon._handle_engine_check(EngineCheckFrame(engine="codex-cli"))

        assert len(sent) == 1
        assert sent[0]["type"] == "engine_check_result"
        # current is normalized from the raw "codex 0.1.0"
        assert sent[0]["current_version"] == "0.1.0"
        assert sent[0]["latest_version"] == "0.2.0"

    async def test_unknown_engine_rejected(self, daemon: MachineDaemon):
        sent = _capture_ws(daemon)
        await daemon._handle_engine_check(EngineCheckFrame(engine="nope"))
        assert sent[0]["error"]
        assert "unknown engine" in sent[0]["error"]

    async def test_current_none_when_not_installed(self, daemon: MachineDaemon):
        sent = _capture_ws(daemon)
        det = DetectionResult(engines=[])  # codex not present on this machine
        with (
            patch(
                "anygarden_machine.daemon.detect_engines",
                AsyncMock(return_value=det),
            ),
            patch(
                "anygarden_machine.engines.channels.NpmGlobal.latest_version",
                AsyncMock(return_value="0.2.0"),
            ),
        ):
            await daemon._handle_engine_check(EngineCheckFrame(engine="codex-cli"))

        assert sent[0]["current_version"] is None
        assert sent[0]["latest_version"] == "0.2.0"


class TestEngineUpdateHandler:
    async def test_updating_then_success(self, daemon: MachineDaemon):
        sent = _capture_ws(daemon)
        with patch(
            "anygarden_machine.daemon.run_engine_update",
            return_value=EngineUpdateResult(True, "codex-cli", None),
        ):
            await daemon._handle_engine_update(EngineUpdateFrame(engine="codex-cli"))

        assert [f["status"] for f in sent] == ["updating", "success"]
        assert all(f["type"] == "engine_update_result" for f in sent)

    async def test_reports_failed_with_error(self, daemon: MachineDaemon):
        sent = _capture_ws(daemon)
        with patch(
            "anygarden_machine.daemon.run_engine_update",
            return_value=EngineUpdateResult(False, "codex-cli", "boom"),
        ):
            await daemon._handle_engine_update(EngineUpdateFrame(engine="codex-cli"))

        assert sent[-1]["status"] == "failed"
        assert sent[-1]["error"] == "boom"
